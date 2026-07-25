import unittest
from ensemble_weight_decay import EnsembleWeightDecay, EnsembleWeightDecayConfig

class TestEnsembleWeightDecay(unittest.TestCase):
    def test_initialization(self):
        config = EnsembleWeightDecayConfig()
        obj = EnsembleWeightDecay(config)
        self.assertEqual(obj.config.parameter_1, 1.0)

    def test_process(self):
        config = EnsembleWeightDecayConfig(parameter_1=2.0)
        obj = EnsembleWeightDecay(config)
        data = [{"value": 10}, {"value": 20}]
        result = obj.process(data)
        self.assertEqual(result[0]["result"], 20.0)
        self.assertEqual(result[1]["result"], 40.0)

if __name__ == '__main__':
    unittest.main()
