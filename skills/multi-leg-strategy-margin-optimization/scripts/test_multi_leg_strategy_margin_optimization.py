import unittest
from multi_leg_strategy_margin_optimization import (
    MultiLegStrategyMarginOptimizerEngine, OptionLeg, MultiLegStrategyPayload, MarginOptimizationReport
)

class TestMultiLegStrategyMarginOptimizerEngine(unittest.TestCase):

    def setUp(self):
        self.engine = MultiLegStrategyMarginOptimizerEngine()

    def test_iron_condor_margin_optimization(self):
        # AAPL Iron Condor @ S = $150
        # Bull Put Spread: Short 145 Put @ $2.0, Long 140 Put @ $0.8
        # Bear Call Spread: Short 155 Call @ $2.0, Long 160 Call @ $0.8
        # Net Credit = ($2.0 - $0.8) + ($2.0 - $0.8) = $2.40 per share ($240)
        # Max Width = $5.0. Combo Max Loss = ($5.0 - $2.4) * 100 = $260.
        legs = [
            OptionLeg("PUT", "BUY", strike=140.0, quantity=1, premium=0.8),
            OptionLeg("PUT", "SELL", strike=145.0, quantity=1, premium=2.0),
            OptionLeg("CALL", "SELL", strike=155.0, quantity=1, premium=2.0),
            OptionLeg("CALL", "BUY", strike=160.0, quantity=1, premium=0.8),
        ]
        payload = MultiLegStrategyPayload("AAPL", underlying_price=150.0, legs=legs)

        report = self.engine.optimize_strategy_margin(payload)

        self.assertEqual(report.status, "MARGIN_OPTIMIZED_SUCCESS")
        self.assertEqual(report.strategy_type, "IRON_CONDOR")
        self.assertEqual(report.optimized_combo_margin_usd, 260.0)
        self.assertGreater(report.uncombined_naked_margin_usd, 5000.0)
        self.assertGreater(report.margin_reduction_pct, 90.0)

    def test_vertical_call_spread_margin_optimization(self):
        # Bull Call Spread: Long 150 Call @ $5.0, Short 155 Call @ $2.0
        # Net Debit = $3.0 ($300). Max Loss Combo Margin = ($155 - $150 - 0) * 100 = $500 max loss (or net debit $300).
        legs = [
            OptionLeg("CALL", "BUY", strike=150.0, quantity=1, premium=5.0),
            OptionLeg("CALL", "SELL", strike=155.0, quantity=1, premium=2.0),
        ]
        payload = MultiLegStrategyPayload("AAPL", underlying_price=150.0, legs=legs)

        report = self.engine.optimize_strategy_margin(payload)

        self.assertEqual(report.strategy_type, "VERTICAL_SPREAD")
        self.assertEqual(report.optimized_combo_margin_usd, 500.0)
        self.assertGreater(report.margin_savings_usd, 2000.0)

if __name__ == '__main__':
    unittest.main()
