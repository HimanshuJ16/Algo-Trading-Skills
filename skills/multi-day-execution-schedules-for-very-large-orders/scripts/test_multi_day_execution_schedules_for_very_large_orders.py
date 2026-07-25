import unittest
from multi_day_execution_schedules_for_very_large_orders import Config, MainEngine

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
