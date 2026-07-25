import unittest
from strategy_performance_attribution_vs_market_beta import StrategyPerformanceAttributionVsMarketBeta, StrategyPerformanceAttributionVsMarketBetaConfig

class TestStrategyPerformanceAttributionVsMarketBeta(unittest.TestCase):
    def test_execute_true(self):
        config = StrategyPerformanceAttributionVsMarketBetaConfig(enabled=True)
        engine = StrategyPerformanceAttributionVsMarketBeta(config)
        self.assertTrue(engine.execute())

    def test_execute_false(self):
        config = StrategyPerformanceAttributionVsMarketBetaConfig(enabled=False)
        engine = StrategyPerformanceAttributionVsMarketBeta(config)
        self.assertFalse(engine.execute())

if __name__ == '__main__':
    unittest.main()
