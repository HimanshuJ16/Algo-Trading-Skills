import unittest
from sec_rule_15c3_5_risk_controls_us import SecRule15C35RiskControlsUsEngine, ComplianceResult

class TestSecRule15C35RiskControlsUs(unittest.TestCase):
    def setUp(self):
        self.engine = SecRule15C35RiskControlsUsEngine()
        
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
