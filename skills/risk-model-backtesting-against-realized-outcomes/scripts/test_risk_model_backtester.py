import unittest
import sys
import os
import importlib
sys.path.insert(0, os.path.abspath(os.path.dirname(__file__)))

module = importlib.import_module("risk_model_backtester")
RiskModelBacktesterEngine = getattr(module, "RiskModelBacktesterEngine")
Result = getattr(module, "Result")

class TestRiskModelBacktesterEngine(unittest.TestCase):
    def setUp(self):
        self.engine = RiskModelBacktesterEngine()
        
    def test_execute_true(self):
        res = self.engine.execute(True)
        self.assertTrue(res.success)
        
    def test_execute_false(self):
        res = self.engine.execute(False)
        self.assertFalse(res.success)

if __name__ == '__main__':
    unittest.main()
