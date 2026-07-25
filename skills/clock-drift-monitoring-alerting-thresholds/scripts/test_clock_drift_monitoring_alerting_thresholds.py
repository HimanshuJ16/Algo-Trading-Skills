import unittest
from clock_drift_monitoring_alerting_thresholds import Config, Engine

class TestEngine(unittest.TestCase):
    def test_process_true(self):
        engine = Engine(Config(threshold=1.0))
        self.assertTrue(engine.process(2.0))
        
    def test_process_false(self):
        engine = Engine(Config(threshold=1.0))
        self.assertFalse(engine.process(0.5))

if __name__ == '__main__':
    unittest.main()
