import unittest
from regulatory_capital_tracker import (
    RegulatoryCapitalTrackerEngine, Result, CapitalComponents,
    CapitalRequirementSpec, CapitalStatusReport
)

class TestRegulatoryCapitalTrackerEngine(unittest.TestCase):

    def setUp(self):
        self.spec = CapitalRequirementSpec(
            jurisdiction="SEC_15C3_1",
            base_minimum_capital=250000.0,
            variable_risk_weighted_req=50000.0,
            warning_buffer_pct=1.20
        )
        self.engine = RegulatoryCapitalTrackerEngine(self.spec)

    def test_legacy_execute_true(self):
        res = self.engine.execute(True)
        self.assertTrue(res.success)

    def test_legacy_execute_false(self):
        res = self.engine.execute(False)
        self.assertFalse(res.success)

    def test_compliant_capital_adequacy(self):
        components = CapitalComponents(
            liquid_assets=1000000.0,
            illiquid_deductions=50000.0,
            total_liabilities=500000.0,
            subordinated_debt=100000.0
        )
        # Net capital = (1,000,000 + 100,000) - (500,000 + 50,000) = 550,000
        # Required = 250,000 + 50,000 = 300,000
        # Warning threshold = 300,000 * 1.20 = 360,000
        # 550,000 > 360,000 -> COMPLIANT
        report = self.engine.evaluate_capital_adequacy(components)
        self.assertEqual(report.status, "COMPLIANT")
        self.assertTrue(report.is_compliant)
        self.assertFalse(report.is_warning)
        self.assertEqual(report.net_capital_available, 550000.0)
        self.assertEqual(report.capital_headroom, 250000.0)

    def test_warning_buffer_breach(self):
        components = CapitalComponents(
            liquid_assets=800000.0,
            illiquid_deductions=50000.0,
            total_liabilities=430000.0,
            subordinated_debt=0.0
        )
        # Net capital = 800,000 - 480,000 = 320,000
        # Required = 300,000
        # Warning threshold = 360,000
        # 300,000 <= 320,000 < 360,000 -> WARNING_BUFFER_BREACHED
        report = self.engine.evaluate_capital_adequacy(components)
        self.assertEqual(report.status, "WARNING_BUFFER_BREACHED")
        self.assertTrue(report.is_compliant)
        self.assertTrue(report.is_warning)

    def test_capital_deficit(self):
        components = CapitalComponents(
            liquid_assets=500000.0,
            illiquid_deductions=50000.0,
            total_liabilities=300000.0,
            subordinated_debt=0.0
        )
        # Net capital = 500,000 - 350,000 = 150,000
        # Required = 300,000
        # 150,000 < 300,000 -> CAPITAL_DEFICIT
        report = self.engine.evaluate_capital_adequacy(components)
        self.assertEqual(report.status, "CAPITAL_DEFICIT")
        self.assertFalse(report.is_compliant)
        self.assertEqual(report.capital_headroom, -150000.0)

if __name__ == '__main__':
    unittest.main()
