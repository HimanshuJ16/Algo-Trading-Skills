import unittest
from capital_efficiency_across_cross_margined_strategies import Config, Engine

class TestCapitalEfficiencyAcrossCrossMarginedStrategies(unittest.TestCase):
    def test_init(self):
        engine = Engine(Config())
        self.assertTrue(engine.config.enabled)
        
    def test_run(self):
        engine = Engine(Config())
        self.assertTrue(engine.run())

if __name__ == '__main__':
    unittest.main()
