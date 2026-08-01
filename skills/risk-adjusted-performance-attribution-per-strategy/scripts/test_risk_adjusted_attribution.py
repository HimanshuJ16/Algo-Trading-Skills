import unittest
from risk_adjusted_attribution import (
    RiskAdjustedPerformanceAttributionEngine, StrategyReturns
)

class TestRiskAdjustedPerformanceAttributionEngine(unittest.TestCase):

    def setUp(self):
        self.engine = RiskAdjustedPerformanceAttributionEngine(risk_free_rate_annual=0.05)

    def test_sharpe_and_sortino_computed(self):
        # Positive returns strategy with slight variation and some negative days
        strat = StrategyReturns(
            strategy_id="TREND_FOLLOW_01",
            daily_returns=[0.003, 0.002, 0.004, -0.001, 0.005, 0.002] * 42  # 252 days
        )
        report = self.engine.compute_strategy_attribution([strat])
        attr = report.strategy_attributions[0]

        self.assertEqual(report.status, "ATTRIBUTION_COMPLETE")
        self.assertGreater(attr.annualized_return, 0.3)  # should be positive
        self.assertGreater(attr.sharpe_ratio, 1.0)
        self.assertGreater(attr.sortino_ratio, 0.0)
        self.assertGreater(attr.max_drawdown, 0.0)  # has a negative day

    def test_multi_strategy_risk_contribution(self):
        # High-vol strategy vs low-vol strategy
        strat_high_vol = StrategyReturns(
            strategy_id="HIGH_VOL",
            daily_returns=[0.02, -0.015, 0.018, -0.012, 0.01] * 50  # 250 days
        )
        strat_low_vol = StrategyReturns(
            strategy_id="LOW_VOL",
            daily_returns=[0.001, 0.0005, 0.0012, 0.0008, 0.0006] * 50
        )
        report = self.engine.compute_strategy_attribution(
            [strat_high_vol, strat_low_vol],
            weights=[0.5, 0.5]
        )
        self.assertEqual(len(report.strategy_attributions), 2)
        high_vol_attr = report.strategy_attributions[0]
        low_vol_attr = report.strategy_attributions[1]

        # High vol strategy should contribute more risk
        self.assertGreater(high_vol_attr.risk_contribution_pct, low_vol_attr.risk_contribution_pct)
        self.assertGreater(high_vol_attr.annualized_volatility, low_vol_attr.annualized_volatility)

    def test_max_drawdown_detected(self):
        # Strategy with a drawdown
        returns = [0.01] * 20 + [-0.05] * 5 + [0.01] * 20
        strat = StrategyReturns(strategy_id="DD_TEST", daily_returns=returns)
        report = self.engine.compute_strategy_attribution([strat])
        attr = report.strategy_attributions[0]
        self.assertGreater(attr.max_drawdown, 0.10)  # Should have >10% drawdown

if __name__ == '__main__':
    unittest.main()
