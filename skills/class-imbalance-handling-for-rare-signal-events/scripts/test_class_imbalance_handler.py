import unittest
from class_imbalance_handler import Config, ClassImbalanceHandler

class TestClassImbalanceHandler(unittest.TestCase):
    def test_init(self):
        obj = ClassImbalanceHandler(Config())
        self.assertIsNotNone(obj)
        
    def test_process(self):
        obj = ClassImbalanceHandler(Config())
        self.assertTrue(obj.process())

if __name__ == "__main__":
    unittest.main()
