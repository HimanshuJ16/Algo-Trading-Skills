import sys, os
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import unittest
from cloud_cost_anomaly_detector import CloudCostAnomalyDetector, Config

class TestCloudCostAnomalyDetector(unittest.TestCase):
    def test_init(self):
        obj = CloudCostAnomalyDetector(Config("test"))
        self.assertEqual(obj.config.name, "test")

    def test_process(self):
        obj = CloudCostAnomalyDetector()
        self.assertTrue(obj.process())

if __name__ == '__main__':
    unittest.main()
