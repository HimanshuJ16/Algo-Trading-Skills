import unittest
from asic_market_integrity_rules_automated_trading import AsicMarketIntegrityRulesAutomatedTradingEngine, ComplianceResult

class TestAsicMarketIntegrityRulesAutomatedTrading(unittest.TestCase):
    def setUp(self):
        self.engine = AsicMarketIntegrityRulesAutomatedTradingEngine()
        
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
