import unittest
from leakage_free_tuner import Config, LeakageFreeTuner

class TestLeakageFreeTuner(unittest.TestCase):
    def test_init(self):
        obj = LeakageFreeTuner(Config())
        self.assertIsNotNone(obj)
        
    def test_process(self):
        obj = LeakageFreeTuner(Config())
        self.assertTrue(obj.process())

if __name__ == "__main__":
    unittest.main()
