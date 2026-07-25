"""Unit tests for adversarial-robustness-of-trading-signals."""
import unittest
import numpy as np
from signal_adversarial_tester import SignalAdversarialTester, AdversarialRobustnessConfig


class DummyFragileModel:
    """A highly sensitive model that flips prediction if feature 0 > 0.5"""
    def predict(self, X: np.ndarray) -> np.ndarray:
        return (X[:, 0] > 0.5).astype(int)

class DummyRobustModel:
    """A robust model that is insensitive to small perturbations"""
    def predict(self, X: np.ndarray) -> np.ndarray:
        return (X[:, 0] > 5.0).astype(int)


class TestSignalAdversarialTester(unittest.TestCase):
    def setUp(self):
        # Set seeds for deterministic tests
        np.random.seed(42)

    def test_robust_model_passes(self):
        config = AdversarialRobustnessConfig(epsilon=0.01, flip_tolerance_pct=5.0)
        tester = SignalAdversarialTester(config)
        
        # Features range from 0 to 10
        X_clean = np.random.uniform(0, 10, size=(100, 2))
        
        model = DummyRobustModel()
        report = tester.evaluate_model(model.predict, X_clean)
        
        # At epsilon 0.01, a threshold of 5.0 is hard to cross by chance for many samples
        self.assertTrue(report.is_robust)
        self.assertLessEqual(report.vulnerability_score_pct, 5.0)

    def test_fragile_model_fails_under_worst_case_noise(self):
        # Force worst case boundary noise
        config = AdversarialRobustnessConfig(epsilon=0.1, flip_tolerance_pct=2.0, noise_type="worst_case_sign")
        tester = SignalAdversarialTester(config)
        
        # Create data clustered exactly around the decision boundary (0.5)
        # This makes the model highly fragile to perturbations
        X_clean = np.full((100, 1), 0.5)
        
        model = DummyFragileModel()
        report = tester.evaluate_model(model.predict, X_clean)
        
        # Because we push features by epsilon=0.1 in both directions, 
        # roughly ~50% of the signals will flip below the 0.5 threshold.
        self.assertFalse(report.is_robust)
        self.assertGreater(report.vulnerability_score_pct, 2.0)
        self.assertIn("VULNERABLE", report.message)

    def test_invalid_noise_type(self):
        config = AdversarialRobustnessConfig(noise_type="invalid_type")
        tester = SignalAdversarialTester(config)
        X_clean = np.ones((10, 2))
        
        with self.assertRaises(ValueError):
            tester.evaluate_model(lambda x: x, X_clean)

if __name__ == '__main__':
    unittest.main()
