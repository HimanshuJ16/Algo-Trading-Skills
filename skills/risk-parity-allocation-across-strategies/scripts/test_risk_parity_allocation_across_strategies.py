import unittest
from risk_parity_allocation_across_strategies import (
    RiskParityAllocationAcrossStrategies, RiskParityAllocationAcrossStrategiesConfig,
    RiskParityAllocationEngine, StrategyRiskData, RiskParityReport
)

class TestRiskParityLegacy(unittest.TestCase):

    def test_execute_true(self):
        config = RiskParityAllocationAcrossStrategiesConfig(enabled=True)
        engine = RiskParityAllocationAcrossStrategies(config)
        self.assertTrue(engine.execute())

    def test_execute_false(self):
        config = RiskParityAllocationAcrossStrategiesConfig(enabled=False)
        engine = RiskParityAllocationAcrossStrategies(config)
        self.assertFalse(engine.execute())

class TestRiskParityAllocationEngine(unittest.TestCase):

    def setUp(self):
        self.engine = RiskParityAllocationEngine(max_allowed_risk_error_pct=5.0)

    def test_inverse_volatility_weighting_diagonal(self):
        # 3 strategies with vols 10%, 20%, 30%
        s1 = StrategyRiskData("LOW_RISK", 0.10)
        s2 = StrategyRiskData("MED_RISK", 0.20)
        s3 = StrategyRiskData("HIGH_RISK", 0.30)

        report = self.engine.compute_risk_parity_allocation([s1, s2, s3], total_capital_usd=1000000.0)

        self.assertEqual(report.status, "RISK_PARITY_BALANCED")
        self.assertTrue(report.is_risk_balanced)

        # Low risk strategy gets highest capital weight (1/0.10 = 10, 1/0.20 = 5, 1/0.30 = 3.333 -> sum = 18.333 -> w1 = 10/18.333 ~ 54.5%)
        w1 = report.allocations[0].weight
        w2 = report.allocations[1].weight
        w3 = report.allocations[2].weight

        self.assertGreater(w1, w2)
        self.assertGreater(w2, w3)
        self.assertAlmostEqual(w1 + w2 + w3, 1.0, places=2)

    def test_full_covariance_matrix_allocation(self):
        s1 = StrategyRiskData("EQUITY_LONG_SHORT", 0.15)
        s2 = StrategyRiskData("CTA_TREND", 0.10)

        # Covariance matrix for 2 assets with correlation = 0.2
        # var1 = 0.15^2 = 0.0225, var2 = 0.10^2 = 0.0100, cov = 0.2 * 0.15 * 0.10 = 0.003
        cov = [
            [0.0225, 0.0030],
            [0.0030, 0.0100]
        ]
        report = self.engine.compute_risk_parity_allocation([s1, s2], total_capital_usd=500000.0, covariance_matrix=cov)

        self.assertEqual(len(report.allocations), 2)
        self.assertGreater(report.allocations[1].allocated_capital_usd, report.allocations[0].allocated_capital_usd)
        self.assertLess(report.max_risk_parity_error_pct, 10.0)

    def test_empty_strategies_raises_error(self):
        with self.assertRaises(ValueError):
            self.engine.compute_risk_parity_allocation([])

if __name__ == '__main__':
    unittest.main()
