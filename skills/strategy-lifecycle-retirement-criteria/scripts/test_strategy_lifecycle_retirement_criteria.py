import unittest
from strategy_lifecycle_retirement_criteria import (
    StrategyLifecycleRetirementCriteria,
    StrategyLifecycleRetirementCriteriaConfig,
    StrategyLifecycleRetirementEngine, StrategyPerformanceMetrics,
    StrategyRetirementReport, RetirementDecision
)

class TestStrategyLifecycleLegacy(unittest.TestCase):
    def test_execute_true(self):
        config = StrategyLifecycleRetirementCriteriaConfig(enabled=True)
        engine = StrategyLifecycleRetirementCriteria(config)
        self.assertTrue(engine.execute())

    def test_execute_false(self):
        config = StrategyLifecycleRetirementCriteriaConfig(enabled=False)
        engine = StrategyLifecycleRetirementCriteria(config)
        self.assertFalse(engine.execute())

class TestStrategyLifecycleRetirementEngineAdvanced(unittest.TestCase):

    def setUp(self):
        self.engine = StrategyLifecycleRetirementEngine(
            min_live_information_ratio=0.50,
            max_drawdown_multiplier=1.50,
            min_ic_t_stat=1.96,
            max_allowed_performance_drift_pct=-40.0
        )

    def test_active_healthy_strategy(self):
        metrics = StrategyPerformanceMetrics(
            strategy_id="STAT_ARB_HEALTHY",
            backtest_sharpe=2.0,
            backtest_max_drawdown_pct=10.0,
            live_sharpe=1.8,
            live_max_drawdown_pct=11.0,           # 11% <= 15% (1.5x of 10%)
            live_information_ratio=1.2,           # 1.2 >= 0.50
            live_ic_t_stat=2.5,                   # 2.5 >= 1.96
            live_realized_annual_return_pct=18.0,
            backtest_annual_return_pct=20.0       # Drift = -10% > -40%
        )
        report = self.engine.evaluate_strategy(metrics)

        self.assertEqual(report.decision, RetirementDecision.ACTIVE_HEALTHY)
        self.assertFalse(report.is_retired)
        self.assertEqual(len(report.breached_criteria), 0)

    def test_mandatory_retirement_on_multiple_breaches(self):
        metrics = StrategyPerformanceMetrics(
            strategy_id="FAILED_ALPHA_STRAT",
            backtest_sharpe=2.5,
            backtest_max_drawdown_pct=8.0,        # Allowed DD = 12%
            live_sharpe=-0.5,
            live_max_drawdown_pct=20.0,           # BREACH! 20% > 12%
            live_information_ratio=-0.2,          # BREACH! -0.2 < 0.50
            live_ic_t_stat=0.4,                   # BREACH! 0.4 < 1.96
            live_realized_annual_return_pct=-5.0,
            backtest_annual_return_pct=25.0       # BREACH! -120% drift
        )
        report = self.engine.evaluate_strategy(metrics)

        self.assertEqual(report.decision, RetirementDecision.MANDATORY_RETIREMENT)
        self.assertTrue(report.is_retired)
        self.assertEqual(len(report.breached_criteria), 4)

if __name__ == '__main__':
    unittest.main()
