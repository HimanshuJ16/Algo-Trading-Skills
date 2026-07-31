import unittest
from post_trade_execution_quality_scorecard import (
    Config, Engine, PostTradeExecutionQualityScorecard,
    ExecutedOrderRecord, SingleOrderMetrics, ExecutionQualityScorecardReport
)

class TestPostTradeExecutionQualityScorecard(unittest.TestCase):

    def setUp(self):
        self.config = Config(enabled=True)
        self.legacy_engine = Engine(self.config)
        self.scorecard_engine = PostTradeExecutionQualityScorecard(self.config)

    def test_legacy_execute_true(self):
        self.assertTrue(self.legacy_engine.execute())

    def test_legacy_execute_false(self):
        engine = Engine(Config(enabled=False))
        self.assertFalse(engine.execute())

    def test_pristine_execution_quality_rating_a(self):
        # BUY 1,000 shares @ $100.05 fill vs $100.00 arrival price, $100.10 VWAP, $100.00 midquote, $0.10 quoted spread
        # IS = (100.05 - 100.00) / 100.00 * 10000 = +5.0 bps
        # VWAP Slippage = (100.05 - 100.10) / 100.10 * 10000 = -4.99 bps (Beat VWAP!)
        # EQR = 2 * (100.05 - 100.00) / 0.10 = 1.0
        # Fill Rate = 100%
        orders = [
            ExecutedOrderRecord(
                order_id="ORD_1", venue="NASDAQ", symbol="AAPL", side="BUY",
                parent_qty=1000.0, executed_qty=1000.0, avg_fill_price=100.05,
                arrival_price=100.00, market_vwap=100.10, arrival_midquote=100.00, arrival_quoted_spread=0.10
            )
        ]

        report = self.scorecard_engine.evaluate_scorecard(orders)
        self.assertEqual(report.status, "SCORECARD_AUDIT_PASSED")
        self.assertEqual(report.composite_scorecard_rating, "A")
        self.assertEqual(report.avg_implementation_shortfall_bps, 5.0)
        self.assertEqual(report.avg_eqr_ratio, 1.0)
        self.assertEqual(report.overall_fill_rate_pct, 100.0)

    def test_poor_execution_quality_rating_f(self):
        # Severe slippage BUY @ $105.00 vs $100.00 arrival (500 bps IS) -> Rating F
        orders = [
            ExecutedOrderRecord(
                order_id="ORD_BAD", venue="DARK_POOL", symbol="XYZ", side="BUY",
                parent_qty=1000.0, executed_qty=500.0, avg_fill_price=105.00,
                arrival_price=100.00, market_vwap=100.00, arrival_midquote=100.00, arrival_quoted_spread=0.10
            )
        ]

        report = self.scorecard_engine.evaluate_scorecard(orders)
        self.assertEqual(report.status, "SCORECARD_AUDIT_FAILED")
        self.assertEqual(report.composite_scorecard_rating, "F")
        self.assertEqual(report.avg_implementation_shortfall_bps, 500.0)

if __name__ == '__main__':
    unittest.main()
