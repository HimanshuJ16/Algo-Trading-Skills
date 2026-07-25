import unittest
import sys
import os
sys.path.append(os.path.dirname(os.path.abspath(__file__)))
from real_time_greeks_recalculator import RealTimeGreeksRecalculator, RealTimeGreeksRecalculatorConfig

class TestRealTimeGreeksRecalculator(unittest.TestCase):
    def setUp(self):
        self.config = RealTimeGreeksRecalculatorConfig(enabled=True)
        self.instance = RealTimeGreeksRecalculator(self.config)

    def test_initialization(self):
        self.assertTrue(self.instance.config.enabled)

    def test_execute(self):
        result = self.instance.execute({"mock": "data"})
        self.assertTrue(result)

    def test_execute_disabled(self):
        self.instance.config.enabled = False
        result = self.instance.execute({"mock": "data"})
        self.assertFalse(result)

if __name__ == '__main__':
    unittest.main()
