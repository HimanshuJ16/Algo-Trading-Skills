import unittest
from uk_fca_algorithmic_trading_systems_controls import UkFcaAlgorithmicTradingSystemsControlsEngine, ComplianceResult

class TestUkFcaAlgorithmicTradingSystemsControls(unittest.TestCase):
    def setUp(self):
        self.engine = UkFcaAlgorithmicTradingSystemsControlsEngine()
        
    def test_empty(self):
        res = self.engine.run_checks({})
        self.assertFalse(res.is_compliant)
        
    def test_negative_size(self):
        res = self.engine.run_checks({'size': -10})
        self.assertFalse(res.is_compliant)
        
    def test_valid(self):
        res = self.engine.run_checks({'size': 100})
        self.assertTrue(res.is_compliant)

if __name__ == '__main__':
    unittest.main()
