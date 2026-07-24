"""
Unit tests for graceful-degradation-to-polling-fallback skill.

Tests:
1. Healthy WebSocket tick ingestion and deduplication.
2. Detecting stream silence and degrading to REST polling mode.
3. Ingesting REST polled ticks and deduplicating handover updates.
4. WebSocket stream recovery and stabilization after consecutive ticks.
"""
import time
import unittest
from feed_fallback_manager import FeedFallbackManager, FeedMode, TickPayload


class TestFeedFallbackManager(unittest.TestCase):

    def setUp(self):
        self.mgr = FeedFallbackManager(
            symbol="ETHUSD", silence_timeout_seconds=0.1, required_stabilization_ticks=3
        )

    def test_healthy_websocket_ingestion(self):
        t1 = TickPayload(symbol="ETHUSD", price=3000.0, volume=1.0, timestamp=100.0)
        res = self.mgr.ingest_websocket_tick(t1)

        self.assertIsNotNone(res)
        self.assertEqual(res.price, 3000.0)
        self.assertEqual(self.mgr.feed_mode, FeedMode.HEALTHY_WEBSOCKET)

    def test_silence_degradation_to_polling(self):
        # Simulate silence breach
        self.mgr.last_ws_tick_time = time.time() - 0.2  # > 0.1s silence limit
        mode = self.mgr.check_feed_health()

        self.assertEqual(mode, FeedMode.DEGRADED_POLLING)

    def test_rest_polling_and_deduplication(self):
        self.mgr.last_processed_timestamp = 100.0
        self.mgr.feed_mode = FeedMode.DEGRADED_POLLING

        def mock_rest_fetch(symbol: str) -> TickPayload:
            return TickPayload(symbol=symbol, price=3005.0, volume=0.5, timestamp=101.0)

        tick = self.mgr.poll_rest_fallback(mock_rest_fetch)
        self.assertIsNotNone(tick)
        self.assertEqual(tick.source, "REST_POLLING")
        self.assertEqual(tick.price, 3005.0)

        # Attempt to ingest duplicate timestamp 101.0 -> should be deduplicated (None)
        duplicate_tick = self.mgr.poll_rest_fallback(mock_rest_fetch)
        self.assertIsNone(duplicate_tick)

    def test_websocket_recovery_and_stabilization(self):
        self.mgr.feed_mode = FeedMode.DEGRADED_POLLING

        # Send 3 consecutive WS ticks to trigger stabilization
        for i in range(3):
            t = TickPayload(symbol="ETHUSD", price=3010.0 + i, volume=1.0, timestamp=200.0 + i)
            self.mgr.ingest_websocket_tick(t)

        self.assertEqual(self.mgr.feed_mode, FeedMode.HEALTHY_WEBSOCKET)


if __name__ == "__main__":
    unittest.main()
