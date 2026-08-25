"""Unit tests for graceful-degradation-to-polling-fallback.

Every elapsed-time assertion runs against an injected clock rather than
``time.sleep``, so the suite is deterministic and fast.

Several tests are regressions against defects the earlier implementation had,
and are marked ``REGRESSION`` in their docstring: identity-aware deduplication
of same-instant trades, stabilisation requiring *recent* consecutive ticks,
heartbeat-based liveness on a quiet instrument, the polling throttle, and the
blind escalation. Each of them fails against the previous behaviour.
"""
import threading
import time
import unittest

from feed_fallback_manager import (
    FeedFallbackManager,
    FeedMode,
    FeedStatus,
    TickPayload,
)


class FakeClock:
    """Deterministic stand-in for ``time.monotonic``."""

    def __init__(self, start: float = 1_000.0) -> None:
        self.now = start

    def __call__(self) -> float:
        return self.now

    def advance(self, seconds: float) -> None:
        self.now += seconds


def ws_tick(timestamp: float, price: float = 3_000.0, identity=None, symbol="ETHUSD"):
    return TickPayload(
        symbol=symbol, price=price, volume=1.0, timestamp=timestamp, identity=identity
    )


class BaseCase(unittest.TestCase):
    def setUp(self):
        self.clock = FakeClock()
        self.mgr = FeedFallbackManager(
            symbol="ETHUSD",
            silence_timeout_seconds=3.0,
            required_stabilization_ticks=3,
            min_poll_interval_seconds=1.0,
            max_consecutive_poll_failures=3,
            monotonic_clock=self.clock,
        )


class TestConstructorValidation(unittest.TestCase):
    def test_rejects_invalid_configuration(self):
        bad = [
            {"symbol": ""},
            {"symbol": "  "},
            {"silence_timeout_seconds": 0.0},
            {"silence_timeout_seconds": -1.0},
            {"silence_timeout_seconds": float("nan")},
            {"silence_timeout_seconds": float("inf")},
            {"min_poll_interval_seconds": 0.0},
            {"min_poll_interval_seconds": -0.5},
            {"required_stabilization_ticks": 0},
            {"required_stabilization_ticks": -1},
            {"required_stabilization_ticks": 2.5},
            {"max_consecutive_poll_failures": 0},
        ]
        for override in bad:
            kwargs = {"symbol": "ETHUSD"}
            kwargs.update(override)
            with self.subTest(override=override):
                with self.assertRaises(ValueError):
                    FeedFallbackManager(**kwargs)

    def test_zero_stabilization_ticks_would_recover_instantly(self):
        """A 0 threshold means the first straggler tick declares recovery."""
        with self.assertRaises(ValueError):
            FeedFallbackManager(symbol="ETHUSD", required_stabilization_ticks=0)

    def test_silence_timeout_must_exceed_twice_the_heartbeat(self):
        # Binance spot streams ping every 20s: a 3s silence window would degrade
        # constantly on a symbol that simply is not trading.
        with self.assertRaises(ValueError):
            FeedFallbackManager(
                symbol="BTCUSDT",
                silence_timeout_seconds=3.0,
                heartbeat_interval_seconds=20.0,
            )
        ok = FeedFallbackManager(
            symbol="BTCUSDT",
            silence_timeout_seconds=45.0,
            heartbeat_interval_seconds=20.0,
        )
        self.assertEqual(ok.silence_timeout_seconds, 45.0)

    def test_default_clock_is_monotonic(self):
        """Elapsed time must not be measured on a clock an NTP step can move."""
        mgr = FeedFallbackManager(symbol="ETHUSD")
        self.assertIs(mgr._clock, time.monotonic)


class TestWebSocketIngestion(BaseCase):
    def test_healthy_tick_is_returned_and_advances_watermark(self):
        result = self.mgr.ingest_websocket_tick(ws_tick(100.0))
        self.assertIsNotNone(result)
        self.assertEqual(result.price, 3_000.0)
        self.assertEqual(self.mgr.feed_mode, FeedMode.HEALTHY_WEBSOCKET)
        self.assertEqual(self.mgr.last_processed_timestamp, 100.0)

    def test_timestamp_behind_watermark_is_dropped_and_counted(self):
        self.mgr.ingest_websocket_tick(ws_tick(100.0))
        self.assertIsNone(self.mgr.ingest_websocket_tick(ws_tick(99.5)))
        status = self.mgr.get_status()
        self.assertEqual(status.stale_tick_count, 1)
        self.assertEqual(status.last_processed_timestamp, 100.0)

    def test_same_timestamp_without_identity_is_treated_as_duplicate(self):
        self.mgr.ingest_websocket_tick(ws_tick(100.0))
        self.assertIsNone(self.mgr.ingest_websocket_tick(ws_tick(100.0)))
        self.assertEqual(self.mgr.get_status().duplicate_tick_count, 1)

    def test_distinct_trades_sharing_a_timestamp_are_both_kept(self):
        """REGRESSION: Kite's last_trade_time has one-second resolution, so a
        strict ``ts > watermark`` test discards every trade after the first in
        each second. With an identity, both must survive."""
        first = self.mgr.ingest_websocket_tick(ws_tick(100.0, price=3_000.0, identity="t1"))
        second = self.mgr.ingest_websocket_tick(ws_tick(100.0, price=3_001.0, identity="t2"))
        self.assertIsNotNone(first)
        self.assertIsNotNone(second)
        self.assertEqual(second.price, 3_001.0)
        self.assertEqual(self.mgr.get_status().duplicate_tick_count, 0)

    def test_replayed_identity_at_same_timestamp_is_deduplicated(self):
        self.mgr.ingest_websocket_tick(ws_tick(100.0, identity="t1"))
        self.assertIsNone(self.mgr.ingest_websocket_tick(ws_tick(100.0, identity="t1")))
        self.assertEqual(self.mgr.get_status().duplicate_tick_count, 1)

    def test_identity_set_resets_when_the_watermark_advances(self):
        self.mgr.ingest_websocket_tick(ws_tick(100.0, identity="t1"))
        self.mgr.ingest_websocket_tick(ws_tick(101.0, identity="t2"))
        # "t1" is reusable at the new instant; it was only ever scoped to 100.0.
        self.assertIsNotNone(self.mgr.ingest_websocket_tick(ws_tick(101.0, identity="t1")))

    def test_foreign_symbol_raises_and_leaves_state_untouched(self):
        self.mgr.ingest_websocket_tick(ws_tick(100.0))
        with self.assertRaises(ValueError):
            self.mgr.ingest_websocket_tick(ws_tick(500.0, symbol="BTCUSD"))
        self.assertEqual(self.mgr.last_processed_timestamp, 100.0)

    def test_corrupt_values_are_rejected_without_advancing_the_watermark(self):
        self.mgr.ingest_websocket_tick(ws_tick(100.0))
        corrupt = [
            TickPayload("ETHUSD", float("nan"), 1.0, 101.0),
            TickPayload("ETHUSD", float("inf"), 1.0, 102.0),
            TickPayload("ETHUSD", 0.0, 1.0, 103.0),
            TickPayload("ETHUSD", -5.0, 1.0, 104.0),
            TickPayload("ETHUSD", 3_000.0, 1.0, float("nan")),
            TickPayload("ETHUSD", 3_000.0, 1.0, None),
            TickPayload("ETHUSD", 3_000.0, float("nan"), 105.0),
        ]
        for tick in corrupt:
            with self.subTest(price=tick.price, ts=tick.timestamp, vol=tick.volume):
                self.assertIsNone(self.mgr.ingest_websocket_tick(tick))
        self.assertEqual(self.mgr.last_processed_timestamp, 100.0)
        self.assertEqual(self.mgr.get_status().rejected_tick_count, len(corrupt))

    def test_deduplicated_tick_still_counts_as_liveness(self):
        """A repeated tick proves the socket is alive even though its payload
        is discarded, so it must not leave the feed looking silent."""
        self.mgr.ingest_websocket_tick(ws_tick(100.0))
        self.clock.advance(2.0)
        self.assertIsNone(self.mgr.ingest_websocket_tick(ws_tick(100.0)))
        self.clock.advance(2.0)
        self.assertEqual(self.mgr.check_feed_health(), FeedMode.HEALTHY_WEBSOCKET)


class TestSilenceDetection(BaseCase):
    def test_silence_beyond_threshold_degrades_to_polling(self):
        self.mgr.ingest_websocket_tick(ws_tick(100.0))
        self.clock.advance(3.5)
        self.assertEqual(self.mgr.check_feed_health(), FeedMode.DEGRADED_POLLING)

    def test_silence_exactly_at_threshold_does_not_degrade(self):
        self.mgr.ingest_websocket_tick(ws_tick(100.0))
        self.clock.advance(3.0)
        self.assertEqual(self.mgr.check_feed_health(), FeedMode.HEALTHY_WEBSOCKET)
        self.clock.advance(0.001)
        self.assertEqual(self.mgr.check_feed_health(), FeedMode.DEGRADED_POLLING)

    def test_heartbeat_keeps_a_quiet_instrument_healthy(self):
        """REGRESSION: degrading on trade silence alone pins an illiquid symbol
        into permanent polling. A venue heartbeat is liveness without a trade."""
        self.mgr.ingest_websocket_tick(ws_tick(100.0))
        for _ in range(10):
            self.clock.advance(2.0)
            self.mgr.on_websocket_heartbeat()
            self.assertEqual(self.mgr.check_feed_health(), FeedMode.HEALTHY_WEBSOCKET)
        # Heartbeats stop: now it really is dead.
        self.clock.advance(3.5)
        self.assertEqual(self.mgr.check_feed_health(), FeedMode.DEGRADED_POLLING)

    def test_degradation_records_the_gap_and_is_counted_once(self):
        self.mgr.ingest_websocket_tick(ws_tick(100.0))
        self.clock.advance(9.0)
        self.mgr.check_feed_health()
        self.clock.advance(5.0)
        self.mgr.check_feed_health()  # already degraded; must not re-record
        status = self.mgr.get_status()
        self.assertEqual(status.degradation_count, 1)
        self.assertAlmostEqual(status.last_degradation_gap_seconds, 9.0)

    def test_health_check_is_idempotent_while_degraded(self):
        self.clock.advance(10.0)
        for _ in range(5):
            self.assertEqual(self.mgr.check_feed_health(), FeedMode.DEGRADED_POLLING)
        self.assertEqual(self.mgr.get_status().degradation_count, 1)


class TestStabilizationHandback(BaseCase):
    def test_consecutive_recent_ticks_restore_websocket_mode(self):
        self.clock.advance(10.0)
        self.mgr.check_feed_health()
        self.assertEqual(self.mgr.feed_mode, FeedMode.DEGRADED_POLLING)

        for i in range(3):
            self.clock.advance(0.2)
            self.mgr.ingest_websocket_tick(ws_tick(200.0 + i))
        self.assertEqual(self.mgr.feed_mode, FeedMode.HEALTHY_WEBSOCKET)

    def test_partial_run_does_not_restore_websocket_mode(self):
        self.clock.advance(10.0)
        self.mgr.check_feed_health()
        for i in range(2):
            self.clock.advance(0.2)
            self.mgr.ingest_websocket_tick(ws_tick(200.0 + i))
        self.assertEqual(self.mgr.feed_mode, FeedMode.DEGRADED_POLLING)

    def test_stragglers_spread_across_gaps_never_stabilize(self):
        """REGRESSION: a plain counter lets ticks dribbling in every 10s from a
        still-broken feed accumulate into a false 'stabilised' verdict."""
        self.clock.advance(10.0)
        self.mgr.check_feed_health()
        for i in range(10):
            self.clock.advance(10.0)  # every gap exceeds the 3s silence window
            self.mgr.ingest_websocket_tick(ws_tick(200.0 + i))
            self.assertEqual(self.mgr.feed_mode, FeedMode.DEGRADED_POLLING)
            self.assertEqual(self.mgr.consecutive_ws_ticks, 1)

    def test_run_resets_midway_then_completes(self):
        self.clock.advance(10.0)
        self.mgr.check_feed_health()
        self.clock.advance(0.1)
        self.mgr.ingest_websocket_tick(ws_tick(200.0))
        self.clock.advance(0.1)
        self.mgr.ingest_websocket_tick(ws_tick(201.0))  # run of 2
        self.clock.advance(30.0)  # feed dies again; run must reset to 1
        self.mgr.ingest_websocket_tick(ws_tick(202.0))
        self.assertEqual(self.mgr.feed_mode, FeedMode.DEGRADED_POLLING)
        self.clock.advance(0.1)
        self.mgr.ingest_websocket_tick(ws_tick(203.0))
        self.clock.advance(0.1)
        self.mgr.ingest_websocket_tick(ws_tick(204.0))
        self.assertEqual(self.mgr.feed_mode, FeedMode.HEALTHY_WEBSOCKET)


class TestRestFallback(BaseCase):
    def setUp(self):
        super().setUp()
        self.calls = []

        def fetch(symbol, price=3_005.0, timestamp=101.0):
            self.calls.append(symbol)
            return TickPayload(symbol, price, 0.5, timestamp)

        self.fetch = fetch

    def _degrade(self):
        self.clock.advance(10.0)
        self.mgr.check_feed_health()

    def test_no_polling_while_the_stream_is_healthy(self):
        self.assertIsNone(self.mgr.poll_rest_fallback(self.fetch))
        self.assertEqual(self.calls, [])

    def test_polled_tick_is_tagged_and_deduplicated(self):
        self._degrade()
        tick = self.mgr.poll_rest_fallback(self.fetch)
        self.assertIsNotNone(tick)
        self.assertEqual(tick.source, "REST_POLLING")
        self.assertEqual(tick.price, 3_005.0)

        # Snapshot endpoints repeat the last trade until a new one prints.
        self.clock.advance(2.0)
        self.assertIsNone(self.mgr.poll_rest_fallback(self.fetch))
        self.assertEqual(len(self.calls), 2)

    def test_throttle_blocks_calls_inside_the_minimum_interval(self):
        """REGRESSION: an unthrottled 500ms poll is twice Kite's documented
        1 req/s quote limit, and Binance escalates repeat offenders to a
        multi-day IP ban."""
        self._degrade()
        self.mgr.poll_rest_fallback(self.fetch)
        for _ in range(20):
            self.clock.advance(0.01)
            self.assertIsNone(self.mgr.poll_rest_fallback(self.fetch))
        self.assertEqual(len(self.calls), 1)
        status = self.mgr.get_status()
        self.assertEqual(status.throttled_poll_count, 20)
        self.assertEqual(status.poll_failure_count, 0)
        self.assertEqual(status.poll_attempt_count, 1)

    def test_throttle_releases_once_the_interval_elapses(self):
        self._degrade()
        self.mgr.poll_rest_fallback(self.fetch)
        self.clock.advance(1.0)
        self.mgr.poll_rest_fallback(self.fetch)
        self.assertEqual(len(self.calls), 2)

    def test_throttled_calls_never_count_towards_blindness(self):
        self._degrade()
        for _ in range(50):
            self.clock.advance(0.001)
            self.mgr.poll_rest_fallback(self.fetch)
        self.assertFalse(self.mgr.is_blind())

    def test_foreign_symbol_from_the_fetcher_raises(self):
        self._degrade()

        def wrong_symbol(symbol):
            return TickPayload("BTCUSD", 60_000.0, 1.0, 500.0)

        with self.assertRaises(ValueError):
            self.mgr.poll_rest_fallback(wrong_symbol)

    def test_non_callable_fetcher_raises(self):
        with self.assertRaises(ValueError):
            self.mgr.poll_rest_fallback("not-callable")

    def test_rest_snapshot_ahead_of_buffered_ticks_records_data_loss(self):
        """The handover is not lossless: a snapshot at t=110 suppresses genuine
        buffered WebSocket prints from t=105..109, and that must be visible."""
        self._degrade()
        self.mgr.poll_rest_fallback(lambda s: TickPayload(s, 3_010.0, 1.0, 110.0))
        for ts in (105.0, 106.0, 107.0):
            self.assertIsNone(self.mgr.ingest_websocket_tick(ws_tick(ts)))
        self.assertEqual(self.mgr.get_status().stale_tick_count, 3)


class TestBlindEscalation(BaseCase):
    def _degrade(self):
        self.clock.advance(10.0)
        self.mgr.check_feed_health()

    def test_repeated_failures_declare_no_price_source(self):
        """REGRESSION: returning None forever is indistinguishable from a quiet
        market. A bot holding positions must be told it has no price source."""
        self._degrade()

        def failing(symbol):
            raise ConnectionError("HTTP 429 Too Many Requests")

        for _ in range(3):
            self.assertIsNone(self.mgr.poll_rest_fallback(failing))
            self.clock.advance(1.0)
        self.assertTrue(self.mgr.is_blind())
        self.assertEqual(self.mgr.get_status().feed_mode, FeedMode.BLIND_NO_DATA)

    def test_fetcher_exception_does_not_propagate(self):
        self._degrade()

        def exploding(symbol):
            raise RuntimeError("socket layer blew up")

        self.assertIsNone(self.mgr.poll_rest_fallback(exploding))
        self.assertEqual(self.mgr.get_status().poll_failure_count, 1)

    def test_empty_response_counts_as_a_failure(self):
        self._degrade()
        for _ in range(3):
            self.assertIsNone(self.mgr.poll_rest_fallback(lambda s: None))
            self.clock.advance(1.0)
        self.assertTrue(self.mgr.is_blind())

    def test_corrupt_quote_counts_as_a_failure(self):
        self._degrade()
        for _ in range(3):
            self.mgr.poll_rest_fallback(lambda s: TickPayload(s, float("nan"), 1.0, 120.0))
            self.clock.advance(1.0)
        self.assertTrue(self.mgr.is_blind())

    def test_successful_poll_clears_blindness_back_to_polling(self):
        self._degrade()
        for _ in range(3):
            self.mgr.poll_rest_fallback(lambda s: None)
            self.clock.advance(1.0)
        self.assertTrue(self.mgr.is_blind())

        recovered = self.mgr.poll_rest_fallback(
            lambda s: TickPayload(s, 3_020.0, 1.0, 130.0)
        )
        self.assertIsNotNone(recovered)
        self.assertFalse(self.mgr.is_blind())
        self.assertEqual(self.mgr.feed_mode, FeedMode.DEGRADED_POLLING)
        self.assertEqual(self.mgr.consecutive_poll_failures, 0)

    def test_websocket_recovery_clears_blindness_directly(self):
        self._degrade()
        for _ in range(3):
            self.mgr.poll_rest_fallback(lambda s: None)
            self.clock.advance(1.0)
        self.assertTrue(self.mgr.is_blind())

        for i in range(3):
            self.clock.advance(0.2)
            self.mgr.ingest_websocket_tick(ws_tick(300.0 + i))
        self.assertEqual(self.mgr.feed_mode, FeedMode.HEALTHY_WEBSOCKET)
        self.assertFalse(self.mgr.is_blind())


class TestConcurrency(unittest.TestCase):
    """The socket read thread, the polling worker and the health loop all touch
    the same state. An unguarded read-modify-write on the watermark lets a
    duplicate tick through, which is the exact failure this skill prevents."""

    def _yielding_clock(self):
        def clock():
            time.sleep(0)  # encourage the scheduler to interleave threads
            return 1_000.0

        return clock

    def test_concurrent_distinct_ticks_are_each_accepted_once(self):
        mgr = FeedFallbackManager(
            symbol="ETHUSD", monotonic_clock=self._yielding_clock()
        )
        accepted = []
        lock = threading.Lock()
        barrier = threading.Barrier(16)

        def worker(index):
            barrier.wait()
            result = mgr.ingest_websocket_tick(ws_tick(100.0, identity="id-%d" % index))
            if result is not None:
                with lock:
                    accepted.append(result.identity)

        threads = [threading.Thread(target=worker, args=(i,)) for i in range(16)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()

        self.assertEqual(len(accepted), 16)
        self.assertEqual(len(set(accepted)), 16)

    def test_concurrent_duplicates_are_accepted_exactly_once(self):
        mgr = FeedFallbackManager(
            symbol="ETHUSD", monotonic_clock=self._yielding_clock()
        )
        accepted = []
        lock = threading.Lock()
        barrier = threading.Barrier(16)

        def worker():
            barrier.wait()
            result = mgr.ingest_websocket_tick(ws_tick(100.0, identity="same-trade"))
            if result is not None:
                with lock:
                    accepted.append(result)

        threads = [threading.Thread(target=worker) for _ in range(16)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()

        self.assertEqual(len(accepted), 1)
        self.assertEqual(mgr.get_status().duplicate_tick_count, 15)

    def test_status_snapshot_is_internally_consistent_under_load(self):
        mgr = FeedFallbackManager(
            symbol="ETHUSD", monotonic_clock=self._yielding_clock()
        )
        stop = threading.Event()

        def producer():
            index = 0
            while not stop.is_set():
                mgr.ingest_websocket_tick(ws_tick(1_000.0 + index, identity=str(index)))
                index += 1

        thread = threading.Thread(target=producer)
        thread.start()
        try:
            for _ in range(200):
                status = mgr.get_status()
                self.assertIsInstance(status, FeedStatus)
                self.assertGreaterEqual(status.last_processed_timestamp, 0.0)
                self.assertEqual(status.symbol, "ETHUSD")
        finally:
            stop.set()
            thread.join()


if __name__ == "__main__":
    unittest.main()
