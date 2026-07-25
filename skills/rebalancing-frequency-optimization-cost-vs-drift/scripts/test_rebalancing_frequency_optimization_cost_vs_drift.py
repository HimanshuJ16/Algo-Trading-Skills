import unittest
from rebalancing_frequency_optimization_cost_vs_drift import Config, Engine

class TestRebalancingFrequencyOptimizationCostVsDrift(unittest.TestCase):
    def test_init(self):
        engine = Engine(Config())
        self.assertTrue(engine.config.enabled)
        
    def test_run(self):
        engine = Engine(Config())
        self.assertTrue(engine.run())

if __name__ == '__main__':
    unittest.main()
