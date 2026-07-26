import unittest
import math
from cross_margining_across_asset_classes import (
    CrossMarginingCalculator, AssetClassMarginComponent, CrossMarginAuditReport
)

class TestCrossMarginingCalculator(unittest.TestCase):

    def setUp(self):
        self.calculator = CrossMarginingCalculator(minimum_floor_pct=0.20)
        
        # Register correlation offset between CME Equity Futures (ES) and OCC Index Options (SPX)
        # Strong negative correlation offset (-0.80) due to delta-hedged position
        self.calculator.register_correlation_offset("EQUITY_FUTURES", "INDEX_OPTIONS", -0.80)
        
        self.components = [
            AssetClassMarginComponent("EQUITY_FUTURES", "CME", 500_000.0),
            AssetClassMarginComponent("INDEX_OPTIONS", "OCC", 400_000.0)
        ]

    def test_cross_margining_calculation_and_savings(self):
        # Standalone Sum = $500k + $400k = $900,000
        # M_cross^2 = 500k^2 + 400k^2 + 2*(-0.80)*500k*400k
        # = 250B + 160B - 640B * 0.8 = 410B - 320B = 90B
        # M_cross = sqrt(90B) = ~300,000
        # Savings = $900k - $300k = $600k (66.67% savings)
        report = self.calculator.calculate_cross_margin(self.components)
        
        self.assertEqual(report.total_standalone_margin_usd, 900_000.0)
        self.assertAlmostEqual(report.total_cross_margined_requirement_usd, 300_000.0, delta=100.0)
        self.assertAlmostEqual(report.margin_savings_usd, 600_000.0, delta=100.0)
        self.assertAlmostEqual(report.capital_efficiency_gain_pct, 66.67, places=1)
        self.assertFalse(report.is_floor_applied)

    def test_minimum_risk_floor_enforcement(self):
        # Perfect negative correlation -1.0 with equal amounts ($500k & $500k)
        # Raw cross margin = sqrt(500k^2 + 500k^2 - 2*500k*500k) = 0.0
        # Risk Floor = 20% of $1,000,000 = $200,000
        self.calculator.register_correlation_offset("EQUITY_FUTURES", "INDEX_OPTIONS", -1.0)
        components = [
            AssetClassMarginComponent("EQUITY_FUTURES", "CME", 500_000.0),
            AssetClassMarginComponent("INDEX_OPTIONS", "OCC", 500_000.0)
        ]
        report = self.calculator.calculate_cross_margin(components)
        
        self.assertEqual(report.total_cross_margined_requirement_usd, 200_000.0)
        self.assertTrue(report.is_floor_applied)
        self.assertEqual(report.capital_efficiency_gain_pct, 80.0)

if __name__ == '__main__':
    unittest.main()
