import unittest
from label_noise_estimator import Config, LabelNoiseEstimator

class TestLabelNoiseEstimator(unittest.TestCase):
    def test_init(self):
        cfg = Config()
        obj = LabelNoiseEstimator(cfg)
        self.assertEqual(obj.config.name, "label-noise-estimation-in-financial-targets")
        
    def test_process(self):
        cfg = Config()
        obj = LabelNoiseEstimator(cfg)
        self.assertTrue(obj.process())

if __name__ == '__main__':
    unittest.main()
