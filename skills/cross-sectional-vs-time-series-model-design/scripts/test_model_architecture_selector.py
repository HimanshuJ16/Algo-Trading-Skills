import unittest
from model_architecture_selector import Config, ModelArchitectureSelector

class TestModelArchitectureSelector(unittest.TestCase):
    def test_init(self):
        obj = ModelArchitectureSelector(Config())
        self.assertIsNotNone(obj)
        
    def test_process(self):
        obj = ModelArchitectureSelector(Config())
        self.assertTrue(obj.process())

if __name__ == "__main__":
    unittest.main()
