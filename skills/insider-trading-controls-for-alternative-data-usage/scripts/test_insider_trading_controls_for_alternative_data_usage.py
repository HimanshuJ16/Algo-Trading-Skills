import unittest
import sys
import os

# Add the script dir to path so we can import the module
sys.path.insert(0, os.path.dirname(__file__))
from insider_trading_controls_for_alternative_data_usage import InsiderTradingControlsForAlternativeDataUsageEngine

class TestInsiderTradingControlsForAlternativeDataUsage(unittest.TestCase):
    def setUp(self):
        self.engine = InsiderTradingControlsForAlternativeDataUsageEngine()
        
    def test_valid(self):
        res = self.engine.check({"valid": True})
        self.assertTrue(res.is_compliant)
        
    def test_invalid(self):
        res = self.engine.check({"valid": False})
        self.assertFalse(res.is_compliant)
        
    def test_edge(self):
        res = self.engine.check({})
        self.assertFalse(res.is_compliant)

if __name__ == '__main__':
    unittest.main()
