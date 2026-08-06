import unittest
from strategy_underperformance_remediation_decision_tree import (
    Config, Engine,
    StrategyUnderperformanceRemediationEngine, UnderperformanceTriageMetrics,
    RemediationAction, StrategyRemediationReport
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

class TestStrategyRemediationEngineAdvanced(unittest.TestCase):

    def setUp(self):
        self.engine = StrategyUnderperformanceRemediationEngine(
            min_healthy_sharpe=1.0,
            max_slippage_alpha_ratio=0.50,
            min_peer_sharpe=0.50
        )

    def test_decommission_on_hypothesis_failure(self):
        metrics = UnderperformanceTriageMetrics(
            strategy_id="BROKEN_EDGE_STRAT",
            live_sharpe=-0.2,
            backtest_sharpe=2.0,
            peer_benchmark_sharpe=1.2,
            realized_slippage_bps=5.0,
            expected_alpha_bps=20.0,
            is_data_feed_healthy=True,
            is_alpha_hypothesis_valid=False # Alpha hypothesis broken!
        )
        report = self.engine.evaluate_remediation(metrics)

        self.assertEqual(report.recommended_action, RemediationAction.MANDATORY_STRATEGY_DECOMMISSION)
        self.assertTrue(report.is_decommissioned)
        self.assertIn("NODE_1_HYPOTHESIS_FAILURE", report.triage_path[0])

    def test_execution_optimization_on_excessive_slippage(self):
        metrics = UnderperformanceTriageMetrics(
            strategy_id="HIGH_SLIPPAGE_STRAT",
            live_sharpe=0.4,
            backtest_sharpe=1.8,
            peer_benchmark_sharpe=1.0,
            realized_slippage_bps=15.0, # 15 bps slippage vs 20 bps alpha = 75% > 50%
            expected_alpha_bps=20.0,
            is_data_feed_healthy=True,
            is_alpha_hypothesis_valid=True
        )
        report = self.engine.evaluate_remediation(metrics)

        self.assertEqual(report.recommended_action, RemediationAction.OPTIMIZE_EXECUTION_AND_DATA)
        self.assertFalse(report.is_decommissioned)

    def test_recalibrate_parameters_on_peer_healthy_underperformance(self):
        metrics = UnderperformanceTriageMetrics(
            strategy_id="STALE_PARAM_STRAT",
            live_sharpe=0.6,
            backtest_sharpe=2.1,
            peer_benchmark_sharpe=1.5, # Peers healthy (1.5 > 0.5)
            realized_slippage_bps=2.0,
            expected_alpha_bps=30.0,
            is_data_feed_healthy=True,
            is_alpha_hypothesis_valid=True
        )
        report = self.engine.evaluate_remediation(metrics)

        self.assertEqual(report.recommended_action, RemediationAction.RECALIBRATE_MODEL_PARAMETERS)
        self.assertFalse(report.is_capital_reduced)

if __name__ == '__main__':
    unittest.main()
