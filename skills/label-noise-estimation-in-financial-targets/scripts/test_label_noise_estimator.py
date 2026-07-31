import unittest
from label_noise_estimator import (
    LabelNoiseEstimatorEngine, LabelNoiseReport
)

class TestLabelNoiseEstimatorEngine(unittest.TestCase):

    def setUp(self):
        self.engine = LabelNoiseEstimatorEngine(high_noise_threshold_pct=20.0)

    def test_clean_label_noise_detection(self):
        # 10 samples: 8 correct, 2 mislabeled (index 2: given 0 but prob=0.90, index 7: given 1 but prob=0.10)
        y_obs = [0, 0, 0, 0, 0, 1, 1, 1, 1, 1]
        probs_p1 = [0.05, 0.10, 0.90, 0.08, 0.12, 0.85, 0.88, 0.10, 0.92, 0.95]

        report, y_clean, weights = self.engine.estimate_label_noise(y_obs, probs_p1)

        self.assertEqual(report.status, "TARGET_NOISE_CLEANED")
        self.assertEqual(report.mislabeled_samples_count, 2)
        self.assertEqual(report.estimated_noise_ratio_pct, 20.0)
        self.assertIn(2, report.mislabeled_indices)
        self.assertIn(7, report.mislabeled_indices)

        # Verify sample weights pruned (0.0) for mislabeled points
        self.assertEqual(weights[2], 0.0)
        self.assertEqual(weights[7], 0.0)
        self.assertEqual(y_clean[2], 1)
        self.assertEqual(y_clean[7], 0)

    def test_high_label_noise_warning_trigger(self):
        # 30% noise -> HIGH_LABEL_NOISE_WARNING!
        y_obs = [0, 0, 0, 1, 1, 1, 1, 1, 1, 1]
        probs_p1 = [0.90, 0.85, 0.92, 0.10, 0.15, 0.88, 0.90, 0.92, 0.85, 0.89]

        report, y_clean, weights = self.engine.estimate_label_noise(y_obs, probs_p1)

        self.assertEqual(report.status, "HIGH_LABEL_NOISE_WARNING")
        self.assertGreaterEqual(report.estimated_noise_ratio_pct, 20.0)

if __name__ == '__main__':
    unittest.main()
