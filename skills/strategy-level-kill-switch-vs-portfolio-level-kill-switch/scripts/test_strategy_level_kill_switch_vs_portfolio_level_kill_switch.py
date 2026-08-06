import unittest
from strategy_level_kill_switch_vs_portfolio_level_kill_switch import (
    Config, Engine,
    HierarchicalKillSwitchEngine, StrategyState, PortfolioState,
    KillSwitchScope, KillSwitchAction, KillSwitchExecutionReport
)

class TestEngineLegacy(unittest.TestCase):
    def test_init(self):
        config = Config(name="test")
        engine = Engine(config)
        self.assertEqual(engine.config.name, "test")

    def test_run(self):
        config = Config(name="test")
        engine = Engine(config)
        self.assertTrue(engine.run())

class TestHierarchicalKillSwitchEngineAdvanced(unittest.TestCase):

    def setUp(self):
        self.s1 = StrategyState("STAT_ARB", peak_equity_usd=100_000.0, current_equity_usd=100_000.0, drawdown_limit_pct=10.0)
        self.s2 = StrategyState("MOMENTUM", peak_equity_usd=100_000.0, current_equity_usd=100_000.0, drawdown_limit_pct=10.0)
        self.s3 = StrategyState("MEAN_REVERSION", peak_equity_usd=100_000.0, current_equity_usd=100_000.0, drawdown_limit_pct=10.0)

        self.pstate = PortfolioState(total_peak_equity_usd=300_000.0, total_current_equity_usd=300_000.0, portfolio_drawdown_limit_pct=15.0, max_tripped_strategies_limit=2)

        self.engine = HierarchicalKillSwitchEngine(
            portfolio_state=self.pstate,
            strategies=[self.s1, self.s2, self.s3]
        )

    def test_strategy_level_kill_switch_isolation(self):
        # Drop STAT_ARB equity to $88,000 (12% drawdown > 10% limit)
        report = self.engine.evaluate_strategy_kill_switch("STAT_ARB", 88_000.0, action=KillSwitchAction.HARD_LIQUIDATE)

        self.assertTrue(report.is_triggered)
        self.assertEqual(report.scope, KillSwitchScope.STRATEGY_LEVEL)
        self.assertEqual(report.affected_strategies, ["STAT_ARB"])
        self.assertTrue(self.s1.is_tripped)
        # Verify other strategies remain untripped
        self.assertFalse(self.s2.is_tripped)

    def test_master_portfolio_kill_switch_drawdown_trigger(self):
        # Drop total portfolio equity from $300,000 to $240,000 (20% drawdown > 15% limit)
        report = self.engine.evaluate_portfolio_kill_switch(240_000.0, action=KillSwitchAction.HARD_LIQUIDATE)

        self.assertTrue(report.is_triggered)
        self.assertEqual(report.scope, KillSwitchScope.PORTFOLIO_LEVEL)
        self.assertEqual(len(report.affected_strategies), 3) # All 3 strategies halted!
        self.assertTrue(self.pstate.is_portfolio_tripped)

if __name__ == '__main__':
    unittest.main()
