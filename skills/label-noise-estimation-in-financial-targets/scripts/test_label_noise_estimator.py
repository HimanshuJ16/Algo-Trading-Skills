"""Unit tests for the Confident Learning label-noise estimator.

Expected values are derived by hand from Eqns 1-3 of Northcutt, Jiang & Chuang
(JAIR 70, 2021) rather than by re-running the implementation's own formula, so a
regression in the estimator surfaces as a failure rather than as a silently
updated expectation.
"""
import unittest

from label_noise_estimator import LabelNoiseEstimatorEngine


class TestConfidentErrorDetection(unittest.TestCase):

    def setUp(self):
        self.engine = LabelNoiseEstimatorEngine(high_noise_threshold_pct=20.0)

    def test_low_noise_dataset_is_cleaned(self):
        # 20 samples, one injected error per class (indices 9 and 19).
        # Hand-derived: t0 = 8.71/10 = 0.871, t1 = 8.63/10 = 0.863.
        # idx 9  (given 0): p0=0.05 < t0, p1=0.95 >= t1 -> true label 1 -> error.
        # idx 19 (given 1): p1=0.06 < t1, p0=0.94 >= t0 -> true label 0 -> error.
        # All others clear only their own class threshold -> on-diagonal.
        y_obs = [0] * 10 + [1] * 10
        probs_p1 = [0.02, 0.05, 0.03, 0.04, 0.06, 0.05, 0.03, 0.04, 0.02, 0.95,
                    0.96, 0.94, 0.97, 0.95, 0.93, 0.96, 0.94, 0.95, 0.97, 0.06]

        report, y_clean, weights = self.engine.estimate_label_noise(y_obs, probs_p1)

        self.assertEqual(report.class_thresholds, {0: 0.871, 1: 0.863})
        self.assertEqual(report.mislabeled_indices, [9, 19])
        self.assertEqual(report.mislabeled_samples_count, 2)
        self.assertEqual(report.estimated_noise_ratio_pct, 10.0)
        self.assertEqual(report.status, "TARGET_NOISE_CLEANED")
        self.assertEqual(report.confident_joint_counts, [[9, 1], [1, 9]])
        self.assertEqual(report.unconfident_samples_count, 0)

        # Relabel vector flips exactly the confident errors.
        self.assertEqual(y_clean[9], 1)
        self.assertEqual(y_clean[19], 0)
        # Prune vector zeroes exactly the confident errors.
        self.assertEqual(weights[9], 0.0)
        self.assertEqual(weights[19], 0.0)
        self.assertEqual(sum(weights), 18.0)

    def test_model_agreement_is_never_flagged_as_a_label_error(self):
        """Regression: Eqn 1's arg max collision term must be applied.

        Without it, a low t_0 (caused by a heavily corrupted class 0) makes every
        class-1 sample with even a sliver of class-0 probability clear t_0 and be
        flagged. Indices 5, 8 and 9 carry p1 = 0.88 / 0.85 / 0.89 -- the model
        strongly agrees with their observed label -- yet each also clears
        t_0 = 0.11. Eqn 1 resolves the collision by arg max, keeping them clean.

        Hand-derived: t0 = 0.33/3 = 0.11, t1 = 4.69/7 = 0.67. Genuine errors are
        the three class-0 samples the model calls class 1 (0, 1, 2) and the two
        class-1 samples the model calls class 0 (3, 4) -> 5/10 = 50%.
        """
        y_obs = [0, 0, 0, 1, 1, 1, 1, 1, 1, 1]
        probs_p1 = [0.90, 0.85, 0.92, 0.10, 0.15, 0.88, 0.90, 0.92, 0.85, 0.89]

        report, y_clean, weights = self.engine.estimate_label_noise(y_obs, probs_p1)

        self.assertEqual(report.class_thresholds, {0: 0.11, 1: 0.67})
        self.assertEqual(report.mislabeled_indices, [0, 1, 2, 3, 4])
        self.assertEqual(report.estimated_noise_ratio_pct, 50.0)
        self.assertEqual(report.status, "HIGH_LABEL_NOISE_WARNING")

        # The samples the model agrees with keep their label and full weight.
        for idx in (5, 8, 9):
            self.assertNotIn(idx, report.mislabeled_indices)
            self.assertEqual(y_clean[idx], 1)
            self.assertEqual(weights[idx], 1.0)

    def test_uninformative_probabilities_produce_no_errors(self):
        """Regression: a coin-flip model must not flag the entire dataset.

        With every probability at 0.5, t0 = t1 = 0.5 and both classes clear their
        threshold for every sample. The collision tie is broken in favour of the
        observed label, so no label error can be manufactured.
        """
        y_obs = [0, 1] * 5
        probs_p1 = [0.5] * 10

        report, y_clean, weights = self.engine.estimate_label_noise(y_obs, probs_p1)

        self.assertEqual(report.mislabeled_indices, [])
        self.assertEqual(report.estimated_noise_ratio_pct, 0.0)
        self.assertEqual(report.status, "TARGET_NOISE_CLEANED")
        self.assertEqual(y_clean, y_obs)
        self.assertEqual(weights, [1.0] * 10)

    def test_unconfident_samples_are_excluded_from_the_confident_joint(self):
        """Eqn 1 counts a sample only if it clears some class threshold.

        Hand-derived: t0 = t1 = 3.35/4 = 0.8375. Indices 3 and 7 sit at p = 0.50
        and clear neither threshold, so they enter no bin of the confident joint.
        Calibration (Eqn 3) then rescales each row back to the observed class
        count of 4, giving a joint of [[0.5, 0.0], [0.0, 0.5]].
        """
        y_obs = [0, 0, 0, 0, 1, 1, 1, 1]
        probs_p1 = [0.05, 0.05, 0.05, 0.50, 0.95, 0.95, 0.95, 0.50]

        report, _, weights = self.engine.estimate_label_noise(y_obs, probs_p1)

        self.assertEqual(report.class_thresholds, {0: 0.8375, 1: 0.8375})
        self.assertEqual(report.unconfident_samples_count, 2)
        self.assertEqual(report.confident_joint_counts, [[3, 0], [0, 3]])
        self.assertEqual(report.estimated_joint_distribution,
                         [[0.5, 0.0], [0.0, 0.5]])
        # Excluded samples are not errors, so they keep full training weight.
        self.assertEqual(report.mislabeled_indices, [])
        self.assertEqual(weights[3], 1.0)
        self.assertEqual(weights[7], 1.0)
        # The noise ratio denominator remains the full sample count.
        self.assertEqual(report.total_samples_count, 8)
        self.assertEqual(report.estimated_noise_ratio_pct, 0.0)


    def test_identical_self_confidences_are_not_silently_discarded(self):
        """Regression: exact-equality samples must clear their own threshold.

        t_k is the mean of the self-confidences it is compared against, so when a
        class has identical probabilities every sample sits exactly on the
        threshold. Floating-point summation lifts the mean a few ULPs above its
        own members (mean([0.99] * 50) == 0.9900000000000003), so a strict `>=`
        excludes all of them, empties the confident joint, and reports a
        perfectly clean dataset as 0% noise on zero evidence.
        """
        y_obs = [0] * 50 + [1] * 50
        probs_p1 = [0.01] * 50 + [0.99] * 50

        report, _, weights = self.engine.estimate_label_noise(y_obs, probs_p1)

        self.assertEqual(report.unconfident_samples_count, 0)
        self.assertEqual(report.confident_joint_counts, [[50, 0], [0, 50]])
        self.assertEqual(report.mislabeled_indices, [])
        self.assertEqual(report.estimated_noise_ratio_pct, 0.0)
        self.assertEqual(report.status, "TARGET_NOISE_CLEANED")
        self.assertEqual(weights, [1.0] * 100)

    def test_identical_self_confidences_still_detect_a_genuine_error(self):
        # Same saturated probabilities, but index 4 is labelled 0 while the model
        # puts it at p1 = 0.99 -> a single confident error, 1/10 = 10%.
        y_obs = [0] * 5 + [1] * 5
        probs_p1 = [0.01, 0.01, 0.01, 0.01, 0.99] + [0.99] * 5

        report, y_clean, weights = self.engine.estimate_label_noise(y_obs, probs_p1)

        self.assertEqual(report.mislabeled_indices, [4])
        self.assertEqual(report.estimated_noise_ratio_pct, 10.0)
        self.assertEqual(report.confident_joint_counts, [[4, 1], [0, 5]])
        self.assertEqual(y_clean[4], 1)
        self.assertEqual(weights[4], 0.0)


class TestHighNoiseThreshold(unittest.TestCase):

    def setUp(self):
        self.engine = LabelNoiseEstimatorEngine(high_noise_threshold_pct=20.0)

    def test_noise_ratio_exactly_at_threshold_triggers_warning(self):
        """standards.md mandates a warning at eta >= 20%, not eta > 20%.

        Hand-derived: t0 = 3.75/5 = 0.75, t1 = 3.70/5 = 0.74. idx 2 (given 0,
        p1 = 0.90) and idx 7 (given 1, p0 = 0.90) are the only off-diagonal
        samples -> exactly 2/10 = 20.0%.
        """
        y_obs = [0, 0, 0, 0, 0, 1, 1, 1, 1, 1]
        probs_p1 = [0.05, 0.10, 0.90, 0.08, 0.12, 0.85, 0.88, 0.10, 0.92, 0.95]

        report, _, _ = self.engine.estimate_label_noise(y_obs, probs_p1)

        self.assertEqual(report.estimated_noise_ratio_pct, 20.0)
        self.assertEqual(report.status, "HIGH_LABEL_NOISE_WARNING")

    def test_noise_ratio_just_below_threshold_does_not_warn(self):
        """Companion to the exact-boundary case: the same two errors, plus one
        extra clean class-1 sample, put the ratio at 2/11 = 18.18% < 20%.

        Hand-derived: t0 = 3.75/5 = 0.75 (unchanged), t1 = 4.63/6 = 0.7717.
        idx 2 and idx 7 remain the only off-diagonal samples.
        """
        y_obs = [0, 0, 0, 0, 0, 1, 1, 1, 1, 1, 1]
        probs_p1 = [0.05, 0.10, 0.90, 0.08, 0.12, 0.85, 0.88, 0.10, 0.92, 0.95, 0.93]

        report, _, _ = self.engine.estimate_label_noise(y_obs, probs_p1)

        self.assertEqual(report.class_thresholds, {0: 0.75, 1: 0.7717})
        self.assertEqual(report.mislabeled_indices, [2, 7])
        self.assertEqual(report.estimated_noise_ratio_pct, 18.18)
        self.assertEqual(report.status, "TARGET_NOISE_CLEANED")

    def test_invalid_threshold_is_rejected(self):
        for bad in (-1.0, 101.0, float("nan")):
            with self.assertRaises(ValueError):
                LabelNoiseEstimatorEngine(high_noise_threshold_pct=bad)


class TestNoiseMatrices(unittest.TestCase):

    def setUp(self):
        self.engine = LabelNoiseEstimatorEngine(high_noise_threshold_pct=20.0)

    def test_transition_and_inverse_matrices_are_distinct_and_correct(self):
        """The noise transition matrix is P(y_tilde|y_star), not P(y_star|y_tilde).

        Hand-derived from C = [[0, 3], [2, 5]] with class counts [3, 7]:
          calibrated = [[0, 3], [2, 5]] -> Q = [[0.0, 0.3], [0.2, 0.5]]
          P(y_tilde|y_star) column-normalised: [[0.0, 0.375], [1.0, 0.625]]
          P(y_star|y_tilde) row-normalised:    [[0.0, 1.0], [0.2857, 0.7143]]
        """
        y_obs = [0, 0, 0, 1, 1, 1, 1, 1, 1, 1]
        probs_p1 = [0.90, 0.85, 0.92, 0.10, 0.15, 0.88, 0.90, 0.92, 0.85, 0.89]

        report, _, _ = self.engine.estimate_label_noise(y_obs, probs_p1)

        self.assertEqual(report.confident_joint_counts, [[0, 3], [2, 5]])
        self.assertEqual(report.estimated_joint_distribution,
                         [[0.0, 0.3], [0.2, 0.5]])
        self.assertEqual(report.noise_transition_matrix, [[0.0, 0.375], [1.0, 0.625]])
        self.assertEqual(report.inverse_noise_matrix, [[0.0, 1.0], [0.2857, 0.7143]])

    def test_matrix_normalisation_invariants(self):
        y_obs = [0] * 6 + [1] * 6
        probs_p1 = [0.05, 0.08, 0.03, 0.91, 0.06, 0.04,
                    0.95, 0.93, 0.09, 0.97, 0.92, 0.94]

        report, _, _ = self.engine.estimate_label_noise(y_obs, probs_p1)

        # Joint sums to 1; transition columns sum to 1; inverse rows sum to 1.
        self.assertAlmostEqual(
            sum(sum(row) for row in report.estimated_joint_distribution), 1.0, places=6)
        for j in range(2):
            col = sum(report.noise_transition_matrix[i][j] for i in range(2))
            self.assertAlmostEqual(col, 1.0, places=4)
        for row in report.inverse_noise_matrix:
            self.assertAlmostEqual(sum(row), 1.0, places=4)


class TestInputValidation(unittest.TestCase):

    def setUp(self):
        self.engine = LabelNoiseEstimatorEngine()

    def test_length_mismatch_is_rejected(self):
        with self.assertRaises(ValueError):
            self.engine.estimate_label_noise([0, 1], [0.5])

    def test_empty_dataset_is_rejected(self):
        with self.assertRaises(ValueError):
            self.engine.estimate_label_noise([], [])

    def test_non_binary_labels_are_rejected(self):
        """A label outside {0, 1} would otherwise index the joint silently.

        Under Python's negative indexing, an observed label of -1 would land in
        row 1 of the confident joint and corrupt the estimate without any error.
        """
        for bad_labels in ([0, -1], [0, 2], [0, 1.5]):
            with self.assertRaises(ValueError):
                self.engine.estimate_label_noise(bad_labels, [0.4, 0.6])

    def test_integer_valued_float_labels_are_accepted(self):
        report, _, _ = self.engine.estimate_label_noise([0.0, 1.0], [0.1, 0.9])
        self.assertEqual(report.total_samples_count, 2)

    def test_non_finite_probabilities_are_rejected(self):
        """NaN compares False against every threshold and would read as clean."""
        for bad in (float("nan"), float("inf"), float("-inf")):
            with self.assertRaises(ValueError):
                self.engine.estimate_label_noise([0, 1], [0.4, bad])

    def test_out_of_range_probabilities_are_rejected(self):
        for bad in (-0.01, 1.01):
            with self.assertRaises(ValueError):
                self.engine.estimate_label_noise([0, 1], [0.4, bad])

    def test_single_class_target_falls_back_to_neutral_threshold(self):
        # Class 0 is absent, so t_0 is undefined and falls back to 0.5.
        with self.assertLogs("label_noise_estimator", level="WARNING"):
            report, _, _ = self.engine.estimate_label_noise([1, 1, 1], [0.9, 0.8, 0.2])
        self.assertEqual(report.class_thresholds[0], 0.5)


if __name__ == '__main__':
    unittest.main()
