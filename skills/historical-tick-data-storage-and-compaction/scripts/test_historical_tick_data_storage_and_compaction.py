import unittest
from historical_tick_data_storage_and_compaction import Config, Engine

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
