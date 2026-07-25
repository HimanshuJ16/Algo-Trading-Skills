import unittest
from benchmark_portfolio_for_multi_strategy_performance_context import Config, Engine

class TestBenchmarkPortfolioForMultiStrategyPerformanceContext(unittest.TestCase):
    def test_init(self):
        engine = Engine(Config())
        self.assertTrue(engine.config.enabled)
        
    def test_run(self):
        engine = Engine(Config())
        self.assertTrue(engine.run())

if __name__ == '__main__':
    unittest.main()
