import unittest
from point_in_time_fundamentals_data_joins import Config, Engine

class TestEngine(unittest.TestCase):
    def test_init(self):
        c = Config("test")
        e = Engine(c)
        self.assertEqual(e.config.name, "test")
        
    def test_process(self):
        c = Config("test")
        e = Engine(c)
        self.assertEqual(e.process(1), 1)

if __name__ == '__main__':
    unittest.main()
