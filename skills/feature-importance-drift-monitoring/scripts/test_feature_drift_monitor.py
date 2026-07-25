import unittest
from feature_drift_monitor import Config, FeatureDriftMonitor

class TestFeatureDriftMonitor(unittest.TestCase):
    def test_init(self):
        obj = FeatureDriftMonitor(Config())
        self.assertIsNotNone(obj)
        
    def test_process(self):
        obj = FeatureDriftMonitor(Config())
        self.assertTrue(obj.process())

if __name__ == "__main__":
    unittest.main()
