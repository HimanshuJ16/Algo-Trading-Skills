import unittest
from feature_stability_analyzer import FeatureStabilityAnalyzer, FeatureStabilityAnalyzerConfig

class TestFeatureStabilityAnalyzer(unittest.TestCase):
    def test_initialization(self):
        config = FeatureStabilityAnalyzerConfig()
        obj = FeatureStabilityAnalyzer(config)
        self.assertEqual(obj.config.parameter_1, 1.0)

    def test_process(self):
        config = FeatureStabilityAnalyzerConfig(parameter_1=2.0)
        obj = FeatureStabilityAnalyzer(config)
        data = [{"value": 10}, {"value": 20}]
        result = obj.process(data)
        self.assertEqual(result[0]["result"], 20.0)
        self.assertEqual(result[1]["result"], 40.0)

if __name__ == '__main__':
    unittest.main()
