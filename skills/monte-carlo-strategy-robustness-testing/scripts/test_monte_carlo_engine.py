"""
Unit tests for monte-carlo-strategy-robustness-testing skill.

Tests:
1. Monte Carlo sequence shuffling execution and quantile computation.
2. Monte Carlo bootstrap resampling execution.
3. Risk of Ruin detection on high drawdown trade series.
"""
import unittest
from monte_carlo_engine import MonteCarloError, MonteCarloResult, MonteCarloRobustnessEngine


class TestMonteCarloRobustnessEngine(unittest.TestCase):

    def setUp(self):
        self.engine = MonteCarloRobustnessEngine(
            initial_capital=100000.0, max_drawdown_limit=0.20, num_simulations=500, seed=42
        )

    def test_robust_strategy_simulation(self):
        # 20 profitable trades with small losses
        trade_returns = [0.03, 0.02, -0.01, 0.04, -0.005, 0.025, 0.03, -0.01, 0.02, 0.015] * 3
        res = self.engine.run_sequence_shuffling(trade_returns)

        self.assertEqual(res.num_simulations, 500)
        self.assertGreater(res.median_final_equity, 100000.0)
        self.assertLess(res.p95_max_drawdown, 0.20)
        self.assertEqual(res.risk_of_ruin_pct, 0.0)
        self.assertTrue(res.is_robust)

    def test_bootstrap_resampling(self):
        trade_returns = [0.02, -0.01, 0.03, -0.015, 0.025, 0.01, -0.005] * 3
        res = self.engine.run_bootstrap_resampling(trade_returns)

        self.assertEqual(res.num_simulations, 500)
        self.assertGreater(res.median_final_equity, 100000.0)

    def test_high_risk_ruin_detection(self):
        # Series with severe loss clusters (-15% per losing trade)
        high_risk_returns = [0.05, -0.15, -0.15, 0.05, -0.15, -0.15, 0.10] * 3
        res = self.engine.run_sequence_shuffling(high_risk_returns)

        # Should breach 20% drawdown limit in many shuffled paths
        self.assertGreater(res.p95_max_drawdown, 0.20)
        self.assertFalse(res.is_robust)


if __name__ == "__main__":
    unittest.main()
