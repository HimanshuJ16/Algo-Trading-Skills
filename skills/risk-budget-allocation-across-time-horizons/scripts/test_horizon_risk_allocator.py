import unittest
from horizon_risk_allocator import (
    RiskBudgetAllocationEngine, TimeHorizonBucket
)

class TestRiskBudgetAllocationEngine(unittest.TestCase):

    def setUp(self):
        self.engine = RiskBudgetAllocationEngine(total_portfolio_vol_target=0.15)

    def test_valid_risk_budget_allocation(self):
        buckets = [
            TimeHorizonBucket("INTRADAY", 1, 20.0, 0.10, 2.0),
            TimeHorizonBucket("SHORT_TERM", 5, 30.0, 0.12, 5.0),
            TimeHorizonBucket("MEDIUM_TERM", 20, 30.0, 0.15, 8.0),
            TimeHorizonBucket("LONG_TERM", 60, 20.0, 0.18, 10.0),
        ]
        report = self.engine.allocate_risk_budget(buckets)
        self.assertEqual(report.status, "RISK_BUDGET_VALID")
        self.assertFalse(report.over_allocated)
        self.assertEqual(report.total_risk_budget_pct, 100.0)
        self.assertEqual(report.total_horizons, 4)

        # Check position size scalars
        intraday = report.horizon_allocations[0]
        self.assertAlmostEqual(intraday.position_size_scalar, 0.10 / 0.15, places=3)

    def test_over_allocated_budget_flagged(self):
        buckets = [
            TimeHorizonBucket("INTRADAY", 1, 50.0, 0.10, 2.0),
            TimeHorizonBucket("SHORT_TERM", 5, 60.0, 0.12, 5.0),
        ]
        report = self.engine.allocate_risk_budget(buckets)
        self.assertEqual(report.status, "RISK_BUDGET_OVER_ALLOCATED")
        self.assertTrue(report.over_allocated)
        self.assertGreater(report.total_risk_budget_pct, 100.0)

if __name__ == '__main__':
    unittest.main()
