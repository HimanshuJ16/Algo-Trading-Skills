import unittest
from japan_fsa_high_speed_trading_registration import JapanFsaHighSpeedTradingRegistrationEngine, ComplianceResult

class TestJapanFsaHighSpeedTradingRegistration(unittest.TestCase):
    def setUp(self):
        self.engine = JapanFsaHighSpeedTradingRegistrationEngine()
        
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
