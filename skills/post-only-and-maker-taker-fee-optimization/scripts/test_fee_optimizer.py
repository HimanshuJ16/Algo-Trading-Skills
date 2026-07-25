"""
Unit tests for post-only-and-maker-taker-fee-optimization skill.
"""
import unittest
from fee_optimizer import FeeSchedule, MakerTakerFeeOptimizer


class TestMakerTakerFeeOptimizer(unittest.TestCase):

    def setUp(self):
        # Maker: 0.05% (5 bps), Taker: 0.25% (25 bps)
        self.schedule = FeeSchedule(maker_fee_rate=0.0005, taker_fee_rate=0.0025)
        self.optimizer = MakerTakerFeeOptimizer(self.schedule)

    def test_passive_post_only_payload_injection(self):
        # Buy limit below ask -> Accepted without repricing
        res = self.optimizer.prepare_post_only_payload(
            symbol="BTCUSDT",
            action="BUY",
            quantity=1.0,
            limit_price=60000.0,
            best_bid=60000.0,
            best_ask=60010.0,
        )

        self.assertTrue(res.is_accepted)
        self.assertFalse(res.repriced)
        self.assertTrue(res.order_payload["post_only"])
        self.assertEqual(res.order_payload["price"], 60000.0)

        # Fee savings: 60,000 * (0.0025 - 0.0005) = $120.00 saved
        self.assertAlmostEqual(res.fee_saved_usd, 120.0, places=2)

    def test_spread_crossing_passive_repricing(self):
        # Buy limit 60020 >= best_ask 60010 -> Repriced to best_bid 60000
        res = self.optimizer.prepare_post_only_payload(
            symbol="BTCUSDT",
            action="BUY",
            quantity=1.0,
            limit_price=60020.0,
            best_bid=60000.0,
            best_ask=60010.0,
            allow_passive_reprice=True,
        )

        self.assertTrue(res.is_accepted)
        self.assertTrue(res.repriced)
        self.assertEqual(res.new_limit_price, 60000.0)
        self.assertEqual(res.order_payload["price"], 60000.0)

    def test_spread_crossing_rejection_without_reprice(self):
        res = self.optimizer.prepare_post_only_payload(
            symbol="BTCUSDT",
            action="BUY",
            quantity=1.0,
            limit_price=60020.0,
            best_bid=60000.0,
            best_ask=60010.0,
            allow_passive_reprice=False,
        )

        self.assertFalse(res.is_accepted)
        self.assertIn("Post-Only Buy rejected", res.message)


if __name__ == "__main__":
    unittest.main()
