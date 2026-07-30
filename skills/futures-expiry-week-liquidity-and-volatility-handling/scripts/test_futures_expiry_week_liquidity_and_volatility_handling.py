import unittest
from futures_expiry_week_liquidity_and_volatility_handling import (
    FuturesExpiryRiskHandlerEngine, FuturesOrderBookState, FuturesExpiryRiskReport
)

class TestFuturesExpiryRiskHandlerEngine(unittest.TestCase):

    def setUp(self):
        self.engine = FuturesExpiryRiskHandlerEngine(
            max_spread_ticks_threshold=2.0,
            min_depth_ratio_threshold=0.30,
            mandatory_roll_dbe_cutoff=2
        )

    def test_normal_market_execution_allows_full_size(self):
        book = FuturesOrderBookState(
            symbol="ESH6", days_to_expiration=15, bid_ask_spread_ticks=1.0,
            top_of_book_depth_qty=1000, baseline_average_depth_qty=1000, is_quadruple_witching_week=False
        )
        report = self.engine.audit_expiry_execution_safeguards(book, base_order_qty=100)

        self.assertEqual(report.status, "NORMAL_EXECUTION")
        self.assertTrue(report.is_market_orders_allowed)
        self.assertEqual(report.size_haircut_factor, 1.0)
        self.assertEqual(report.adjusted_max_order_qty, 100)
        self.assertTrue(report.is_new_entry_allowed)

    def test_quad_witching_and_wide_spread_applies_haircut_and_blocks_market(self):
        # Quad witching + Spread = 3.5 ticks + Depth = 200 (20% baseline) -> Haircut 50% & Block Market Orders!
        book = FuturesOrderBookState(
            symbol="ESH6", days_to_expiration=4, bid_ask_spread_ticks=3.5,
            top_of_book_depth_qty=200, baseline_average_depth_qty=1000, is_quadruple_witching_week=True
        )
        report = self.engine.audit_expiry_execution_safeguards(book, base_order_qty=100)

        self.assertEqual(report.status, "EXPIRY_WEEK_RESTRICTED")
        self.assertFalse(report.is_market_orders_allowed)
        self.assertEqual(report.size_haircut_factor, 0.50)
        self.assertEqual(report.adjusted_max_order_qty, 50)
        self.assertTrue(report.is_new_entry_allowed)

    def test_critical_dbe_triggers_mandatory_roll(self):
        # DBE = 1d <= 2d cutoff -> MANDATORY ROLL REQUIRED!
        book = FuturesOrderBookState(
            symbol="ESH6", days_to_expiration=1, bid_ask_spread_ticks=1.5,
            top_of_book_depth_qty=500, baseline_average_depth_qty=1000, is_quadruple_witching_week=False
        )
        report = self.engine.audit_expiry_execution_safeguards(book, base_order_qty=100)

        self.assertEqual(report.status, "MANDATORY_ROLL_REQUIRED")
        self.assertTrue(report.is_mandatory_roll_required)
        self.assertFalse(report.is_new_entry_allowed)

if __name__ == '__main__':
    unittest.main()
