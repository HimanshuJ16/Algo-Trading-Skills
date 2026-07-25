import unittest
from tail_correlation_between_strategies_under_stress import Config, Engine

class TestTailCorrelationBetweenStrategiesUnderStress(unittest.TestCase):
    def test_init(self):
        engine = Engine(Config())
        self.assertTrue(engine.config.enabled)
        
    def test_run(self):
        engine = Engine(Config())
        self.assertTrue(engine.run())

if __name__ == '__main__':
    unittest.main()
