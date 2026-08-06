import unittest
import numpy as np
from strategy_specific_vs_shared_risk_budget_allocation import (
    Config, Engine,
    StrategySpecificVsSharedRiskBudgetEngine, StrategyRiskBudgetSpec,
    StrategyRiskBreakdown, PortfolioRiskBudgetAllocationReport
)

class TestEngineLegacy(unittest.TestCase):
    def test_init(self):
        engine = Engine(Config())
        self.assertTrue(engine.config.enabled)

    def test_run(self):
        engine = Engine(Config())
        self.assertTrue(engine.run())

class TestStrategySpecificVsSharedRiskBudgetEngineAdvanced(unittest.TestCase):

    def setUp(self):
        self.spec1 = StrategyRiskBudgetSpec("STAT_ARB", target_capital_usd=500_000.0, max_standalone_volatility_pct=15.0, max_shared_risk_contribution_pct=40.0)
        self.spec2 = StrategyRiskBudgetSpec("TREND", target_capital_usd=500_000.0, max_standalone_volatility_pct=25.0, max_shared_risk_contribution_pct=70.0)

        self.engine = StrategySpecificVsSharedRiskBudgetEngine([self.spec1, self.spec2])

    def test_euler_decomposition_and_risk_budget_audit(self):
        # 2x2 daily covariance matrix
        cov = np.array([
            [0.0001, 0.00002],
            [0.00002, 0.0004]
        ])
        report = self.engine.evaluate_risk_budgets(cov, ["STAT_ARB", "TREND"])

        self.assertTrue(report.is_euler_decomposition_valid)
        self.assertEqual(report.total_portfolio_capital_usd, 1_000_000.0)
        self.assertEqual(len(report.strategy_breakdown), 2)
        # Verify component risk sums to 100%
        total_comp_risk = sum(b.component_risk_pct for b in report.strategy_breakdown.values())
        self.assertAlmostEqual(total_comp_risk, 100.0, places=2)

    def test_standalone_and_shared_risk_breach_detection(self):
        # High volatility covariance for STAT_ARB causing breach
        cov_breach = np.array([
            [0.0009, 0.0001], # Standalone vol ~47% > 15% limit
            [0.0001, 0.0001]
        ])
        report = self.engine.evaluate_risk_budgets(cov_breach, ["STAT_ARB", "TREND"])

        self.assertIn("STAT_ARB", report.breached_strategies)
        stat_breakdown = report.strategy_breakdown["STAT_ARB"]
        self.assertTrue(stat_breakdown.standalone_limit_breached)
        self.assertLess(stat_breakdown.recommended_capital_adjustment_factor, 1.0)

if __name__ == '__main__':
    unittest.main()
