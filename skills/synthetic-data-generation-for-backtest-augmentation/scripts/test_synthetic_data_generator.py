import unittest
import numpy as np
from synthetic_data_generator import (
    Config, Engine,
    SyntheticDataGenerator, GBMConfig, GARCHConfig, SyntheticValidationReport
)

class TestEngineLegacy(unittest.TestCase):
    def test_init(self):
        config = Config(name="test")
        engine = Engine(config)
        self.assertEqual(engine.config.name, "test")

    def test_run(self):
        config = Config(name="test")
        engine = Engine(config)
        self.assertTrue(engine.run())

class TestSyntheticDataGenerator(unittest.TestCase):
    def setUp(self):
        self.generator = SyntheticDataGenerator(seed=123)

    def test_generate_gbm(self):
        config = GBMConfig(mu=0.05, sigma=0.2, S0=100.0, steps=10)
        paths = self.generator.generate_gbm(config)
        self.assertEqual(len(paths), 11)
        self.assertEqual(paths[0], 100.0)

    def test_generate_garch(self):
        config = GARCHConfig(omega=0.00001, alpha=0.1, beta=0.85, S0=100.0, steps=50)
        prices, sigmas = self.generator.generate_garch(config)
        self.assertEqual(len(prices), 51)
        self.assertEqual(len(sigmas), 51)
        self.assertEqual(prices[0], 100.0)

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

    def test_validate_synthetic_path(self):
        hist_returns = np.random.default_rng(42).normal(0.001, 0.015, size=252)
        synth_returns = np.random.default_rng(123).normal(0.001, 0.015, size=252)
        report = self.generator.validate_synthetic_path(hist_returns, synth_returns)
        self.assertTrue(report.is_statistically_consistent)
        self.assertAlmostEqual(report.volatility_historical, report.volatility_synthetic, delta=0.005)

if __name__ == '__main__':
    unittest.main()
