import unittest
from model_version_manager import Config, ModelVersionManager

class TestModelVersionManager(unittest.TestCase):
    def test_init(self):
        obj = ModelVersionManager(Config())
        self.assertIsNotNone(obj)
        
    def test_process(self):
        obj = ModelVersionManager(Config())
        self.assertTrue(obj.process())

if __name__ == "__main__":
    unittest.main()
