"""
Unit tests for websocket-subscription-reconciliation-after-reconnect.

Clocks are injected rather than slept through, so gap-duration assertions are
exact instead of resolution-dependent (``time.monotonic()`` advances in ~15.6ms
steps on Windows, which a 10ms sleep cannot reliably clear).

Several tests are regressions against defects that shipped in v1.0.0 and are
marked as such: backoff exceeding ``max_delay``, ``OverflowError`` on a large
attempt counter, wall-clock gap measurement, backfill running before
resubscription, silent symbol upper-casing, and deduplicator state desync.
"""
import threading
import unittest
from unittest import mock
from unittest.mock import Mock

from reconnect_manager import (
    MIN_BACKOFF_SEC,
    SubscriptionManager,
    SubscriptionReconciliation,
    TickDeduplicator,
    WebSocketReconnectEngine,
)


class ScriptedClock:
    """Returns a fixed sequence of readings, then repeats the last one."""

    def __init__(self, *readings):
        self.readings = list(readings)
        self.index = 0

    def __call__(self):
        reading = self.readings[min(self.index, len(self.readings) - 1)]
        self.index += 1
        return reading


class TestDesiredSubscriptionState(unittest.TestCase):

    def setUp(self):
        self.engine = WebSocketReconnectEngine(base_delay=1.0, max_delay=10.0, jitter_pct=0.20)

    def test_fresh_resubscription_from_state(self):
        engine = WebSocketReconnectEngine(
            monotonic_clock=ScriptedClock(100.0, 142.5),
            wall_clock=ScriptedClock(1_700_000_000.0, 1_700_000_042.5),
        )
        engine.subscribe("NIFTY")
        engine.subscribe("BANKNIFTY")
        sub_mock = Mock()

        engine.on_disconnect("test drop")
        event = engine.on_reconnect(sub_mock)

        sub_mock.assert_called_once_with(["BANKNIFTY", "NIFTY"])
        self.assertEqual(event.subscribed_symbols_count, 2)
        self.assertAlmostEqual(event.gap_duration_sec, 42.5)
        self.assertEqual(event.disconnect_timestamp, 1_700_000_000.0)
        self.assertEqual(event.reconnect_timestamp, 1_700_000_042.5)

    def test_repeated_reconnects_do_not_grow_the_subscription_set(self):
        """The core claim of the skill: N reconnects subscribe the same set N times.

        A replayed append-only subscribe log would hand progressively larger
        lists to the broker on each cycle.
        """
        for symbol in ("NIFTY", "BANKNIFTY", "FINNIFTY"):
            self.engine.subscribe(symbol)

        calls = []
        for _ in range(5):
            self.engine.on_disconnect()
            self.engine.on_reconnect(calls.append)

        self.assertEqual(len(calls), 5)
        for batch in calls:
            self.assertEqual(batch, ["BANKNIFTY", "FINNIFTY", "NIFTY"])

    def test_unsubscribe_removes_symbol_from_the_next_resubscription(self):
        self.engine.subscribe("NIFTY")
        self.engine.subscribe("BANKNIFTY")
        self.engine.unsubscribe("NIFTY")

        sub_mock = Mock()
        self.engine.on_disconnect()
        self.engine.on_reconnect(sub_mock)

        sub_mock.assert_called_once_with(["BANKNIFTY"])

    def test_symbols_are_stored_verbatim_by_default(self):
        """Regression: v1.0.0 upper-cased every symbol.

        Binance stream names are lower-case, so ``BTCUSDT@TRADE`` is not a
        stream the venue recognises -- the resubscription silently covers
        nothing.
        """
        self.engine.subscribe("btcusdt@trade")
        self.engine.subscribe("NSE:RELIANCE-EQ")

        self.assertEqual(self.engine.snapshot_desired(), ["NSE:RELIANCE-EQ", "btcusdt@trade"])

    def test_symbol_normalizer_is_opt_in(self):
        engine = WebSocketReconnectEngine(symbol_normalizer=str.upper)
        engine.subscribe("reliance")

        self.assertEqual(engine.snapshot_desired(), ["RELIANCE"])
        engine.unsubscribe("RELIANCE")
        self.assertEqual(engine.snapshot_desired(), [])

    def test_surrounding_whitespace_is_stripped(self):
        """A trailing space is never part of a symbol, and subscribing with one
        silently covers nothing at the venue."""
        self.engine.subscribe("  NIFTY \n")

        self.assertEqual(self.engine.snapshot_desired(), ["NIFTY"])
        self.engine.unsubscribe("NIFTY ")
        self.assertEqual(self.engine.snapshot_desired(), [])

    def test_blank_symbol_is_rejected(self):
        for bad in ("", "   ", None, 42):
            with self.subTest(symbol=bad):
                with self.assertRaises(ValueError):
                    self.engine.subscribe(bad)


class TestBackoff(unittest.TestCase):

    def setUp(self):
        self.engine = WebSocketReconnectEngine(base_delay=1.0, max_delay=10.0, jitter_pct=0.20)

    def test_backoff_never_exceeds_max_delay(self):
        """Regression: jitter used to be applied after the cap, so a
        ``max_delay`` of 10.0 could return 11.9."""
        for attempt in range(1, 41):
            for _ in range(200):
                delay = self.engine.calculate_backoff(attempt)
                self.assertGreaterEqual(delay, MIN_BACKOFF_SEC)
                self.assertLessEqual(delay, self.engine.max_delay)

    def test_backoff_reaches_the_cap_and_stays_jittered(self):
        saturated = [self.engine.calculate_backoff(30) for _ in range(200)]
        self.assertLessEqual(max(saturated), 10.0)
        self.assertGreaterEqual(min(saturated), 8.0)
        self.assertGreater(len(set(saturated)), 1, "jitter must decorrelate reconnecting clients")

    def test_backoff_growth_without_jitter_is_exact(self):
        engine = WebSocketReconnectEngine(base_delay=0.5, max_delay=10.0, jitter_pct=0.0)
        self.assertAlmostEqual(engine.calculate_backoff(1), 0.5)
        self.assertAlmostEqual(engine.calculate_backoff(2), 1.0)
        self.assertAlmostEqual(engine.calculate_backoff(3), 2.0)
        self.assertAlmostEqual(engine.calculate_backoff(6), 10.0)
        self.assertAlmostEqual(engine.calculate_backoff(7), 10.0)

    def test_backoff_survives_a_very_large_attempt_counter(self):
        """Regression: ``base_delay * 2 ** (attempt - 1)`` raised OverflowError
        from ~attempt 1025, crashing the reconnect loop during a long outage."""
        for attempt in (1_024, 1_025, 100_000):
            with self.subTest(attempt=attempt):
                self.assertAlmostEqual(self.engine.calculate_backoff(attempt), 10.0, delta=2.0)

    def test_backoff_rejects_non_positive_or_non_integer_attempts(self):
        for bad in (0, -3, 1.5, True, "2"):
            with self.subTest(attempt=bad):
                with self.assertRaises(ValueError):
                    self.engine.calculate_backoff(bad)

    def test_constructor_validates_its_parameters(self):
        bad_kwargs = [
            {"base_delay": 0.0},
            {"base_delay": -1.0},
            {"base_delay": 5.0, "max_delay": 1.0},
            {"jitter_pct": -0.1},
            {"jitter_pct": 1.5},
            {"history_limit": 0},
        ]
        for kwargs in bad_kwargs:
            with self.subTest(**kwargs):
                with self.assertRaises(ValueError):
                    WebSocketReconnectEngine(**kwargs)


class TestGapHandling(unittest.TestCase):

    def setUp(self):
        self.engine = WebSocketReconnectEngine()
        self.engine.subscribe("NIFTY")

    def test_resubscription_happens_before_backfill(self):
        """Regression: v1.0.0 backfilled first, leaving a second silent gap
        between the end of the backfill window and the live stream."""
        order = []
        self.engine.on_disconnect()
        self.engine.on_reconnect(
            subscribe_fn=lambda symbols: order.append("subscribe"),
            backfill_fn=lambda symbols, start, end: order.append("backfill"),
        )

        self.assertEqual(order, ["subscribe", "backfill"])

    def test_backfill_window_spans_disconnect_to_resubscription(self):
        captured = {}

        def backfill(symbols, start, end):
            captured["symbols"] = symbols
            captured["start"] = start
            captured["end"] = end

        engine = WebSocketReconnectEngine(
            monotonic_clock=ScriptedClock(10.0, 25.0),
            wall_clock=ScriptedClock(1_700_000_000.0, 1_700_000_015.0),
        )
        engine.subscribe("NIFTY")
        engine.on_disconnect()
        event = engine.on_reconnect(Mock(), backfill_fn=backfill)

        self.assertEqual(captured["symbols"], ["NIFTY"])
        self.assertEqual(captured["start"], 1_700_000_000.0)
        self.assertEqual(captured["end"], 1_700_000_015.0)
        self.assertTrue(event.backfill_executed)
        self.assertIsNone(event.backfill_error)

    def test_gap_duration_survives_a_wall_clock_step(self):
        """Regression: the gap was ``time.time()`` arithmetic, so an NTP
        correction during the outage produced a negative or inflated gap and a
        correspondingly wrong backfill request."""
        engine = WebSocketReconnectEngine(
            monotonic_clock=ScriptedClock(500.0, 530.0),
            wall_clock=ScriptedClock(1_700_000_000.0, 1_700_000_000.0 - 3_600.0),
        )
        engine.subscribe("NIFTY")
        engine.on_disconnect()
        event = engine.on_reconnect(Mock())

        self.assertAlmostEqual(event.gap_duration_sec, 30.0)

    def test_repeat_disconnect_notification_does_not_shrink_the_gap(self):
        """An SDK firing both on_error and on_close for one drop must not reset
        the clock; the second notification would otherwise erase most of the
        gap and the backfill with it."""
        engine = WebSocketReconnectEngine(
            monotonic_clock=ScriptedClock(100.0, 160.0),
            wall_clock=ScriptedClock(1_700_000_000.0, 1_700_000_060.0),
        )
        engine.subscribe("NIFTY")
        engine.on_disconnect("on_error")
        engine.on_disconnect("on_close")
        event = engine.on_reconnect(Mock())

        self.assertAlmostEqual(event.gap_duration_sec, 60.0)

    def test_backfill_failure_is_recorded_not_swallowed(self):
        def failing_backfill(symbols, start, end):
            raise TimeoutError("historical endpoint timed out")

        sub_mock = Mock()
        self.engine.on_disconnect()
        event = self.engine.on_reconnect(sub_mock, backfill_fn=failing_backfill)

        sub_mock.assert_called_once()
        self.assertFalse(event.backfill_executed)
        self.assertIn("TimeoutError", event.backfill_error)

    def test_subscribe_failure_preserves_the_original_gap_for_a_retry(self):
        self.engine.on_disconnect()
        original_disconnect = self.engine.last_disconnect

        with self.assertRaises(ConnectionError):
            self.engine.on_reconnect(Mock(side_effect=ConnectionError("socket closed")))

        self.assertEqual(self.engine.last_disconnect, original_disconnect)
        self.assertEqual(len(self.engine.reconnect_history), 0)

        captured = {}
        self.engine.on_reconnect(
            Mock(),
            backfill_fn=lambda symbols, start, end: captured.update(start=start),
        )
        self.assertEqual(captured["start"], original_disconnect)

    def test_reconnect_without_a_prior_disconnect_skips_backfill(self):
        backfill_mock = Mock()
        event = self.engine.on_reconnect(Mock(), backfill_fn=backfill_mock)

        backfill_mock.assert_not_called()
        self.assertEqual(event.gap_duration_sec, 0.0)
        self.assertFalse(event.backfill_executed)

    def test_reconnect_history_is_bounded(self):
        engine = WebSocketReconnectEngine(history_limit=3)
        engine.subscribe("NIFTY")
        for _ in range(5):
            engine.on_disconnect()
            engine.on_reconnect(Mock())

        self.assertEqual(len(engine.reconnect_history), 3)


class TestSubscriptionReconciliation(unittest.TestCase):

    def setUp(self):
        self.engine = WebSocketReconnectEngine()
        for symbol in ("AAPL", "MSFT", "TSLA"):
            self.engine.subscribe(symbol)

    def test_missing_and_unexpected_are_both_reported(self):
        result = self.engine.reconcile_subscriptions(["AAPL", "MSFT", "GOOG"])

        self.assertEqual(result.missing, frozenset({"TSLA"}))
        self.assertEqual(result.unexpected, frozenset({"GOOG"}))
        self.assertFalse(result.is_clean)

    def test_exact_match_is_clean(self):
        result = self.engine.reconcile_subscriptions(["TSLA", "AAPL", "MSFT"])

        self.assertEqual(result.missing, frozenset())
        self.assertEqual(result.unexpected, frozenset())
        self.assertTrue(result.is_clean)

    def test_subscribe_fn_returning_a_collection_is_reconciled_automatically(self):
        self.engine.on_disconnect()
        event = self.engine.on_reconnect(lambda symbols: ["AAPL", "MSFT"])

        self.assertIsInstance(event.reconciliation, SubscriptionReconciliation)
        self.assertEqual(event.reconciliation.missing, frozenset({"TSLA"}))

    def test_a_non_symbol_return_value_is_not_mistaken_for_an_acknowledgement(self):
        """An SDK returning internal token ids must not raise a false
        reconciliation alarm on every reconnect."""
        self.engine.on_disconnect()
        event = self.engine.on_reconnect(lambda symbols: [408_065, 738_561])

        self.assertIsNone(event.reconciliation)

    def test_subscribe_fn_returning_nothing_leaves_reconciliation_unset(self):
        self.engine.on_disconnect()
        event = self.engine.on_reconnect(Mock())

        self.assertIsNone(event.reconciliation)


class TestTickDeduplicator(unittest.TestCase):

    def test_tick_deduplication(self):
        dedup = TickDeduplicator(max_history=100)

        self.assertFalse(dedup.is_duplicate("NIFTY", 1_700_000_000.0, 101))
        self.assertTrue(dedup.is_duplicate("NIFTY", 1_700_000_000.0, 101))
        self.assertFalse(dedup.is_duplicate("NIFTY", 1_700_000_000.0, 102))
        self.assertFalse(dedup.is_duplicate("BANKNIFTY", 1_700_000_000.0, 101))

    def test_window_is_bounded_and_evicts_oldest_first(self):
        dedup = TickDeduplicator(max_history=3)
        for seq in range(3):
            dedup.is_duplicate("NIFTY", 1.0, seq)

        self.assertTrue(dedup.is_duplicate("NIFTY", 1.0, 0))

        dedup.is_duplicate("NIFTY", 1.0, 3)  # evicts seq 0
        self.assertEqual(len(dedup.history_queue), 3)
        self.assertEqual(len(dedup.seen_signatures), 3)
        self.assertFalse(dedup.is_duplicate("NIFTY", 1.0, 0))

    def test_state_stays_consistent_across_threads(self):
        """Regression: an unlocked deduplicator shared by a fan-out consumer
        pool desynchronises the set from the bounded queue -- the set grows
        without limit and strands signatures that then suppress genuine ticks.
        """
        dedup = TickDeduplicator(max_history=200)
        stop = threading.Event()

        def spam(offset):
            counter = 0
            while not stop.is_set():
                dedup.is_duplicate("NIFTY", float(offset * 10 ** 6 + counter), counter)
                counter += 1

        threads = [threading.Thread(target=spam, args=(k,)) for k in range(4)]
        for thread in threads:
            thread.start()
        stop.wait(0.3)
        stop.set()
        for thread in threads:
            thread.join(timeout=5)

        self.assertEqual(len(dedup.history_queue), len(dedup.seen_signatures))
        self.assertLessEqual(len(dedup.seen_signatures), 200)

    def test_invalid_max_history_is_rejected(self):
        for bad in (0, -1, 2.5, True):
            with self.subTest(max_history=bad):
                with self.assertRaises(ValueError):
                    TickDeduplicator(max_history=bad)


class TestConcurrency(unittest.TestCase):

    def test_subscribing_while_reconnecting_is_safe(self):
        engine = WebSocketReconnectEngine()
        for index in range(500):
            engine.subscribe(f"SYM{index}")

        errors = []
        stop = threading.Event()

        def churn():
            index = 10_000
            try:
                while not stop.is_set():
                    engine.subscribe(f"X{index}")
                    engine.unsubscribe(f"X{index - 1}")
                    index += 1
            except Exception as exc:  # pragma: no cover - failure path
                errors.append(exc)

        churner = threading.Thread(target=churn)
        churner.start()
        try:
            for _ in range(200):
                engine.on_disconnect()
                event = engine.on_reconnect(lambda symbols: None)
                self.assertGreaterEqual(event.subscribed_symbols_count, 500)
        finally:
            stop.set()
            churner.join(timeout=5)

        self.assertEqual(errors, [])


class TestLegacySubscriptionManager(unittest.TestCase):

    def test_backward_compatibility(self):
        manager = SubscriptionManager()
        manager.add_symbol("NIFTY")
        manager.on_disconnect()

        sub_mock = Mock()
        manager.on_reconnect(sub_mock)

        sub_mock.assert_called_once_with(["NIFTY"])

    def test_gap_is_recorded_even_when_the_clock_reads_zero(self):
        """Regression: ``if self.last_disconnect:`` discarded a legitimate 0.0
        reading, dropping the gap record and the backfill call with it."""
        manager = SubscriptionManager()
        manager.add_symbol("NIFTY")
        backfill_mock = Mock()

        with mock.patch("reconnect_manager.time.monotonic", side_effect=[0.0, 4.0]):
            manager.on_disconnect()
            manager.on_reconnect(Mock(), backfill_fn=backfill_mock)

        self.assertEqual(manager.gap_log, [4.0])
        backfill_mock.assert_called_once()


if __name__ == "__main__":
    unittest.main()
