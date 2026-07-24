"""
Unit tests for websocket-reconnect-without-duplicate-subscriptions skill.

Tests:
1. Decoupled state resubscription (fresh resubscription on reconnect without duplication).
2. Jittered exponential backoff calculation bounds.
3. Gap window duration tracking and REST backfill callback execution.
4. Consumer-level tick deduplication filter.
5. Backward compatibility with SubscriptionManager class.
"""
import time
import unittest
from unittest.mock import Mock
from reconnect_manager import (
    SubscriptionManager,
    TickDeduplicator,
    WebSocketReconnectEngine,
)


class TestWebSocketReconnectManager(unittest.TestCase):

    def setUp(self):
        self.engine = WebSocketReconnectEngine(base_delay=1.0, max_delay=10.0, jitter_pct=0.20)

    def test_fresh_resubscription_from_state(self):
        self.engine.subscribe("NIFTY")
        self.engine.subscribe("BANKNIFTY")

        sub_mock = Mock()
        self.engine.on_disconnect()
        time.sleep(0.01)
        event = self.engine.on_reconnect(sub_mock)

        sub_mock.assert_called_once_with(["BANKNIFTY", "NIFTY"])
        self.assertEqual(event.subscribed_symbols_count, 2)
        self.assertGreater(event.gap_duration_sec, 0.0)

    def test_backoff_calculation(self):
        for attempt in range(1, 5):
            delay = self.engine.calculate_backoff(attempt)
            self.assertGreater(delay, 0.0)
            self.assertLessEqual(delay, 12.0)  # Max delay 10.0 + 20% jitter = 12.0

    def test_gap_backfill_callback(self):
        self.engine.subscribe("NIFTY")
        self.engine.on_disconnect()

        backfill_mock = Mock()
        sub_mock = Mock()

        event = self.engine.on_reconnect(sub_mock, backfill_fn=backfill_mock)

        self.assertTrue(event.backfill_executed)
        backfill_mock.assert_called_once()

    def test_tick_deduplication(self):
        dedup = TickDeduplicator(max_history=100)

        # First delivery -> not duplicate
        self.assertFalse(dedup.is_duplicate("NIFTY", 1700000000.0, 101))

        # Duplicate delivery -> duplicate!
        self.assertTrue(dedup.is_duplicate("NIFTY", 1700000000.0, 101))

        # Different sequence number -> not duplicate
        self.assertFalse(dedup.is_duplicate("NIFTY", 1700000000.0, 102))

    def test_backward_compatibility(self):
        sm = SubscriptionManager()
        sm.add_symbol("NIFTY")
        sm.on_disconnect()

        sub_mock = Mock()
        sm.on_reconnect(sub_mock)

        sub_mock.assert_called_once_with(["NIFTY"])


if __name__ == "__main__":
    unittest.main()
