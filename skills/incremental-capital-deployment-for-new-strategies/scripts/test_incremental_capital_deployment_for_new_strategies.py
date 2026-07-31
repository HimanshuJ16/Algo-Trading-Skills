import unittest
from incremental_capital_deployment_for_new_strategies import (
    IncrementalCapitalDeploymentEngine, StrategyDeploymentState, IncrementalDeploymentReport
)

class TestIncrementalCapitalDeploymentEngine(unittest.TestCase):

    def setUp(self):
        self.engine = IncrementalCapitalDeploymentEngine(emergency_max_drawdown_limit_pct=12.0)

    def test_promotion_from_tier_1_to_tier_2(self):
        # Tier 1 (10% = $100k) -> Passes gates (35 days, Sharpe 1.4, Max DD 3.2%, Slippage 1.1x) -> Promoted to Tier 2 (50% = $500k)!
        state = StrategyDeploymentState(
            strategy_id="STAT_ARB_01", current_tier=1, days_in_tier=35,
            realized_sharpe=1.4, realized_max_drawdown_pct=3.2, slippage_vs_backtest_ratio=1.1,
            target_full_capital_usd=1_000_000.0
        )
        report = self.engine.evaluate_strategy_deployment(state)

        self.assertEqual(report.promotion_status, "PROMOTED")
        self.assertEqual(report.new_tier, 2)
        self.assertEqual(report.capital_allocation_pct, 0.50)
        self.assertEqual(report.allocated_capital_usd, 500_000.0)

    def test_emergency_deactivation_on_drawdown_breach(self):
        # Max DD 14.5% > limit 12.0% -> DEMOTED to Tier 0 ($0 allocation)!
        state = StrategyDeploymentState(
            strategy_id="MOMENTUM_02", current_tier=2, days_in_tier=45,
            realized_sharpe=0.5, realized_max_drawdown_pct=14.5, slippage_vs_backtest_ratio=2.0,
            target_full_capital_usd=1_000_000.0
        )
        report = self.engine.evaluate_strategy_deployment(state)

        self.assertEqual(report.promotion_status, "DEMOTED_DRAWDOWN_BREACH")
        self.assertEqual(report.new_tier, 0)
        self.assertEqual(report.allocated_capital_usd, 0.0)

    def test_retained_in_tier_when_gates_not_met(self):
        # Only 15 days in Tier 1 (< 30 days required) -> RETAINED!
        state = StrategyDeploymentState(
            strategy_id="STAT_ARB_01", current_tier=1, days_in_tier=15,
            realized_sharpe=1.4, realized_max_drawdown_pct=3.2, slippage_vs_backtest_ratio=1.1,
            target_full_capital_usd=1_000_000.0
        )
        report = self.engine.evaluate_strategy_deployment(state)

        self.assertEqual(report.promotion_status, "RETAINED_CURRENT_TIER")
        self.assertEqual(report.new_tier, 1)

if __name__ == '__main__':
    unittest.main()
