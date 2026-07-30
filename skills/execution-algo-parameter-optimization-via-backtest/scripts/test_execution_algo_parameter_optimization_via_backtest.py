import unittest
from execution_algo_parameter_optimization_via_backtest import (
    ExecutionAlgoOptimizerEngine, AlgoParameterConfig, HistoricalTradeSample, AlgoOptimizationAuditReport
)

class TestExecutionAlgoOptimizerEngine(unittest.TestCase):

    def setUp(self):
        self.engine = ExecutionAlgoOptimizerEngine(
            is_volatility_penalty_weight=0.5,
            incomplete_fill_penalty_weight=50.0
        )

        # 3 Grid candidates
        self.grid = [
            AlgoParameterConfig(max_participation_rate=0.05, risk_aversion_lambda=1e-5, peg_offset_ticks=0),
            AlgoParameterConfig(max_participation_rate=0.15, risk_aversion_lambda=1e-5, peg_offset_ticks=1),
            AlgoParameterConfig(max_participation_rate=0.25, risk_aversion_lambda=1e-4, peg_offset_ticks=2)
        ]

        # 3 Trade samples
        self.samples = [
            HistoricalTradeSample("TR_01", "AAPL", 10_000, 185.00, 100_000.0, 0.015, [185.00, 185.10]),
            HistoricalTradeSample("TR_02", "AAPL", 20_000, 185.20, 100_000.0, 0.018, [185.20, 185.35]),
            HistoricalTradeSample("TR_03", "AAPL", 15_000, 184.90, 100_000.0, 0.014, [184.90, 185.05])
        ]

    def test_grid_search_optimization_selects_lowest_utility_score(self):
        report = self.engine.optimize_algo_parameters(
            algo_name="IS_ALMGREN_CHRISS", symbol="AAPL",
            grid_search_configs=self.grid, trade_samples=self.samples
        )

        self.assertEqual(report.total_grid_candidates_evaluated, 3)
        self.assertEqual(report.total_trade_samples_tested, 3)
        self.assertIsNotNone(report.optimal_config)
        self.assertLess(report.optimal_utility_score, report.all_candidate_results[-1].utility_score)

if __name__ == '__main__':
    unittest.main()
