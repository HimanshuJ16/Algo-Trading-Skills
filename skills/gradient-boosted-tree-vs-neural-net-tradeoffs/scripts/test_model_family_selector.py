import unittest
from model_family_selector import ModelFamilySelector, ModelFamilySelectorConfig

class TestModelFamilySelector(unittest.TestCase):
    def test_initialization(self):
        config = ModelFamilySelectorConfig()
        obj = ModelFamilySelector(config)
        self.assertEqual(obj.config.parameter_1, 1.0)

    def test_process(self):
        config = ModelFamilySelectorConfig(parameter_1=2.0)
        obj = ModelFamilySelector(config)
        data = [{"value": 10}, {"value": 20}]
        result = obj.process(data)
        self.assertEqual(result[0]["result"], 20.0)
        self.assertEqual(result[1]["result"], 40.0)

if __name__ == '__main__':
    unittest.main()
