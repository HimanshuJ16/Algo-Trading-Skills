import unittest
import numpy as np
from synthetic_data_generator import SyntheticDataGenerator, GBMConfig

class TestSyntheticDataGenerator(unittest.TestCase):
    def setUp(self):
        self.generator = SyntheticDataGenerator(seed=123)

    def test_generate_gbm(self):
        config = GBMConfig(mu=0.05, sigma=0.2, S0=100.0, steps=10)
        paths = self.generator.generate_gbm(config)
        self.assertEqual(len(paths), 11)
        self.assertEqual(paths[0], 100.0)

    def test_bootstrap_returns(self):
        hist_returns = np.array([0.01, -0.01, 0.02, -0.02])
        sampled = self.generator.bootstrap_returns(hist_returns, 10)
        self.assertEqual(len(sampled), 10)
        for r in sampled:
            self.assertIn(r, hist_returns)

    def test_block_bootstrap_returns(self):
        hist_returns = np.array([0.01, -0.01, 0.02, -0.02, 0.03, -0.03])
        sampled = self.generator.block_bootstrap_returns(hist_returns, 10, block_size=2)
        self.assertEqual(len(sampled), 10)

if __name__ == '__main__':
    unittest.main()
