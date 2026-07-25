import unittest
import sys
import os
import importlib
sys.path.insert(0, os.path.abspath(os.path.dirname(__file__)))

module = importlib.import_module("regulatory_capital_tracker")
RegulatoryCapitalTrackerEngine = getattr(module, "RegulatoryCapitalTrackerEngine")
Result = getattr(module, "Result")

class TestRegulatoryCapitalTrackerEngine(unittest.TestCase):
    def setUp(self):
        self.engine = RegulatoryCapitalTrackerEngine()
        
    def test_execute_true(self):
        res = self.engine.execute(True)
        self.assertTrue(res.success)
        
    def test_execute_false(self):
        res = self.engine.execute(False)
        self.assertFalse(res.success)

if __name__ == '__main__':
    unittest.main()
