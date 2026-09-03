"""
Unit tests for websocket-reconnection-with-state-recovery skill.

The regression tests worth naming explicitly, because each one fails against the
older implementation:

* ``test_backoff_never_exceeds_cap`` - additive jitter used to return up to 1.5x the
  documented ceiling.
* ``test_first_retry_is_bounded_by_base_delay`` - the attempt counter used to be
  incremented before the delay was computed, so the first retry waited 2x base.
* ``test_backoff_survives_a_very_long_outage`` - ``base * 2**attempts`` used to raise
  ``OverflowError`` once the attempt counter passed ~1024.
* ``test_partial_gap_fill_is_rejected`` / ``test_gap_without_fill_callback_latches`` /
  ``test_gap_fill_exception_latches`` - an unfilled hole used to be logged and then
  silently absorbed, with the watermark advanced as though recovery had happened.
* ``test_stale_message_does_not_regress_watermark`` - a replayed frame used to rewind the
  watermark, fabricating a gap and re-emitting already-applied messages.
"""
import logging
import random
import threading
import unittest

from ws_recovery import (
    ConnectionState,
    SequenceGap,
    WebSocketStateRecoveryManager,
    WSMessage,
)

logging.disable(logging.CRITICAL)


def mock_rest_gap_fill(symbol, start_seq, end_seq):
    """Mock REST gap fill returning exactly the missing messages."""
    return [
        WSMessage(symbol=symbol, sequence_id=s, data={"gap_filled": True})
        for s in range(start_seq, end_seq + 1)
    ]


class ManagerTestCase(unittest.TestCase):
    def make(self, **kwargs):
        kwargs.setdefault("base_backoff_sec", 1.0)
        kwargs.setdefault("max_backoff_sec", 30.0)
        kwargs.setdefault("rest_gap_fill_fn", mock_rest_gap_fill)
        kwargs.setdefault("rng", random.Random(20260902))
        mgr = WebSocketStateRecoveryManager(**kwargs)
        mgr.register_symbol_subscription("BTCUSDT")
        return mgr


class TestConstructorValidation(ManagerTestCase):
    def test_rejects_invalid_configuration(self):
        for kwargs in (
            {"base_backoff_sec": 0.0},
            {"base_backoff_sec": -1.0},
            {"max_backoff_sec": 0.5},          # below base
            {"jitter_factor": -0.1},
            {"jitter_factor": 1.5},
            {"max_gap_fill_size": 0},
            {"max_retained_messages": -1},
        ):
            with self.subTest(**kwargs):
                with self.assertRaises(ValueError):
                    WebSocketStateRecoveryManager(**kwargs)

    def test_message_validation(self):
        with self.assertRaises(ValueError):
            WSMessage("   ", 1, {})
        with self.assertRaises(ValueError):
            WSMessage("BTCUSDT", -1, {})
        with self.assertRaises(TypeError):
            WSMessage("BTCUSDT", 1.5, {})
        with self.assertRaises(TypeError):
            WSMessage("BTCUSDT", True, {})

    def test_non_message_input_is_rejected(self):
        mgr = self.make()
        with self.assertRaises(TypeError):
            mgr.process_incoming_message({"symbol": "BTCUSDT", "sequence_id": 1})


class TestBackoff(ManagerTestCase):
    def test_first_retry_is_bounded_by_base_delay(self):
        # Attempt 0 draws inside [0, base]; it does not start at 2 x base.
        mgr = self.make()
        self.assertLessEqual(mgr.on_connection_lost("Drop 1"), 1.0)
        self.assertEqual(mgr.reconnect_attempts, 1)

    def test_backoff_ceiling_doubles_per_attempt(self):
        mgr = self.make(jitter_factor=0.0)
        self.assertAlmostEqual(mgr.compute_next_backoff(attempt=0), 1.0)
        self.assertAlmostEqual(mgr.compute_next_backoff(attempt=1), 2.0)
        self.assertAlmostEqual(mgr.compute_next_backoff(attempt=4), 16.0)
        self.assertAlmostEqual(mgr.compute_next_backoff(attempt=5), 30.0)  # clamped

    def test_backoff_never_exceeds_cap(self):
        mgr = self.make(max_backoff_sec=30.0, jitter_factor=1.0)
        for attempt in range(0, 40):
            delay = mgr.compute_next_backoff(attempt=attempt)
            self.assertGreaterEqual(delay, 0.0)
            self.assertLessEqual(delay, 30.0)

    def test_equal_jitter_keeps_a_floor(self):
        mgr = self.make(jitter_factor=0.5)
        for _ in range(200):
            delay = mgr.compute_next_backoff(attempt=3)  # capped = 8.0
            self.assertGreaterEqual(delay, 4.0)
            self.assertLessEqual(delay, 8.0)

    def test_zero_jitter_is_deterministic(self):
        mgr = self.make(jitter_factor=0.0)
        self.assertEqual(
            {mgr.compute_next_backoff(attempt=2) for _ in range(50)}, {4.0}
        )

    def test_full_jitter_actually_spreads_reconnects(self):
        mgr = self.make(jitter_factor=1.0)
        draws = {round(mgr.compute_next_backoff(attempt=6), 6) for _ in range(200)}
        self.assertGreater(len(draws), 100, "full jitter must not collapse onto one instant")

    def test_backoff_survives_a_very_long_outage(self):
        # A 30s cadence reaches attempt 1024 in under nine hours; 2**1024 overflows float.
        mgr = self.make()
        for _ in range(1100):
            delay = mgr.on_connection_lost("Prolonged venue outage")
            self.assertLessEqual(delay, 30.0)
        self.assertEqual(mgr.reconnect_attempts, 1100)

    def test_scheduled_rotation_does_not_escalate_backoff(self):
        # Binance documents a 24h connection lifetime; an expected eviction is not a fault.
        mgr = self.make()
        mgr.on_connection_lost("Drop 1")
        mgr.on_connection_lost("Drop 2")
        attempts_before = mgr.reconnect_attempts
        delay = mgr.on_connection_lost("24h connection rotation", scheduled=True)
        self.assertEqual(mgr.reconnect_attempts, attempts_before)
        self.assertLessEqual(delay, 1.0)
        self.assertEqual(mgr.state, ConnectionState.DISCONNECTED)

    def test_negative_attempt_rejected(self):
        with self.assertRaises(ValueError):
            self.make().compute_next_backoff(attempt=-1)


class TestSubscriptionLifecycle(ManagerTestCase):
    def test_resubscription_list_is_sorted_and_normalized(self):
        mgr = self.make()
        mgr.register_symbol_subscription(" ethusdt ")
        mgr.register_symbol_subscription("ADAUSDT")
        self.assertEqual(
            mgr.on_connection_established(), ["ADAUSDT", "BTCUSDT", "ETHUSDT"]
        )
        self.assertEqual(mgr.state, ConnectionState.SUBSCRIBED)

    def test_registering_the_same_symbol_twice_does_not_duplicate(self):
        mgr = self.make()
        for _ in range(5):
            mgr.register_symbol_subscription("btcusdt")
        self.assertEqual(mgr.on_connection_established(), ["BTCUSDT"])

    def test_private_stream_passes_through_authenticated(self):
        mgr = self.make(requires_auth=True)
        mgr.on_connection_lost("Drop")
        mgr.on_connection_established()
        self.assertEqual(
            list(mgr.state_history),
            [
                ConnectionState.DISCONNECTED,
                ConnectionState.CONNECTING,
                ConnectionState.AUTHENTICATED,
                ConnectionState.SUBSCRIBED,
            ],
        )

    def test_public_stream_skips_authenticated(self):
        # A public market-data stream has no auth step; claiming one would be fiction.
        mgr = self.make(requires_auth=False)
        mgr.on_connection_lost("Drop")
        mgr.on_connection_established()
        self.assertNotIn(ConnectionState.AUTHENTICATED, mgr.state_history)

    def test_full_lifecycle_transition_sequence(self):
        mgr = self.make()
        mgr.on_connection_lost("Drop")
        mgr.on_connection_established()
        mgr.process_incoming_message(WSMessage("BTCUSDT", 100, {}))
        mgr.process_incoming_message(WSMessage("BTCUSDT", 104, {}))  # gap 101..103
        self.assertEqual(
            list(mgr.state_history),
            [
                ConnectionState.DISCONNECTED,
                ConnectionState.CONNECTING,
                ConnectionState.SUBSCRIBED,
                ConnectionState.STREAMING,
                ConnectionState.RECOVERING_GAP,
                ConnectionState.STREAMING,
            ],
        )

    def test_invalid_symbol_rejected(self):
        mgr = self.make()
        with self.assertRaises(ValueError):
            mgr.register_symbol_subscription("")


class TestSequenceGapRecovery(ManagerTestCase):
    def test_first_message_adopts_baseline(self):
        mgr = self.make()
        out = mgr.process_incoming_message(WSMessage("BTCUSDT", 100, {"price": 60000.0}))
        self.assertEqual([m.sequence_id for m in out], [100])
        self.assertTrue(mgr.is_synchronized("BTCUSDT"))

    def test_contiguous_messages_pass_through(self):
        mgr = self.make()
        for seq in (100, 101, 102):
            self.assertEqual(len(mgr.process_incoming_message(WSMessage("BTCUSDT", seq, {}))), 1)
        self.assertEqual(mgr.gap_fill_success_count, 0)
        self.assertEqual(mgr.state, ConnectionState.STREAMING)

    def test_sequence_gap_recovery(self):
        mgr = self.make()
        mgr.process_incoming_message(WSMessage("BTCUSDT", 100, {"price": 60000.0}))
        out = mgr.process_incoming_message(WSMessage("BTCUSDT", 104, {"price": 60050.0}))
        self.assertEqual([m.sequence_id for m in out], [101, 102, 103, 104])
        self.assertEqual(mgr.state, ConnectionState.STREAMING)
        self.assertTrue(mgr.is_synchronized())
        self.assertEqual(mgr.gap_fill_success_count, 1)
        self.assertEqual(mgr.last_seen_sequence["BTCUSDT"], 104)

    def test_gap_fill_callback_receives_only_the_missing_range(self):
        calls = []

        def spy(symbol, start, end):
            calls.append((symbol, start, end))
            return mock_rest_gap_fill(symbol, start, end)

        mgr = self.make(rest_gap_fill_fn=spy)
        mgr.process_incoming_message(WSMessage("BTCUSDT", 10, {}))
        mgr.process_incoming_message(WSMessage("BTCUSDT", 15, {}))
        self.assertEqual(calls, [("BTCUSDT", 11, 14)])

    def test_gap_is_tracked_per_symbol(self):
        mgr = self.make()
        mgr.process_incoming_message(WSMessage("BTCUSDT", 100, {}))
        mgr.process_incoming_message(WSMessage("ETHUSDT", 5000, {}))
        out = mgr.process_incoming_message(WSMessage("ETHUSDT", 5001, {}))
        self.assertEqual([m.sequence_id for m in out], [5001])
        self.assertEqual(mgr.gap_fill_success_count, 0)


class TestFailClosedRecovery(ManagerTestCase):
    def _gap_with(self, fill_fn, **kwargs):
        mgr = self.make(rest_gap_fill_fn=fill_fn, **kwargs)
        mgr.process_incoming_message(WSMessage("BTCUSDT", 100, {}))
        out = mgr.process_incoming_message(WSMessage("BTCUSDT", 105, {}))
        return mgr, out

    def test_partial_gap_fill_is_rejected(self):
        def partial(symbol, start, end):
            return mock_rest_gap_fill(symbol, start, end - 1)  # one short

        mgr, out = self._gap_with(partial)
        self.assertEqual(out, [])
        self.assertFalse(mgr.is_synchronized("BTCUSDT"))
        self.assertEqual(mgr.state, ConnectionState.RECOVERING_GAP)
        gap = mgr.unrecovered_gaps()["BTCUSDT"]
        self.assertEqual((gap.first_missing, gap.last_missing, gap.size), (101, 104, 4))
        self.assertIn("partial fill", gap.reason)

    def test_empty_gap_fill_is_rejected(self):
        mgr, out = self._gap_with(lambda s, a, b: [])
        self.assertEqual(out, [])
        self.assertFalse(mgr.is_synchronized())

    def test_none_gap_fill_is_rejected(self):
        mgr, out = self._gap_with(lambda s, a, b: None)
        self.assertEqual(out, [])
        self.assertFalse(mgr.is_synchronized())

    def test_out_of_order_gap_fill_is_rejected(self):
        def shuffled(symbol, start, end):
            msgs = mock_rest_gap_fill(symbol, start, end)
            msgs.reverse()
            return msgs

        mgr, out = self._gap_with(shuffled)
        self.assertEqual(out, [])
        self.assertIn("out of order", mgr.unrecovered_gaps()["BTCUSDT"].reason)

    def test_wrong_symbol_gap_fill_is_rejected(self):
        def wrong_symbol(symbol, start, end):
            return mock_rest_gap_fill("ETHUSDT", start, end)

        mgr, out = self._gap_with(wrong_symbol)
        self.assertEqual(out, [])
        self.assertIn("expected", mgr.unrecovered_gaps()["BTCUSDT"].reason)

    def test_non_sequence_gap_fill_is_rejected(self):
        # A generator has the right elements but no length; accepting it would consume the
        # range without ever proving it was complete.
        def lazy(symbol, start, end):
            return (WSMessage(symbol, s, {}) for s in range(start, end + 1))

        mgr, out = self._gap_with(lazy)
        self.assertEqual(out, [])
        self.assertIn("sequence of WSMessage", mgr.unrecovered_gaps()["BTCUSDT"].reason)

    def test_overlong_gap_fill_is_rejected(self):
        def too_many(symbol, start, end):
            return mock_rest_gap_fill(symbol, start, end + 1)

        mgr, out = self._gap_with(too_many)
        self.assertEqual(out, [])
        self.assertIn("partial fill", mgr.unrecovered_gaps()["BTCUSDT"].reason)

    def test_gap_fill_exception_latches(self):
        def boom(symbol, start, end):
            raise ConnectionError("REST endpoint unreachable")

        mgr, out = self._gap_with(boom)
        self.assertEqual(out, [])
        self.assertFalse(mgr.is_synchronized())
        self.assertIn("ConnectionError", mgr.unrecovered_gaps()["BTCUSDT"].reason)

    def test_gap_without_fill_callback_latches(self):
        mgr = WebSocketStateRecoveryManager(rng=random.Random(1))
        mgr.process_incoming_message(WSMessage("BTCUSDT", 100, {}))
        out = mgr.process_incoming_message(WSMessage("BTCUSDT", 105, {}))
        self.assertEqual(out, [])
        self.assertFalse(mgr.is_synchronized("BTCUSDT"))
        self.assertIn("no gap-fill callback", mgr.unrecovered_gaps()["BTCUSDT"].reason)

    def test_oversized_gap_is_not_refetched(self):
        calls = []

        def spy(symbol, start, end):
            calls.append((start, end))
            return mock_rest_gap_fill(symbol, start, end)

        mgr = self.make(rest_gap_fill_fn=spy, max_gap_fill_size=1000)
        mgr.process_incoming_message(WSMessage("BTCUSDT", 100, {}))
        out = mgr.process_incoming_message(WSMessage("BTCUSDT", 100 + 1002, {}))
        self.assertEqual(out, [])
        self.assertEqual(calls, [], "an outage-sized hole must escalate, not hammer REST")
        self.assertIn("max_gap_fill_size", mgr.unrecovered_gaps()["BTCUSDT"].reason)

    def test_gap_exactly_at_the_limit_is_still_filled(self):
        mgr = self.make(max_gap_fill_size=4)
        mgr.process_incoming_message(WSMessage("BTCUSDT", 100, {}))
        out = mgr.process_incoming_message(WSMessage("BTCUSDT", 105, {}))  # missing 101..104
        self.assertEqual([m.sequence_id for m in out], [101, 102, 103, 104, 105])

    def test_messages_are_withheld_until_resynchronize(self):
        mgr, _ = self._gap_with(lambda s, a, b: [])
        for seq in range(106, 111):
            self.assertEqual(mgr.process_incoming_message(WSMessage("BTCUSDT", seq, {})), [])
        self.assertEqual(mgr.withheld_message_count, 5)

        mgr.resynchronize("btcusdt", 200)
        self.assertTrue(mgr.is_synchronized("BTCUSDT"))
        self.assertEqual(mgr.state, ConnectionState.STREAMING)
        self.assertEqual(mgr.unrecovered_gaps(), {})

        out = mgr.process_incoming_message(WSMessage("BTCUSDT", 200, {}))
        self.assertEqual([m.sequence_id for m in out], [200])

    def test_one_unsynced_symbol_does_not_block_another(self):
        mgr = self.make(rest_gap_fill_fn=lambda s, a, b: [])
        mgr.process_incoming_message(WSMessage("BTCUSDT", 100, {}))
        mgr.process_incoming_message(WSMessage("BTCUSDT", 105, {}))
        mgr.process_incoming_message(WSMessage("ETHUSDT", 10, {}))
        out = mgr.process_incoming_message(WSMessage("ETHUSDT", 11, {}))
        self.assertEqual([m.sequence_id for m in out], [11])
        self.assertFalse(mgr.is_synchronized("BTCUSDT"))
        self.assertTrue(mgr.is_synchronized("ETHUSDT"))
        self.assertFalse(mgr.is_synchronized(), "the aggregate gate stays closed")

    def test_resynchronize_validates_input(self):
        mgr = self.make()
        with self.assertRaises(ValueError):
            mgr.resynchronize("BTCUSDT", -1)
        with self.assertRaises(TypeError):
            mgr.resynchronize("BTCUSDT", 1.0)
        with self.assertRaises(ValueError):
            mgr.resynchronize("", 1)

    def test_resynchronize_sets_the_next_expected_sequence(self):
        mgr = self.make()
        mgr.resynchronize("BTCUSDT", 5000)  # snapshot lastUpdateId + 1
        out = mgr.process_incoming_message(WSMessage("BTCUSDT", 5000, {}))
        self.assertEqual([m.sequence_id for m in out], [5000])
        self.assertEqual(mgr.gap_fill_success_count, 0, "5000 is contiguous, not a gap")


class TestDuplicateAndStaleMessages(ManagerTestCase):
    def test_duplicate_message_is_dropped(self):
        mgr = self.make()
        mgr.process_incoming_message(WSMessage("BTCUSDT", 100, {}))
        self.assertEqual(mgr.process_incoming_message(WSMessage("BTCUSDT", 100, {})), [])
        self.assertEqual(mgr.duplicate_message_count, 1)

    def test_stale_message_does_not_regress_watermark(self):
        mgr = self.make()
        mgr.process_incoming_message(WSMessage("BTCUSDT", 100, {}))
        mgr.process_incoming_message(WSMessage("BTCUSDT", 104, {}))  # fills 101..103
        self.assertEqual(mgr.process_incoming_message(WSMessage("BTCUSDT", 102, {})), [])
        self.assertEqual(mgr.last_seen_sequence["BTCUSDT"], 104)

        # The next genuine message must be an ordinary contiguous advance, not a refetch.
        out = mgr.process_incoming_message(WSMessage("BTCUSDT", 105, {}))
        self.assertEqual([m.sequence_id for m in out], [105])
        self.assertEqual(mgr.gap_fill_success_count, 1, "no second, fabricated gap fill")

    def test_symbol_case_and_padding_are_normalized(self):
        mgr = self.make()
        mgr.process_incoming_message(WSMessage("btcusdt", 100, {}))
        self.assertEqual(mgr.process_incoming_message(WSMessage(" BTCUSDT ", 100, {})), [])
        self.assertEqual(mgr.duplicate_message_count, 1)


class TestRetentionAndCounters(ManagerTestCase):
    def test_processed_messages_is_bounded(self):
        mgr = self.make(max_retained_messages=10)
        for seq in range(1000):
            mgr.process_incoming_message(WSMessage("BTCUSDT", seq, {}))
        self.assertEqual(len(mgr.processed_messages), 10)
        self.assertEqual(mgr.processed_messages[-1].sequence_id, 999)

    def test_retention_can_be_disabled(self):
        mgr = self.make(max_retained_messages=0)
        for seq in range(100):
            mgr.process_incoming_message(WSMessage("BTCUSDT", seq, {}))
        self.assertEqual(len(mgr.processed_messages), 0)

    def test_reconnect_attempts_reset_only_after_a_processed_message(self):
        mgr = self.make()
        mgr.on_connection_lost("Drop 1")
        mgr.on_connection_lost("Drop 2")
        mgr.on_connection_established()
        self.assertEqual(mgr.reconnect_attempts, 2, "opening a socket proves nothing")
        mgr.process_incoming_message(WSMessage("BTCUSDT", 1, {}))
        self.assertEqual(mgr.reconnect_attempts, 0)

    def test_withheld_messages_do_not_reset_backoff(self):
        mgr = self.make(rest_gap_fill_fn=lambda s, a, b: [])
        mgr.process_incoming_message(WSMessage("BTCUSDT", 100, {}))
        mgr.process_incoming_message(WSMessage("BTCUSDT", 105, {}))
        mgr.on_connection_lost("Drop 1")
        mgr.process_incoming_message(WSMessage("BTCUSDT", 106, {}))
        self.assertEqual(mgr.reconnect_attempts, 1)


class TestConcurrency(ManagerTestCase):
    def test_concurrent_ingestion_emits_each_message_exactly_once(self):
        mgr = self.make(max_retained_messages=100_000)
        symbols = ["BTCUSDT", "ETHUSDT", "ADAUSDT", "SOLUSDT"]
        emitted = []
        emitted_lock = threading.Lock()

        def worker(symbol):
            local = []
            for seq in range(1, 501):
                local.extend(mgr.process_incoming_message(WSMessage(symbol, seq, {})))
            with emitted_lock:
                emitted.extend(local)

        threads = [threading.Thread(target=worker, args=(s,)) for s in symbols]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        self.assertEqual(len(emitted), 4 * 500)
        keys = {(m.symbol, m.sequence_id) for m in emitted}
        self.assertEqual(len(keys), 4 * 500)
        self.assertEqual(mgr.gap_fill_success_count, 0)
        self.assertTrue(mgr.is_synchronized())


class TestSequenceGapDataclass(unittest.TestCase):
    def test_size_is_inclusive(self):
        self.assertEqual(SequenceGap("BTCUSDT", 101, 103).size, 3)
        self.assertEqual(SequenceGap("BTCUSDT", 101, 101).size, 1)


if __name__ == "__main__":
    unittest.main()
