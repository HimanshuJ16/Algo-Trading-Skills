"""Unit tests for quantile-regression-for-uncertainty-aware-signals."""
import unittest
import random
from quantile_regression_model import QuantileRegressionSignalModel


class TestQuantileRegressionSignalModel(unittest.TestCase):
    def setUp(self):
        random.seed(42)
        self.model = QuantileRegressionSignalModel(num_features=1, learning_rate=0.05)

    def test_pinball_loss_quantile_ordering(self):
        # Train on target with heteroscedastic noise
        for _ in range(200):
            x = random.uniform(1.0, 3.0)
            y = 2.0 * x + random.gauss(0, 0.5 * x)
            self.model.train_sample([x], y)

        pred = self.model.predict([2.0])

        # Monotonicity check: q10 <= q50 <= q90
        self.assertLessEqual(pred.q10, pred.q50)
        self.assertLessEqual(pred.q50, pred.q90)
        self.assertGreater(pred.uncertainty_width, 0.0)

    def test_confidence_scaling_position_size(self):
        pred = self.model.predict([1.0], max_position_size=1.0)
        self.assertGreaterEqual(abs(pred.confidence_scaled_size), 0.0)
        self.assertLessEqual(abs(pred.confidence_scaled_size), 1.0)


if __name__ == "__main__":
    unittest.main()
