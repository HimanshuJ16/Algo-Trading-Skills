import unittest
from futures_contract_roll_automation import Configuration, FuturesContractRollAutomationEngine

class TestFuturesContractRollAutomation(unittest.TestCase):
    def test_default_execution(self):
        engine = FuturesContractRollAutomationEngine()
        res = engine.execute({"value": 10})
        self.assertEqual(res["status"], "success")
        self.assertEqual(res["value"], 5.0)

    def test_disabled_execution(self):
        engine = FuturesContractRollAutomationEngine(Configuration(enabled=False))
        res = engine.execute({"value": 10})
        self.assertEqual(res["status"], "disabled")
        
    def test_empty_data(self):
        engine = FuturesContractRollAutomationEngine()
        res = engine.execute({})
        self.assertEqual(res["status"], "success")
        self.assertEqual(res["value"], 0.0)

if __name__ == '__main__':
    unittest.main()
