"""Unit tests for online-learning-for-adaptive-signal-models."""
import unittest
import random
from online_adaptive_model import OnlineAdaptiveSignalModel


class TestOnlineAdaptiveSignalModel(unittest.TestCase):
    def setUp(self):
        random.seed(42)
        self.model = OnlineAdaptiveSignalModel(num_features=2, learning_rate=0.05)

    def test_online_learning_convergence(self):
        # Target relationship: y = 2.0 * x0 - 1.5 * x1
        for _ in range(250):
            x0 = random.uniform(-1.0, 1.0)
            x1 = random.uniform(-1.0, 1.0)
            y = (2.0 * x0) - (1.5 * x1) + random.gauss(0, 0.05)
            self.model.update([x0, x1], y)

        report = self.model.audit_performance()
        self.assertTrue(report.is_converged)
        self.assertAlmostEqual(self.model.weights[0], 2.0, delta=0.3)
        self.assertAlmostEqual(self.model.weights[1], -1.5, delta=0.3)

    def test_weight_norm_clipping(self):
        model = OnlineAdaptiveSignalModel(num_features=1, learning_rate=1.0, max_weight_norm=5.0)
        # Large targets pushing weights high
        for _ in range(10):
            model.update([10.0], 100.0)

        report = model.audit_performance()
        norm = abs(model.weights[0])
        self.assertLessEqual(norm, 5.0001)


if __name__ == "__main__":
    unittest.main()
