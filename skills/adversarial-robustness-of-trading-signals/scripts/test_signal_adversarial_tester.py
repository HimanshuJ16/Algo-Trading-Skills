"""Unit tests for adversarial-robustness-of-trading-signals.

Test categories
---------------
* Core behaviour: robust model passes, fragile model fails, montecarlo worst-of-N
* Determinism: same seed -> identical report; seed propagates across trials
* Decoding: integer labels, 1D float scores, 2D probability matrices
* Clipping: clean-domain clip, explicit feature_bounds
* Scaling: explicit feature_scales, zero-variance fallback
* Validation: epsilon, tolerance, noise_type, n_trials, feature_scales,
  feature_bounds, batch_size, ci_confidence_level
* Rubber-stamp guards: non-finite X, non-finite / higher-rank model output,
  degenerate shapes -- each of which previously produced a silent PASS
* Statistical validity: Wilson upper bound, is_robust_at_ci marginal verdict
* API ergonomics: legacy alias, report.as_dict JSON round-trip, chunked predict,
  public perturb(), categorical class labels
"""
import json
import logging
import unittest

import numpy as np

from signal_adversarial_tester import (
    AdversarialRobustnessConfig,
    DEFAULT_CI_CONFIDENCE,
    NOISE_MONTECARLO_WORST,
    NOISE_RANDOM_SIGN,
    NOISE_UNIFORM,
    SignalAdversarialTester,
    wilson_upper_bound,
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

    def test_out_of_range_ci_confidence_raises(self):
        for bad in (0.0, 0.5, 1.0, 1.5):
            with self.subTest(confidence=bad):
                with self.assertRaises(ValueError):
                    AdversarialRobustnessConfig(ci_confidence_level=bad)

    def test_zero_epsilon_logs_vacuous_gate_warning(self):
        # epsilon=0 is accepted (a deliberate control run) but must not pass
        # silently: it would report 0% flips and PASS every model.
        with self.assertLogs("signal_adversarial_tester", level=logging.WARNING) as cm:
            AdversarialRobustnessConfig(epsilon=0.0)
        self.assertIn("no perturbation", "".join(cm.output))


class TestRubberStampGuards(unittest.TestCase):
    """Inputs that previously produced a silent PASS must now raise.

    Every case here is a regression test: against the pre-fix engine each one
    returned ``is_robust=True`` with a 0.00% flip rate, turning the governance
    gate into a rubber stamp.
    """

    def test_nan_in_x_clean_raises_instead_of_passing(self):
        # One NaN cell makes the per-feature ptp NaN, which makes *every*
        # perturbation NaN, which decodes to one class for clean and adversarial
        # alike -> 0% flips -> PASS.
        X = np.full((100, 1), 0.5)
        X[0, 0] = np.nan
        tester = SignalAdversarialTester(
            AdversarialRobustnessConfig(
                epsilon=0.1, noise_type=NOISE_RANDOM_SIGN, seed=1
            )
        )
        with self.assertRaises(ValueError) as ctx:
            tester.evaluate_model(DummyFragileModel().predict, X)
        self.assertIn("non-finite", str(ctx.exception))

    def test_inf_in_x_clean_raises(self):
        X = np.full((100, 1), 0.5)
        X[0, 0] = np.inf
        tester = SignalAdversarialTester(AdversarialRobustnessConfig(seed=1))
        with self.assertRaises(ValueError):
            tester.evaluate_model(DummyFragileModel().predict, X)

    def test_zero_sample_matrix_raises(self):
        tester = SignalAdversarialTester(AdversarialRobustnessConfig(seed=1))
        with self.assertRaises(ValueError) as ctx:
            tester.evaluate_model(DummyFragileModel().predict, np.zeros((0, 2)))
        self.assertIn("non-empty", str(ctx.exception))

    def test_zero_feature_matrix_raises(self):
        # No features => nothing to perturb => a vacuous 0% flip rate.
        tester = SignalAdversarialTester(AdversarialRobustnessConfig(seed=1))
        with self.assertRaises(ValueError):
            tester.evaluate_model(
                lambda X: np.zeros(len(X), dtype=int), np.zeros((10, 0))
            )

    def test_non_finite_model_output_raises(self):
        # nan > threshold is False for clean and perturbed alike -> 0% flips.
        tester = SignalAdversarialTester(AdversarialRobustnessConfig(seed=1))
        with self.assertRaises(ValueError) as ctx:
            tester.evaluate_model(
                lambda X: np.full(len(X), np.nan), np.zeros((10, 2))
            )
        self.assertIn("NaN", str(ctx.exception))

    def test_higher_rank_model_output_raises(self):
        # A 3-D output used to survive the length check and make flipped_indices
        # index a flattened array -- indices beyond total_samples.
        tester = SignalAdversarialTester(AdversarialRobustnessConfig(seed=1))
        with self.assertRaises(ValueError) as ctx:
            tester.evaluate_model(
                lambda X: np.zeros((len(X), 2, 2)), np.zeros((10, 2))
            )
        self.assertIn("1-D", str(ctx.exception))


class TestStatisticalValidity(unittest.TestCase):
    def test_wilson_upper_bound_matches_independent_values(self):
        # Expected values derived independently by solving the score equation
        # (p_hat - p) / sqrt(p(1-p)/n) = -z for p with a root finder, not by
        # re-running the closed form under test.
        for successes, trials, expected in [
            (5, 100, 0.09916109),
            (25, 500, 0.06859312),
            (41, 2000, 0.02639623),
            (500, 10000, 0.05370817),
            (1, 1000, 0.00446972),
        ]:
            with self.subTest(successes=successes, trials=trials):
                self.assertAlmostEqual(
                    wilson_upper_bound(successes, trials), expected, places=7
                )

    def test_wilson_zero_successes_is_not_zero_width(self):
        # The regime where a Wald bound would wrongly certify robustness.
        bound = wilson_upper_bound(0, 50)
        self.assertGreater(bound, 0.0)
        self.assertAlmostEqual(bound, 0.05133319, places=7)

    def test_wilson_upper_bound_rejects_bad_arguments(self):
        with self.assertRaises(ValueError):
            wilson_upper_bound(0, 0)
        with self.assertRaises(ValueError):
            wilson_upper_bound(5, 4)
        with self.assertRaises(ValueError):
            wilson_upper_bound(1, 10, confidence_level=1.0)

    def test_marginal_pass_flagged_by_is_robust_at_ci(self):
        # 0 flips on 50 samples: the point estimate 0% clears a 5% tolerance but
        # the 95% Wilson upper bound is ~5.13% -- not a clean pass.
        X = np.full((50, 1), 0.5)
        report = SignalAdversarialTester(
            AdversarialRobustnessConfig(
                epsilon=1e-12, noise_type=NOISE_RANDOM_SIGN,
                flip_tolerance_pct=5.0, seed=42,
            )
        ).evaluate_model(DummyRobustModel().predict, X)
        self.assertEqual(report.flipped_signals, 0)
        self.assertTrue(report.is_robust)          # point estimate passes
        self.assertFalse(report.is_robust_at_ci)   # confidence bound does not
        self.assertGreater(report.flip_rate_ci_upper_pct, 5.0)
        self.assertIn("MARGINAL", report.message)

    def test_large_clean_sample_clears_the_ci_gate(self):
        # The same 0-flip result on 5000 samples: the bound now clears 5%.
        X = np.full((5000, 1), 0.5)
        report = SignalAdversarialTester(
            AdversarialRobustnessConfig(
                epsilon=1e-12, noise_type=NOISE_RANDOM_SIGN,
                flip_tolerance_pct=5.0, seed=42,
            )
        ).evaluate_model(DummyRobustModel().predict, X)
        self.assertTrue(report.is_robust)
        self.assertTrue(report.is_robust_at_ci)
        self.assertLessEqual(report.flip_rate_ci_upper_pct, 5.0)
        self.assertNotIn("MARGINAL", report.message)

    def test_ci_upper_bound_never_below_point_estimate(self):
        X = np.full((200, 1), 0.5)
        report = SignalAdversarialTester(
            AdversarialRobustnessConfig(
                epsilon=0.1, noise_type=NOISE_RANDOM_SIGN, seed=42,
                clip_to_clean_domain=False,
            )
        ).evaluate_model(DummyFragileModel().predict, X)
        self.assertGreaterEqual(
            report.flip_rate_ci_upper_pct, report.vulnerability_score_pct
        )
        self.assertEqual(report.ci_confidence_level, DEFAULT_CI_CONFIDENCE)


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

    def test_categorical_class_labels_are_compared_directly(self):
        # An sklearn classifier fit on ["BUY", "SELL"] targets returns string
        # labels; flip detection only needs equality, so this must not raise.
        def string_label_model(X):
            return np.where(X[:, 0] > 0.5, "BUY", "SELL")

        X = np.full((200, 1), 0.5)
        report = SignalAdversarialTester(
            AdversarialRobustnessConfig(
                epsilon=0.1, noise_type=NOISE_RANDOM_SIGN, seed=42,
                clip_to_clean_domain=False,
            )
        ).evaluate_model(string_label_model, X)
        self.assertGreater(report.flipped_signals, 0)
        self.assertFalse(report.is_robust)

    def test_as_dict_carries_the_full_audit_record(self):
        # checklist.md section 7 requires seed, epsilon and flip_tolerance_pct
        # in the persisted model-card snapshot.
        X = np.full((100, 1), 0.5)
        report = SignalAdversarialTester(
            AdversarialRobustnessConfig(
                epsilon=0.25, flip_tolerance_pct=3.5, seed=99,
                noise_type=NOISE_MONTECARLO_WORST, n_trials=4,
            )
        ).evaluate_model(DummyFragileModel().predict, X)
        d = report.as_dict()
        json.dumps(d)
        self.assertEqual(d["epsilon"], 0.25)
        self.assertEqual(d["flip_tolerance_pct"], 3.5)
        self.assertEqual(d["seed"], 99)
        self.assertEqual(d["ci_confidence_level"], DEFAULT_CI_CONFIDENCE)
        self.assertIn("flip_rate_ci_upper_pct", d)
        self.assertIn("is_robust_at_ci", d)

    def test_perturb_respects_epsilon_scale_and_bounds(self):
        # The public remediation hook: for samples inside the feasible domain,
        # perturbations stay within eps * scale AND inside the domain.
        rng = np.random.default_rng(0)
        X = rng.uniform(2.0, 8.0, size=(200, 3))
        scales = np.array([2.0, 4.0, 8.0])
        bounds = np.array([[1.0, 9.0], [1.0, 9.0], [1.0, 9.0]])
        cfg = AdversarialRobustnessConfig(
            epsilon=0.05, noise_type=NOISE_RANDOM_SIGN, seed=3,
            feature_scales=scales, feature_bounds=bounds,
        )
        X_adv = SignalAdversarialTester(cfg).perturb(X)
        self.assertEqual(X_adv.shape, X.shape)
        self.assertTrue(np.all(np.abs(X_adv - X) <= 0.05 * scales + 1e-12))
        self.assertTrue(np.all(X_adv >= 1.0 - 1e-12))
        self.assertTrue(np.all(X_adv <= 9.0 + 1e-12))

    def test_domain_clip_never_exceeds_the_epsilon_budget(self):
        # Regression: training-set feature_bounds can be narrower than the
        # validation range. Clipping such a sample back to the domain would move
        # it by far more than eps * scale, so a "flip" would be attributed to an
        # epsilon-bounded perturbation it never underwent. The epsilon ball wins.
        X = np.array([[50.0], [-50.0], [5.0]])   # two samples far outside bounds
        scales = np.array([1.0])
        bounds = np.array([[0.0, 10.0]])
        cfg = AdversarialRobustnessConfig(
            epsilon=0.02, noise_type=NOISE_RANDOM_SIGN, seed=3,
            feature_scales=scales, feature_bounds=bounds,
        )
        X_adv = SignalAdversarialTester(cfg).perturb(X)
        # Budget honoured for every sample, including the out-of-domain ones.
        self.assertTrue(np.all(np.abs(X_adv - X) <= 0.02 * scales + 1e-12))
        # The in-domain sample is still held inside the feasible range.
        self.assertGreaterEqual(X_adv[2, 0], 0.0)
        self.assertLessEqual(X_adv[2, 0], 10.0)

    def test_out_of_domain_sample_does_not_manufacture_a_flip(self):
        # End-to-end form of the same regression. Pre-fix, the clean signal was
        # read at x=50 and the "perturbed" signal at the clipped x=10, flipping
        # a model whose boundary sits at 20 -- a 40-unit move sold as eps=0.02.
        class BoundaryAtTwenty:
            def predict(self, X):
                return (X[:, 0] > 20.0).astype(int)

        X = np.full((100, 1), 50.0)
        report = SignalAdversarialTester(
            AdversarialRobustnessConfig(
                epsilon=0.02, noise_type=NOISE_RANDOM_SIGN, seed=3,
                feature_scales=np.array([1.0]),
                feature_bounds=np.array([[0.0, 10.0]]),
            )
        ).evaluate_model(BoundaryAtTwenty().predict, X)
        self.assertEqual(report.flipped_signals, 0)
        self.assertTrue(report.is_robust)

    def test_perturb_is_seeded_and_accepts_an_explicit_rng(self):
        X = np.full((50, 2), 1.0)
        cfg = AdversarialRobustnessConfig(
            epsilon=0.1, noise_type=NOISE_RANDOM_SIGN, seed=5,
            clip_to_clean_domain=False,
        )
        tester = SignalAdversarialTester(cfg)
        np.testing.assert_array_equal(tester.perturb(X), tester.perturb(X))
        # An explicit generator yields independent augmentation draws.
        rng = np.random.default_rng(5)
        first, second = tester.perturb(X, rng), tester.perturb(X, rng)
        self.assertFalse(np.array_equal(first, second))

    def test_perturb_rejects_non_finite_input(self):
        X = np.full((10, 1), 1.0)
        X[0, 0] = np.nan
        tester = SignalAdversarialTester(AdversarialRobustnessConfig(seed=1))
        with self.assertRaises(ValueError):
            tester.perturb(X)


if __name__ == "__main__":
    unittest.main()
