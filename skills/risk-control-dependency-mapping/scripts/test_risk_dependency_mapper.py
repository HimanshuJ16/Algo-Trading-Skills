import unittest
import sys
import os
import importlib
sys.path.insert(0, os.path.abspath(os.path.dirname(__file__)))

module = importlib.import_module("risk_dependency_mapper")
RiskDependencyMapperEngine = getattr(module, "RiskDependencyMapperEngine")
Result = getattr(module, "Result")

class TestRiskDependencyMapperEngine(unittest.TestCase):
    def setUp(self):
        self.engine = RiskDependencyMapperEngine()
        
    def test_execute_true(self):
        res = self.engine.execute(True)
        self.assertTrue(res.success)
        
    def test_execute_false(self):
        res = self.engine.execute(False)
        self.assertFalse(res.success)

if __name__ == '__main__':
    unittest.main()
