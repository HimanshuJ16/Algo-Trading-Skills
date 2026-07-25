import unittest
from risk_unit_test_harness import InputData, RiskUnitTestHarness

class TestRiskUnitTestHarness(unittest.TestCase):
    def setUp(self):
        self.engine = RiskUnitTestHarness()
        
    def test_process_valid(self):
        data = InputData("test", 10.0)
        self.assertTrue(self.engine.process(data))
        
    def test_process_invalid(self):
        data = InputData("test", -10.0)
        self.assertFalse(self.engine.process(data))
        
    def test_history(self):
        data = InputData("test", 10.0)
        self.engine.process(data)
        self.assertEqual(len(self.engine.history), 1)

if __name__ == '__main__':
    unittest.main()
