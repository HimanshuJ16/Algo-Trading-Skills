import unittest
from breach_rca_generator import InputData, BreachRcaGenerator

class TestBreachRcaGenerator(unittest.TestCase):
    def setUp(self):
        self.engine = BreachRcaGenerator()
        
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
