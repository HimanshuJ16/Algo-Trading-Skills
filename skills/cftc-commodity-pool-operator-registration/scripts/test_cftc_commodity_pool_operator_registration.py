import unittest
from cftc_commodity_pool_operator_registration import CftcCommodityPoolOperatorRegistrationEngine, ComplianceResult

class TestCftcCommodityPoolOperatorRegistration(unittest.TestCase):
    def setUp(self):
        self.engine = CftcCommodityPoolOperatorRegistrationEngine()
        
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
