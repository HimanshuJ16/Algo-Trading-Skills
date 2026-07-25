import unittest
from execution_algo_behavior_under_halted_instrument import Config, MainEngine

class TestMainEngine(unittest.TestCase):
    def setUp(self):
        self.config = Config(param1=2.0)
        self.engine = MainEngine(self.config)
        
    def test_execute(self):
        self.assertTrue(self.engine.execute())
        
    def test_process_data(self):
        self.assertEqual(self.engine.process_data(10.0), 20.0)

if __name__ == '__main__':
    unittest.main()
