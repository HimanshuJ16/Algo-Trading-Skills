import unittest
from strategy_performance_decay_detection_vs_market_wide_decay import Config, Engine

class TestStrategyPerformanceDecayDetectionVsMarketWideDecay(unittest.TestCase):
    def test_init(self):
        engine = Engine(Config())
        self.assertTrue(engine.config.enabled)
        
    def test_run(self):
        engine = Engine(Config())
        self.assertTrue(engine.run())

if __name__ == '__main__':
    unittest.main()
