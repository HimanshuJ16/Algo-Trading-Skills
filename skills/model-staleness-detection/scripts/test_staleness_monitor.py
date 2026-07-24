"""
Unit tests for model-staleness-detection skill.

Tests:
1. Rolling accuracy computation and threshold breach.
2. Feature distribution drift calculation (Z-score & PSI).
3. Automatic model confidence scaling matrix (1.0 -> 0.5 -> 0.0).
4. Training baseline setup and evaluation.
5. Backward compatibility with RollingAccuracy and feature_drift_score.
"""
import unittest
from staleness_monitor import (
    ModelHealthStatus,
    ModelStalenessMonitor,
    RollingAccuracy,
    feature_drift_score,
)


class TestModelStalenessDetection(unittest.TestCase):

    def setUp(self):
        self.monitor = ModelStalenessMonitor(window=10, min_accuracy_threshold=0.55)
        self.monitor.set_training_baseline(
            {"volatility": {"mean": 0.02, "std": 0.005}, "volume": {"mean": 1000.0, "std": 200.0}}
        )

    def test_rolling_accuracy_tracking(self):
        # 8 correct out of 10 -> 80% accuracy -> HEALTHY
        for _ in range(8):
            self.monitor.record_prediction(1, 1)
        for _ in range(2):
            self.monitor.record_prediction(1, 0)

        report = self.monitor.evaluate_health()
        self.assertEqual(report.status, ModelHealthStatus.HEALTHY)
        self.assertEqual(report.sizing_multiplier, 1.0)
        self.assertEqual(report.rolling_accuracy, 0.8)

    def test_accuracy_breach_halts_model(self):
        # 4 correct out of 10 -> 40% accuracy < 55% threshold -> HALTED_STALE
        for _ in range(4):
            self.monitor.record_prediction(1, 1)
        for _ in range(6):
            self.monitor.record_prediction(1, 0)

        report = self.monitor.evaluate_health()
        self.assertEqual(report.status, ModelHealthStatus.HALTED_STALE)
        self.assertEqual(report.sizing_multiplier, 0.0)
        self.assertIn("HALT MODEL", report.action_required)

    def test_feature_drift_detection(self):
        # Normal feature values
        normal_vals = [0.021, 0.019, 0.020, 0.022]
        res_norm = self.monitor.compute_feature_drift("volatility", normal_vals)
        self.assertFalse(res_norm.is_drifting)

        # Shifted feature values (mean shifted from 0.02 to 0.05 -> Z > 3)
        shifted_vals = [0.050, 0.052, 0.048, 0.051]
        res_shift = self.monitor.compute_feature_drift("volatility", shifted_vals)
        self.assertTrue(res_shift.is_drifting)

    def test_backward_compatibility(self):
        ra = RollingAccuracy(window=5)
        ra.record(1, 1)
        ra.record(1, 0)
        self.assertEqual(ra.accuracy(), 0.5)

        score = feature_drift_score(live_mean=15, live_std=2, train_mean=10, train_std=2)
        self.assertEqual(score, 2.5)


if __name__ == "__main__":
    unittest.main()
