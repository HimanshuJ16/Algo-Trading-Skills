import unittest
import numpy as np
from cross_strategy_correlation_monitoring import (
    CrossStrategyCorrelationMonitor, StrategyCorrelationReport, CorrelationPairBreach
)

class TestCrossStrategyCorrelationMonitor(unittest.TestCase):

    def setUp(self):
        self.monitor = CrossStrategyCorrelationMonitor(
            high_correlation_threshold=0.70,
            redundancy_threshold=0.85,
            min_diversification_ratio=1.20
        )
        np.random.seed(42)

    def test_high_correlation_and_redundancy_breaches(self):
        # 60 days of returns for 3 pods
        base_signal = np.random.normal(0.001, 0.01, (60, 1))
        
        # StatArb and TrendFollow share 85%+ correlation (redundant!)
        stat_arb = base_signal + np.random.normal(0, 0.001, (60, 1))
        trend_follow = base_signal + np.random.normal(0, 0.001, (60, 1))
        # Uncorrelated OptionsArb pod
        options_arb = np.random.normal(0.002, 0.015, (60, 1))
        
        pnl_matrix = np.hstack([stat_arb, trend_follow, options_arb])
        names = ["StatArb", "TrendFollow", "OptionsArb"]
        
        report = self.monitor.analyze_strategy_correlations(names, pnl_matrix)
        
        self.assertFalse(report.is_diversification_healthy)
        self.assertGreaterEqual(len(report.high_correlation_breaches), 1)
        
        breach = report.high_correlation_breaches[0]
        self.assertEqual(breach.strategy_a, "StatArb")
        self.assertEqual(breach.strategy_b, "TrendFollow")
        self.assertGreaterEqual(breach.correlation, 0.85)

    def test_healthy_uncorrelated_portfolio(self):
        # 3 independent uncorrelated strategies
        pnl_matrix = np.random.normal(0.001, 0.01, (60, 3))
        names = ["Pod_Alpha", "Pod_Beta", "Pod_Gamma"]
        
        report = self.monitor.analyze_strategy_correlations(names, pnl_matrix)
        self.assertTrue(report.is_diversification_healthy)
        self.assertGreater(report.diversification_ratio, 1.20)
        self.assertEqual(len(report.high_correlation_breaches), 0)

if __name__ == '__main__':
    unittest.main()
