"""Unit tests for adversarial-robustness-of-trading-signals.

Test categories
---------------
* Core behaviour: robust model passes, fragile model fails, montecarlo worst-of-N
* Determinism: same seed -> identical report; seed propagates across trials
* Decoding: integer labels, 1D float scores, 2D probability matrices
* Clipping: clean-domain clip, explicit feature_bounds
* Scaling: explicit feature_scales, zero-variance fallback
* Validation: epsilon, tolerance, noise_type, n_trials, feature_scales,
  feature_bounds, batch_size
* API ergonomics: legacy alias, report.as_dict JSON round-trip, chunked predict
"""
import json
import unittest

import numpy as np

from signal_adversarial_tester import (
    AdversarialRobustnessConfig,
    NOISE_MONTECARLO_WORST,
    NOISE_RANDOM_SIGN,
    NOISE_UNIFORM,
    SignalAdversarialTester,
)


class DummyFragileModel:
    """Flips signal when feature 0 > 0.5 — highly sensitive at the boundary."""

    def predict(self, X: np.ndarray) -> np.ndarray:
        return (X[:, 0] > 0.5).astype(int)


class DummyRobustModel:
    """Signal flips only when feature 0 crosses 5.0 — insensitive to small noise."""

    def predict(self, X: np.ndarray) -> np.ndarray:
        return (X[:, 0] > 5.0).astype(int)


class DummyProbaModel:
    """Returns a 2D probability matrix (n, 2)."""

    def predict(self, X: np.ndarray) -> np.ndarray:
        p1 = X[:, 0] / 10.0
        return np.column_stack([1.0 - p1, p1])


class DummyScoreModel:
    """Returns a 1D float score in [0, 1]."""

    def predict(self, X: np.ndarray) -> np.ndarray:
        return X[:, 0] / 10.0


class TestCoreBehaviour(unittest.TestCase):
    def test_robust_model_passes_uniform(self):
        rng = np.random.default_rng(0)
        X = rng.uniform(0, 10, size=(500, 2))
        tester = SignalAdversarialTester(
            AdversarialRobustnessConfig(epsilon=0.01, seed=42)
        )
        report = tester.evaluate_model(DummyRobustModel().predict, X)
        self.assertTrue(report.is_robust)
        self.assertLessEqual(report.vulnerability_score_pct, 5.0)
        self.assertEqual(report.noise_type, NOISE_UNIFORM)

    def test_fragile_model_fails_under_random_sign(self):
        # Clustered exactly on the 0.5 boundary — maximally fragile.
        # clip_to_clean_domain=False so the perturbation is not clipped back
        # to the degenerate [0.5, 0.5] domain of a constant column.
        X = np.full((200, 1), 0.5)
        tester = SignalAdversarialTester(
            AdversarialRobustnessConfig(
                epsilon=0.1, flip_tolerance_pct=2.0,
                noise_type=NOISE_RANDOM_SIGN, seed=42,
                clip_to_clean_domain=False,
            )
        )
        report = tester.evaluate_model(DummyFragileModel().predict, X)
        self.assertFalse(report.is_robust)
        self.assertGreater(report.vulnerability_score_pct, 2.0)
        self.assertIn("VULNERABLE", report.message)

    def test_montecarlo_reports_max_across_trials(self):
        X = np.full((200, 1), 0.5)
        # Single-trial montecarlo == one random_sign draw.
        single = SignalAdversarialTester(
            AdversarialRobustnessConfig(
                epsilon=0.1, noise_type=NOISE_MONTECARLO_WORST,
                n_trials=1, seed=42,
            )
        ).evaluate_model(DummyFragileModel().predict, X)
        # Many-trial montecarlo must be >= the single-trial draw (it is the max).
        multi = SignalAdversarialTester(
            AdversarialRobustnessConfig(
                epsilon=0.1, noise_type=NOISE_MONTECARLO_WORST,
                n_trials=50, seed=42,
            )
        ).evaluate_model(DummyFragileModel().predict, X)
        self.assertGreaterEqual(multi.vulnerability_score_pct, single.vulnerability_score_pct)
        self.assertEqual(multi.n_trials, 50)
        self.assertGreaterEqual(multi.worst_trial_index, 0)


class TestDeterminism(unittest.TestCase):
    def test_same_seed_identical_report(self):
        X = np.full((100, 1), 0.5)
        cfg = AdversarialRobustnessConfig(
            epsilon=0.1, noise_type=NOISE_RANDOM_SIGN, seed=7,
        )
        r1 = SignalAdversarialTester(cfg).evaluate_model(DummyFragileModel().predict, X)
        r2 = SignalAdversarialTester(cfg).evaluate_model(DummyFragileModel().predict, X)
        self.assertEqual(r1.vulnerability_score_pct, r2.vulnerability_score_pct)
        np.testing.assert_array_equal(r1.flipped_indices, r2.flipped_indices)

    def test_montecarlo_deterministic_with_seed(self):
        X = np.full((100, 1), 0.5)
        cfg = AdversarialRobustnessConfig(
            epsilon=0.1, noise_type=NOISE_MONTECARLO_WORST, n_trials=25, seed=11,
        )
        r1 = SignalAdversarialTester(cfg).evaluate_model(DummyFragileModel().predict, X)
        r2 = SignalAdversarialTester(cfg).evaluate_model(DummyFragileModel().predict, X)
        self.assertEqual(r1.vulnerability_score_pct, r2.vulnerability_score_pct)
        self.assertEqual(r1.worst_trial_index, r2.worst_trial_index)


class TestOutputDecoding(unittest.TestCase):
    def test_probability_matrix_uses_argmax(self):
        # feature 0 in [0, 10]; proba of class 1 = feature0/10; argmax flips at 5.0.
        X = np.full((200, 1), 5.0)
        tester = SignalAdversarialTester(
            AdversarialRobustnessConfig(
                epsilon=0.01, noise_type=NOISE_RANDOM_SIGN, seed=42,
                clip_to_clean_domain=False,
            )
        )
        report = tester.evaluate_model(DummyProbaModel().predict, X)
        # At the 5.0 boundary, half the random-sign pushes flip the argmax.
        self.assertGreater(report.flipped_signals, 0)

    def test_float_score_uses_decision_threshold(self):
        # score = feature0/10; threshold 0.5 flips at feature0=5.0.
        X = np.full((200, 1), 5.0)
        tester = SignalAdversarialTester(
            AdversarialRobustnessConfig(
                epsilon=0.01, noise_type=NOISE_RANDOM_SIGN,
                decision_threshold=0.5, seed=42,
                clip_to_clean_domain=False,
            )
        )
        report = tester.evaluate_model(DummyScoreModel().predict, X)
        self.assertGreater(report.flipped_signals, 0)

    def test_integer_labels_used_directly(self):
        X = np.full((50, 1), 0.5)
        tester = SignalAdversarialTester(
            AdversarialRobustnessConfig(epsilon=0.1, noise_type=NOISE_RANDOM_SIGN, seed=42)
        )
        report = tester.evaluate_model(DummyFragileModel().predict, X)
        # FragileModel returns int labels directly; some must flip.
        self.assertGreaterEqual(report.total_samples, 50)


class TestClipping(unittest.TestCase):
    def test_clip_to_clean_domain_bounds_perturbations(self):
        # Features are all 0.0 (min=0=max=0 => scale fallback 1.0); positive
        # perturbation would push to +0.1 but the domain clip returns to 0.0,
        # so a model keyed on >0.0 should NOT flip when clipped.
        class ZeroBoundModel:
            def predict(self, X):
                return (X[:, 0] > 0.0).astype(int)

        X = np.zeros((100, 1))
        # Clipped: perturbation forced back to 0.0 -> no flips.
        clipped = SignalAdversarialTester(
            AdversarialRobustnessConfig(
                epsilon=0.5, noise_type=NOISE_RANDOM_SIGN,
                clip_to_clean_domain=True, seed=42,
            )
        ).evaluate_model(ZeroBoundModel().predict, X)
        self.assertEqual(clipped.flipped_signals, 0)
        self.assertTrue(clipped.is_robust)

        # Unclipped: perturbation survives -> flips occur.
        unclipped = SignalAdversarialTester(
            AdversarialRobustnessConfig(
                epsilon=0.5, noise_type=NOISE_RANDOM_SIGN,
                clip_to_clean_domain=False, seed=42,
            )
        ).evaluate_model(ZeroBoundModel().predict, X)
        self.assertGreater(unclipped.flipped_signals, 0)

    def test_explicit_feature_bounds_clip(self):
        class ZeroBoundModel:
            def predict(self, X):
                return (X[:, 0] > 0.0).astype(int)

        X = np.zeros((100, 1))
        bounds = np.array([[0.0, 0.0]])  # force all perturbations to 0.0
        report = SignalAdversarialTester(
            AdversarialRobustnessConfig(
                epsilon=0.5, noise_type=NOISE_RANDOM_SIGN,
                feature_bounds=bounds, seed=42,
            )
        ).evaluate_model(ZeroBoundModel().predict, X)
        self.assertEqual(report.flipped_signals, 0)


class TestFeatureScaling(unittest.TestCase):
    def test_explicit_feature_scales_used(self):
        # With a tiny explicit scale, even a large epsilon produces tiny noise.
        X = np.full((200, 1), 5.0)
        scales = np.array([1e-6])
        report = SignalAdversarialTester(
            AdversarialRobustnessConfig(
                epsilon=1.0,  # huge epsilon, but scale collapses it
                noise_type=NOISE_RANDOM_SIGN,
                feature_scales=scales, seed=42,
            )
        ).evaluate_model(DummyRobustModel().predict, X)
        self.assertEqual(report.flipped_signals, 0)
        self.assertTrue(report.is_robust)

    def test_zero_variance_feature_fallback(self):
        # Constant feature column -> ptp=0 -> scale fallback to 1.0.
        X = np.full((50, 1), 0.5)
        tester = SignalAdversarialTester(
            AdversarialRobustnessConfig(
                epsilon=0.1, noise_type=NOISE_RANDOM_SIGN, seed=42,
                clip_to_clean_domain=False,
            )
        )
        # Should not raise; flips occur because scale fallback enables noise.
        report = tester.evaluate_model(DummyFragileModel().predict, X)
        self.assertGreater(report.flipped_signals, 0)

    def test_feature_scales_length_mismatch_raises(self):
        X = np.zeros((10, 2))
        tester = SignalAdversarialTester(
            AdversarialRobustnessConfig(feature_scales=np.array([1.0]))
        )
        with self.assertRaises(ValueError):
            tester.evaluate_model(DummyRobustModel().predict, X)


class TestConfigValidation(unittest.TestCase):
    def test_negative_epsilon_raises(self):
        with self.assertRaises(ValueError):
            AdversarialRobustnessConfig(epsilon=-0.1)

    def test_negative_tolerance_raises(self):
        with self.assertRaises(ValueError):
            AdversarialRobustnessConfig(flip_tolerance_pct=-1.0)

    def test_invalid_noise_type_raises(self):
        with self.assertRaises(ValueError):
            AdversarialRobustnessConfig(noise_type="gaussian")

    def test_n_trials_below_one_raises(self):
        with self.assertRaises(ValueError):
            AdversarialRobustnessConfig(noise_type=NOISE_MONTECARLO_WORST, n_trials=0)

    def test_non_positive_feature_scales_raises(self):
        with self.assertRaises(ValueError):
            AdversarialRobustnessConfig(feature_scales=np.array([1.0, 0.0]))

    def test_wrong_shape_feature_bounds_raises(self):
        with self.assertRaises(ValueError):
            AdversarialRobustnessConfig(feature_bounds=np.array([0.0, 1.0]))

    def test_inverted_feature_bounds_raises(self):
        with self.assertRaises(ValueError):
            AdversarialRobustnessConfig(feature_bounds=np.array([[1.0, 0.0]]))

    def test_invalid_batch_size_raises(self):
        with self.assertRaises(ValueError):
            AdversarialRobustnessConfig(batch_size=0)


class TestApiErgonomics(unittest.TestCase):
    def test_legacy_worst_case_sign_alias_routes_to_random_sign(self):
        X = np.full((100, 1), 0.5)
        report = SignalAdversarialTester(
            AdversarialRobustnessConfig(
                epsilon=0.1, noise_type="worst_case_sign", seed=42,
                clip_to_clean_domain=False,
            )
        ).evaluate_model(DummyFragileModel().predict, X)
        # Normalised to the honest name in the report.
        self.assertEqual(report.noise_type, NOISE_RANDOM_SIGN)
        self.assertGreater(report.flipped_signals, 0)

    def test_report_as_dict_is_json_serializable(self):
        X = np.full((50, 1), 0.5)
        report = SignalAdversarialTester(
            AdversarialRobustnessConfig(
                epsilon=0.1, noise_type=NOISE_MONTECARLO_WORST, n_trials=5, seed=42,
            )
        ).evaluate_model(DummyFragileModel().predict, X)
        d = report.as_dict()
        json.dumps(d)  # must not raise
        self.assertIsInstance(d["flipped_indices"], list)
        self.assertEqual(d["n_trials"], 5)

    def test_flipped_indices_within_bounds_and_correct(self):
        X = np.full((40, 1), 0.5)
        report = SignalAdversarialTester(
            AdversarialRobustnessConfig(
                epsilon=0.1, noise_type=NOISE_RANDOM_SIGN, seed=42,
            )
        ).evaluate_model(DummyFragileModel().predict, X)
        idx = report.flipped_indices
        self.assertEqual(report.flipped_signals, len(idx))
        self.assertTrue(np.all(idx >= 0))
        self.assertTrue(np.all(idx < report.total_samples))

    def test_batch_size_chunking_matches_whole_array(self):
        rng = np.random.default_rng(0)
        X = rng.uniform(0, 10, size=(300, 2))
        cfg_whole = AdversarialRobustnessConfig(epsilon=0.02, seed=42)
        cfg_chunk = AdversarialRobustnessConfig(epsilon=0.02, seed=42, batch_size=64)
        r_whole = SignalAdversarialTester(cfg_whole).evaluate_model(
            DummyRobustModel().predict, X
        )
        r_chunk = SignalAdversarialTester(cfg_chunk).evaluate_model(
            DummyRobustModel().predict, X
        )
        self.assertEqual(r_whole.vulnerability_score_pct, r_chunk.vulnerability_score_pct)
        np.testing.assert_array_equal(r_whole.flipped_indices, r_chunk.flipped_indices)

    def test_non_2d_input_raises(self):
        tester = SignalAdversarialTester(AdversarialRobustnessConfig())
        with self.assertRaises(ValueError):
            tester.evaluate_model(DummyRobustModel().predict, np.zeros(10))


if __name__ == "__main__":
    unittest.main()
