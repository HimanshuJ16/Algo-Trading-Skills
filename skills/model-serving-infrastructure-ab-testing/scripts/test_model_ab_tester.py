import unittest
from model_ab_tester import Config, ModelAbTester

class TestModelAbTester(unittest.TestCase):
    def test_init(self):
        cfg = Config()
        obj = ModelAbTester(cfg)
        self.assertEqual(obj.config.name, "model-serving-infrastructure-ab-testing")
        
    def test_process(self):
        cfg = Config()
        obj = ModelAbTester(cfg)
        self.assertTrue(obj.process())

if __name__ == '__main__':
    unittest.main()
