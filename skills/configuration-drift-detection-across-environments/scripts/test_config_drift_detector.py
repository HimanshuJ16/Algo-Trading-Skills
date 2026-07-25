import sys, os
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import unittest
from config_drift_detector import ConfigDriftDetector, Config

class TestConfigDriftDetector(unittest.TestCase):
    def test_init(self):
        obj = ConfigDriftDetector(Config("test"))
        self.assertEqual(obj.config.name, "test")

    def test_process(self):
        obj = ConfigDriftDetector()
        self.assertTrue(obj.process())

if __name__ == '__main__':
    unittest.main()
