"""
Unit tests for crypto-exchange-api-integration skill.

Tests:
1. WeightRateLimiter sliding window capacity & pruning.
2. Async acquire weight backoff.
3. Server header weight synchronization.
4. Multi-namespace rate limit budget separation.
5. Self-Trade Prevention (STP) and order payload construction.
6. Rolling 24h P&L tracking for 24/7 markets.
"""
import asyncio
import time
import unittest
from weight_rate_limiter import (
    CryptoExchangeRateLimiter,
    CryptoOrderPayload,
    OrderType,
    Rolling24hPnLTracker,
    SelfTradePreventionMode,
    TimeInForce,
    WeightRateLimiter,
)


class TestCryptoExchangeIntegration(unittest.TestCase):

    def setUp(self):
        self.limiter = WeightRateLimiter(max_weight_per_window=10, window_seconds=1.0)

    def test_try_consume_and_prune(self):
        self.assertTrue(self.limiter.try_consume(6))
        self.assertEqual(self.limiter.current_weight(), 6)
        self.assertFalse(self.limiter.try_consume(5))  # 6 + 5 = 11 > 10

        time.sleep(1.1)  # Window expires
        self.assertEqual(self.limiter.current_weight(), 0)
        self.assertTrue(self.limiter.try_consume(5))

    def test_async_acquire(self):
        async def run_async_test():
            limiter = WeightRateLimiter(max_weight_per_window=10, window_seconds=0.2)
            limiter.try_consume(10)
            start_time = time.monotonic()
            await limiter.acquire(5)  # Should wait ~0.2s for window to expire
            elapsed = time.monotonic() - start_time
            self.assertGreaterEqual(elapsed, 0.15)

        asyncio.run(run_async_test())

    def test_header_synchronization(self):
        self.limiter.try_consume(2)
        self.assertEqual(self.limiter.current_weight(), 2)

        # Server responds with x-mbx-used-weight-1m = 8
        self.limiter.update_from_header(8)
        self.assertEqual(self.limiter.current_weight(), 8)
        self.assertFalse(self.limiter.try_consume(5))  # 8 + 5 = 13 > 10

    def test_order_payload_stp(self):
        order = CryptoOrderPayload(
            symbol="BTCUSDT",
            side="BUY",
            order_type=OrderType.LIMIT_MAKER,
            quantity=0.5,
            price=50000.0,
            time_in_force=TimeInForce.GTC,
            stp_mode=SelfTradePreventionMode.EXPIRE_MAKER,
            post_only=True,
        )
        payload = order.to_dict()
        self.assertEqual(payload["symbol"], "BTCUSDT")
        self.assertEqual(payload["selfTradePreventionMode"], "EXPIRE_MAKER")
        self.assertEqual(payload["execInst"], "PostOnly")

    def test_rolling_24h_pnl_tracker(self):
        tracker = Rolling24hPnLTracker(window_hours=24.0)
        now = time.time()
        tracker.record_pnl(100.0, timestamp=now - 100)
        tracker.record_pnl(-20.0, timestamp=now - 50)
        tracker.record_pnl(50.0, timestamp=now - 25 * 3600)  # Expired trade (> 24h)

        self.assertEqual(tracker.get_rolling_pnl(), 80.0)

    def test_multi_namespace_manager(self):
        mgr = CryptoExchangeRateLimiter()
        spot = mgr.get_limiter("binance_spot")
        futures = mgr.get_limiter("binance_futures")
        self.assertNotEqual(spot, futures)
        self.assertEqual(spot.max_weight, 1200)
        self.assertEqual(futures.max_weight, 2400)


if __name__ == "__main__":
    unittest.main()
