import unittest
from model_card_generator import ModelCardGenerator, ModelCardGeneratorConfig

class TestModelCardGenerator(unittest.TestCase):
    def test_initialization(self):
        config = ModelCardGeneratorConfig()
        obj = ModelCardGenerator(config)
        self.assertEqual(obj.config.parameter_1, 1.0)

    def test_process(self):
        config = ModelCardGeneratorConfig(parameter_1=2.0)
        obj = ModelCardGenerator(config)
        data = [{"value": 10}, {"value": 20}]
        result = obj.process(data)
        self.assertEqual(result[0]["result"], 20.0)
        self.assertEqual(result[1]["result"], 40.0)

if __name__ == '__main__':
    unittest.main()
