import unittest

import numpy as np

from imbalance_handler import ImbalanceHandler


class TestComputeClassWeights(unittest.TestCase):

    def setUp(self):
        # Class 0: 990 samples (Noise), Class 1: 10 samples (Rare Event)
        self.y = np.concatenate([np.zeros(990, dtype=int), np.ones(10, dtype=int)])
        self.X = np.random.rand(1000, 5)

    def test_compute_class_weights(self):
        weights = ImbalanceHandler.compute_class_weights(self.y)

        # Total samples = 1000, n_classes = 2
        # Class 0 weight = 1000 / (2 * 990) = ~0.50505
        # Class 1 weight = 1000 / (2 * 10) = 50.0
        self.assertAlmostEqual(weights[0], 0.50505050505, places=8)
        self.assertAlmostEqual(weights[1], 50.0, places=8)
        self.assertGreater(weights[1], weights[0] * 90)

    def test_weight_keys_are_plain_ints(self):
        # numpy scalar keys break json.dumps() of a model config and confuse
        # estimators that compare labels by identity.
        weights = ImbalanceHandler.compute_class_weights(self.y)
        for key in weights:
            self.assertIs(type(key), int)
            self.assertIs(type(weights[key]), float)

    def test_weighted_sample_count_equals_n_samples(self):
        # The documented property of the 'balanced' formula: summing each
        # sample's weight reproduces the unweighted dataset size.
        y = np.array([0] * 7 + [1] * 2 + [2] * 1)
        weights = ImbalanceHandler.compute_class_weights(y)
        total = sum(weights[int(label)] for label in y)
        self.assertAlmostEqual(total, len(y), places=8)

    def test_multiclass_weights_hand_computed(self):
        y = np.array([0] * 6 + [1] * 3 + [2] * 1)
        weights = ImbalanceHandler.compute_class_weights(y)
        self.assertAlmostEqual(weights[0], 10 / (3 * 6), places=8)
        self.assertAlmostEqual(weights[1], 10 / (3 * 3), places=8)
        self.assertAlmostEqual(weights[2], 10 / (3 * 1), places=8)

    def test_rejects_nan_labels(self):
        # Regression: NaN labels used to be counted as extra classes, silently
        # deflating every weight instead of failing.
        with self.assertRaises(ValueError):
            ImbalanceHandler.compute_class_weights(np.array([0.0, 1.0, np.nan, np.nan]))

    def test_rejects_two_dimensional_labels(self):
        with self.assertRaises(ValueError):
            ImbalanceHandler.compute_class_weights(np.array([[0, 1], [0, 0]]))

    def test_rejects_non_integral_and_non_numeric_labels(self):
        with self.assertRaises(ValueError):
            ImbalanceHandler.compute_class_weights(np.array([0.0, 0.5, 1.0]))
        with self.assertRaises(ValueError):
            ImbalanceHandler.compute_class_weights(np.array(["up", "down"]))

    def test_rejects_empty_labels(self):
        with self.assertRaises(ValueError):
            ImbalanceHandler.compute_class_weights(np.array([], dtype=int))

    def test_accepts_integral_float_labels(self):
        weights = ImbalanceHandler.compute_class_weights(np.array([0.0, 0.0, 1.0]))
        self.assertAlmostEqual(weights[1], 3 / (2 * 1), places=8)


class TestScalePosWeight(unittest.TestCase):

    def test_ratio_is_negatives_over_positives(self):
        y = np.concatenate([np.zeros(990, dtype=int), np.ones(10, dtype=int)])
        self.assertAlmostEqual(ImbalanceHandler.compute_scale_pos_weight(y), 99.0, places=8)

    def test_differs_from_class_weight_dict(self):
        # The two are not interchangeable: scale_pos_weight is neg/pos, the dict
        # entry is n / (n_classes * count).
        y = np.concatenate([np.zeros(990, dtype=int), np.ones(10, dtype=int)])
        self.assertNotAlmostEqual(
            ImbalanceHandler.compute_scale_pos_weight(y),
            ImbalanceHandler.compute_class_weights(y)[1],
            places=4)

    def test_custom_positive_class(self):
        y = np.array([-1, -1, -1, 1])
        self.assertAlmostEqual(ImbalanceHandler.compute_scale_pos_weight(y, positive_class=1), 3.0)
        self.assertAlmostEqual(
            ImbalanceHandler.compute_scale_pos_weight(y, positive_class=-1), 1 / 3, places=8)

    def test_rejects_missing_positive_class(self):
        with self.assertRaises(ValueError):
            ImbalanceHandler.compute_scale_pos_weight(np.zeros(10, dtype=int))

    def test_rejects_multiclass(self):
        with self.assertRaises(ValueError):
            ImbalanceHandler.compute_scale_pos_weight(np.array([0, 1, 2]))


class TestRandomUndersample(unittest.TestCase):

    def setUp(self):
        self.y = np.concatenate([np.zeros(990, dtype=int), np.ones(10, dtype=int)])
        # Column 0 carries the original row index so alignment can be verified.
        self.X = np.column_stack([np.arange(1000), np.random.rand(1000, 4)])

    def test_random_undersample_reaches_parity(self):
        X_bal, y_bal = ImbalanceHandler.random_undersample(self.X, self.y, random_state=42)
        self.assertEqual(len(y_bal), 20)
        self.assertEqual(len(X_bal), 20)
        counts = np.bincount(y_bal)
        self.assertEqual(counts[0], 10)
        self.assertEqual(counts[1], 10)

    def test_rows_stay_aligned_and_ordered(self):
        X_bal, y_bal = ImbalanceHandler.random_undersample(self.X, self.y, random_state=7)
        original_rows = X_bal[:, 0].astype(int)
        # Every returned label matches the label of the row it came from.
        np.testing.assert_array_equal(y_bal, self.y[original_rows])
        # Original (chronological) ordering is preserved.
        self.assertTrue(np.all(np.diff(original_rows) > 0))

    def test_undersample_determinism(self):
        X1, y1 = ImbalanceHandler.random_undersample(self.X, self.y, random_state=123)
        X2, y2 = ImbalanceHandler.random_undersample(self.X, self.y, random_state=123)
        np.testing.assert_array_equal(X1, X2)
        np.testing.assert_array_equal(y1, y2)

    def test_different_seeds_select_different_majority_rows(self):
        X1, _ = ImbalanceHandler.random_undersample(self.X, self.y, random_state=1)
        X2, _ = ImbalanceHandler.random_undersample(self.X, self.y, random_state=2)
        self.assertFalse(np.array_equal(X1[:, 0], X2[:, 0]))

    def test_does_not_mutate_global_numpy_seed(self):
        # Regression: the implementation used to call np.random.seed(), which
        # silently reseeded the caller's global RNG and made every later draw in
        # the training pipeline a function of this helper's seed.
        np.random.seed(2024)
        expected = np.random.rand(3)

        np.random.seed(2024)
        ImbalanceHandler.random_undersample(self.X, self.y, random_state=999)
        after = np.random.rand(3)

        np.testing.assert_array_equal(expected, after)

    def test_already_balanced_input_keeps_both_classes(self):
        # Regression: with equal class counts, argmin and argmax both returned
        # index 0, so the minority class was dropped entirely and the majority
        # class was duplicated.
        X = np.arange(8).reshape(4, 2)
        y = np.array([0, 0, 1, 1])
        X_bal, y_bal = ImbalanceHandler.random_undersample(X, y, random_state=42)
        np.testing.assert_array_equal(y_bal, np.array([0, 0, 1, 1]))
        np.testing.assert_array_equal(X_bal, X)

    def test_majority_ratio_two_to_one(self):
        X_bal, y_bal = ImbalanceHandler.random_undersample(
            self.X, self.y, random_state=42, majority_ratio=2.0)
        counts = np.bincount(y_bal)
        self.assertEqual(counts[0], 20)
        self.assertEqual(counts[1], 10)

    def test_ratio_capped_by_available_majority_rows(self):
        # 6 majority rows requested at 4:1 against 2 minority rows -> only 6 exist.
        X = np.arange(16).reshape(8, 2)
        y = np.array([0, 0, 0, 0, 0, 0, 1, 1])
        _, y_bal = ImbalanceHandler.random_undersample(X, y, random_state=0, majority_ratio=4.0)
        counts = np.bincount(y_bal)
        self.assertEqual(counts[0], 6)
        self.assertEqual(counts[1], 2)

    def test_never_oversamples_duplicate_rows(self):
        X = np.arange(16).reshape(8, 2)
        y = np.array([0, 0, 0, 0, 0, 0, 1, 1])
        X_bal, _ = ImbalanceHandler.random_undersample(X, y, random_state=0, majority_ratio=10.0)
        self.assertEqual(len(np.unique(X_bal[:, 0])), len(X_bal))

    def test_rejects_length_mismatch(self):
        with self.assertRaises(ValueError):
            ImbalanceHandler.random_undersample(self.X[:100], self.y)

    def test_rejects_invalid_majority_ratio(self):
        with self.assertRaises(ValueError):
            ImbalanceHandler.random_undersample(self.X, self.y, majority_ratio=0)
        with self.assertRaises(ValueError):
            ImbalanceHandler.random_undersample(self.X, self.y, majority_ratio=float("nan"))

    def test_rejects_non_binary_target(self):
        X = np.arange(12).reshape(6, 2)
        y = np.array([0, 0, 1, 1, 2, 2])
        with self.assertRaises(NotImplementedError):
            ImbalanceHandler.random_undersample(X, y)


class TestUndersamplingBiasCorrection(unittest.TestCase):

    def test_beta_one_is_identity(self):
        p = np.array([0.0, 0.25, 0.5, 1.0])
        np.testing.assert_allclose(ImbalanceHandler.correct_undersampling_bias(p, 1.0), p)

    def test_values_match_independent_odds_derivation(self):
        # beta = 0.1, p_s = 0.5 -> sampled odds 1.0 -> true odds 0.1 -> p = 1/11.
        corrected = ImbalanceHandler.correct_undersampling_bias(np.array([0.5]), 0.1)
        self.assertAlmostEqual(corrected[0], 1 / 11, places=10)

        # beta = 0.5, p_s = 0.8 -> sampled odds 4.0 -> true odds 2.0 -> p = 2/3.
        corrected = ImbalanceHandler.correct_undersampling_bias(np.array([0.8]), 0.5)
        self.assertAlmostEqual(corrected[0], 2 / 3, places=10)

    def test_correction_recovers_the_true_prior(self):
        # A model that learns only the base rate outputs the balanced prior 0.5
        # after 1:1 undersampling of a 1% event; correcting must return 0.01.
        n_majority, n_minority = 9900, 100
        beta = n_minority / n_majority
        corrected = ImbalanceHandler.correct_undersampling_bias(np.array([0.5]), beta)
        self.assertAlmostEqual(corrected[0], n_minority / (n_majority + n_minority), places=10)

    def test_correction_shrinks_probabilities_and_preserves_ranking(self):
        p = np.array([0.2, 0.4, 0.6, 0.9])
        corrected = ImbalanceHandler.correct_undersampling_bias(p, 0.05)
        self.assertTrue(np.all(corrected < p))
        np.testing.assert_array_equal(np.argsort(corrected), np.argsort(p))

    def test_boundaries_stay_in_range(self):
        corrected = ImbalanceHandler.correct_undersampling_bias(np.array([0.0, 1.0]), 0.01)
        self.assertAlmostEqual(corrected[0], 0.0, places=12)
        self.assertAlmostEqual(corrected[1], 1.0, places=12)

    def test_rejects_invalid_beta(self):
        for bad_beta in (0.0, -0.1, 1.5, float("nan")):
            with self.assertRaises(ValueError):
                ImbalanceHandler.correct_undersampling_bias(np.array([0.5]), bad_beta)

    def test_rejects_out_of_range_or_nan_probabilities(self):
        with self.assertRaises(ValueError):
            ImbalanceHandler.correct_undersampling_bias(np.array([1.2]), 0.5)
        with self.assertRaises(ValueError):
            ImbalanceHandler.correct_undersampling_bias(np.array([np.nan]), 0.5)


if __name__ == '__main__':
    unittest.main()
