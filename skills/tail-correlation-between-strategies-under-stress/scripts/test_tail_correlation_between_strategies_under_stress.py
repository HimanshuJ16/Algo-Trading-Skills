"""
Unit tests for tail-correlation-between-strategies-under-stress skill.

Tests:
1. Legacy Config & Engine instantiation and run method.
2. Unconditional vs lower tail correlation analysis for uncorrelated normal strategies.
3. Diversification breakdown detection for tail-dependent crash strategy returns.
4. Empirical tail dependence and joint crash probability metrics.
5. Multi-strategy tail correlation matrix computation.
"""
import numpy as np
import pandas as pd
import unittest
from tail_correlation_between_strategies_under_stress import (
    Config,
    Engine,
    TailCorrelationAnalyzerEngine,
    TailCorrelationError,
)


class TestTailCorrelationBetweenStrategiesUnderStress(unittest.TestCase):

    def setUp(self):
        np.random.seed(42)
        self.config = Config(tail_quantile=0.10, breakdown_threshold=0.70)
        self.analyzer = TailCorrelationAnalyzerEngine(self.config)

    def test_legacy_engine(self):
        eng = Engine(Config())
        self.assertTrue(eng.config.enabled)
        self.assertTrue(eng.run())

    def test_uncorrelated_strategies_normal_tail(self):
        n = 500
        returns_a = pd.Series(np.random.normal(0.0005, 0.01, n))
        returns_b = pd.Series(np.random.normal(0.0005, 0.01, n))

        res = self.analyzer.analyze_pair(returns_a, returns_b, "Alpha1", "Alpha2")

        self.assertLess(abs(res.unconditional_correlation), 0.20)
        self.assertFalse(res.diversification_breakdown)

    def test_tail_diversification_breakdown_detection(self):
        n = 500
        # Normal returns uncorrelated in normal times, but crash together on downside shocks
        base_a = np.random.normal(0.0005, 0.01, n)
        base_b = np.random.normal(0.0005, 0.01, n)

        # Inject 30 simultaneous downside tail crashes
        crash_indices = list(range(0, 30))
        for idx in crash_indices:
            base_a[idx] = -0.08
            base_b[idx] = -0.09

        returns_a = pd.Series(base_a)
        returns_b = pd.Series(base_b)

        res = self.analyzer.analyze_pair(returns_a, returns_b, "StratA", "StratB")

        self.assertGreater(res.lower_tail_correlation, res.unconditional_correlation)
        self.assertGreaterEqual(res.empirical_tail_dependence, 0.50)
        self.assertTrue(res.diversification_breakdown)
        self.assertIn("DIVERSIFICATION BREAKDOWN WARNING", res.details[0])

    def test_multi_strategy_portfolio_matrix(self):
        n = 100
        df = pd.DataFrame({
            "S1": np.random.normal(0, 0.01, n),
            "S2": np.random.normal(0, 0.01, n),
            "S3": np.random.normal(0, 0.01, n),
        })

        res = self.analyzer.analyze_portfolio_matrix(df)

        self.assertIn("unconditional_matrix", res)
        self.assertIn("lower_tail_matrix", res)
        self.assertEqual(res["unconditional_matrix"].shape, (3, 3))


if __name__ == "__main__":
    unittest.main()
