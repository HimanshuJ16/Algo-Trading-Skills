import unittest
from auction_only_order_types_for_illiquid_names import (
    IlliquidAuctionExecutionEngine,
    IlliquidExecutionConfig,
    OrderType
)

class TestIlliquidAuctionExecutionEngine(unittest.TestCase):
    def setUp(self):
        self.engine = IlliquidAuctionExecutionEngine()

    def test_severe_illiquidity_100pct_loc(self):
        # Order is 10% of ADV (> 5% threshold)
        plan = self.engine.generate_routing_plan("MICROCAP", total_qty=10000, average_daily_volume=100000)
        
        self.assertEqual(plan.continuous_qty, 0)
        self.assertEqual(plan.auction_qty, 10000)
        self.assertEqual(plan.auction_order_type, OrderType.LIMIT_ON_CLOSE)
        self.assertIn("Severe", plan.reason)

    def test_moderate_illiquidity_hybrid(self):
        # Order is 2.5% of ADV (between 1% and 5%)
        plan = self.engine.generate_routing_plan("MIDCAP", total_qty=25000, average_daily_volume=1000000)
        
        self.assertEqual(plan.continuous_qty, 12500) # 50%
        self.assertEqual(plan.auction_qty, 12500)    # 50%
        self.assertEqual(plan.auction_order_type, OrderType.LIMIT_ON_CLOSE)
        self.assertIn("Moderate", plan.reason)

    def test_liquid_100pct_continuous(self):
        # Order is 0.1% of ADV (< 1% threshold)
        plan = self.engine.generate_routing_plan("MEGA", total_qty=10000, average_daily_volume=10000000)
        
        self.assertEqual(plan.continuous_qty, 10000)
        self.assertEqual(plan.auction_qty, 0)
        self.assertIn("Liquid", plan.reason)

    def test_zero_adv_raises_error(self):
        with self.assertRaises(ValueError):
            self.engine.generate_routing_plan("ERR", total_qty=1000, average_daily_volume=0)

if __name__ == '__main__':
    unittest.main()
