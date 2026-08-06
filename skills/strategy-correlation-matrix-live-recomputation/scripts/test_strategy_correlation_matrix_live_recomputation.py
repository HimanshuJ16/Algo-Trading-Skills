import unittest
import numpy as np
import pandas as pd
from strategy_correlation_matrix_live_recomputation import (
    StrategyCorrelationMatrixLiveRecomputation,
    StrategyCorrelationMatrixLiveRecomputationConfig,
    StrategyCorrelationMatrixEngine, LiveCorrelationMatrixReport, StrategyCorrelationPair
)

class TestStrategyCorrelationMatrixLegacy(unittest.TestCase):
    def test_execute_true(self):
        config = StrategyCorrelationMatrixLiveRecomputationConfig(enabled=True)
        engine = StrategyCorrelationMatrixLiveRecomputation(config)
        self.assertTrue(engine.execute())

    def test_execute_false(self):
        config = StrategyCorrelationMatrixLiveRecomputationConfig(enabled=False)
        engine = StrategyCorrelationMatrixLiveRecomputation(config)
        self.assertFalse(engine.execute())

class TestStrategyCorrelationMatrixEngineAdvanced(unittest.TestCase):

    def setUp(self):
        self.engine = StrategyCorrelationMatrixEngine(
            ewma_span=30,
            shrinkage_delta=0.10,
            high_correlation_threshold=0.70,
            max_avg_correlation_threshold=0.50
        )
        # Create synthetic returns: Strat_A & Strat_B highly correlated; Strat_C uncorrelated
        np.random.seed(42)
        base = np.random.normal(0.001, 0.01, 100)
        returns = pd.DataFrame({
            "Strat_A": base + np.random.normal(0, 0.002, 100),
            "Strat_B": base + np.random.normal(0, 0.002, 100), # Highly correlated with Strat_A
            "Strat_C": np.random.normal(0.0005, 0.01, 100)      # Uncorrelated
        })
        self.returns_df = returns

    def test_live_correlation_matrix_computation(self):
        report = self.engine.compute_live_correlation_matrix(self.returns_df, "2026-08-06T08:00:00Z")

        self.assertEqual(len(report.strategy_names), 3)
        self.assertEqual(len(report.correlation_matrix), 3)

        # Check diagonal elements are 1.0
        self.assertEqual(report.correlation_matrix[0][0], 1.0)
        self.assertEqual(report.correlation_matrix[1][1], 1.0)

        # Check Strat_A <-> Strat_B correlation is high (> 0.70)
        ab_corr = report.correlation_matrix[0][1]
        self.assertGreater(ab_corr, 0.70)

        # Verify high correlation alert triggered
        self.assertEqual(len(report.high_correlation_pairs), 1)
        self.assertEqual(report.high_correlation_pairs[0].strategy_a, "Strat_A")
        self.assertEqual(report.high_correlation_pairs[0].strategy_b, "Strat_B")
        self.assertTrue(report.is_portfolio_diversification_compromised)

if __name__ == '__main__':
    unittest.main()
