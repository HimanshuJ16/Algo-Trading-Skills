import unittest
import sys
import os
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from universe_change_detection import Config, Engine

class TestUniverseChangeDetection(unittest.TestCase):
    def test_init(self):
        c = Config("test")
        e = Engine(c)
        self.assertEqual(e.config.name, "test")
    def test_run(self):
        c = Config("test")
        e = Engine(c)
        self.assertTrue(e.run())

if __name__ == "__main__":
    unittest.main()
