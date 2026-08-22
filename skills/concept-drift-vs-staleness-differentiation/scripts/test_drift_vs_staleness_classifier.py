"""
Tests for concept-drift-vs-staleness-differentiation.

The regression tests below are written against *independently derived* expected
values: bin proportions are constructed by hand and the expected PSI is computed
from the closed form on those proportions, so the test never re-runs the
implementation's own binning to produce its own expectation. Each regression
test also records the value the pre-fix implementation produced, so it is clear
the test discriminates between the two.
"""
import math
import unittest

import numpy as np

from drift_vs_staleness_classifier import (
    DiagnosisStatus,
    DriftVsStalenessClassifier,
)


def _psi_from_proportions(ref_pcts, curr_pcts):
    """Closed-form PSI over known bin proportions - the expectation, not the code path."""
    return sum((q - p) * math.log(q / p) for p, q in zip(ref_pcts, curr_pcts))


class TestPsiBinning(unittest.TestCase):
    """PSI must conserve the current sample's probability mass."""

    def setUp(self):
        self.classifier = DriftVsStalenessClassifier()

    def test_mass_outside_reference_support_is_counted_not_dropped(self):
        # REGRESSION. numpy.histogram ignores values outside the supplied edges,
        # so the pre-fix implementation dropped every current observation that
        # had moved beyond the historical range - exactly the observations that
        # signal a broken or regime-shifted feature.
        #
        # Construction, chosen so every bin count is exact:
        #   reference = 0..999          -> decile edges put exactly 100 per bin.
        #   current   = 600 in-range values, 60 per bin (every value whose last
        #               decimal digit is < 6), plus 400 values at 5000, far
        #               beyond the reference maximum.
        ref = np.arange(1000.0)
        in_range = ref[(ref.astype(int) % 10) < 6]           # 600 values, 60 per bin
        curr = np.concatenate([in_range, np.full(400, 5000.0)])
        self.assertEqual(in_range.size, 600)

        # Expected proportions with unbounded outer edges: the 400 far values
        # land in the top bin, so bins 0-8 hold 0.06 and bin 9 holds 0.46.
        expected = _psi_from_proportions(
            [0.1] * 10,
            [0.06] * 9 + [0.46],
        )
        self.assertAlmostEqual(expected, 0.733278, places=5)

        psi = self.classifier.calculate_psi(ref, curr)
        self.assertAlmostEqual(psi, expected, places=6)

        # The pre-fix implementation dropped the 400 out-of-range values, giving
        # 0.06 in all ten bins and a PSI of 0.2043 - below the 0.25 rule of
        # thumb, i.e. "no action required" while 40% of the feature's mass had
        # left its historical support entirely.
        pre_fix = _psi_from_proportions([0.1] * 10, [0.06] * 10)
        self.assertAlmostEqual(pre_fix, 0.204327, places=5)
        self.assertLess(pre_fix, 0.25)
        self.assertGreater(psi, 0.25)

    def test_identical_distributions_score_near_zero(self):
        ref = np.arange(1000.0)
        self.assertAlmostEqual(self.classifier.calculate_psi(ref, ref.copy()), 0.0, places=9)

    def test_constant_reference_feature_is_undefined_not_zero(self):
        # A constant reference has no quantile structure; reporting 0.0 would
        # read as "stable" for a feature that cannot be assessed at all.
        ref = np.full(500, 7.0)
        curr = np.full(500, 99.0)
        self.assertTrue(math.isnan(self.classifier.calculate_psi(ref, curr)))

    def test_low_cardinality_reference_does_not_collapse_to_a_single_bin(self):
        # REGRESSION. Quantile edges de-duplicate. A 95/5 indicator over 10 bins
        # collapses to two distinct edges - one bin spanning everything - and
        # PSI is then identically 0.0 no matter what the current sample does.
        # Sparse indicators (halt flags, regime flags, event counts) are common
        # in trading feature sets, so this was a total blind spot.
        ref = np.concatenate([np.zeros(950), np.ones(50)])
        curr = np.concatenate([np.zeros(500), np.ones(500)])

        # Two bins split at the midpoint 0.5: reference 0.95/0.05, current
        # 0.50/0.50. Expectation derived from those proportions alone.
        expected = _psi_from_proportions([0.95, 0.05], [0.5, 0.5])
        self.assertAlmostEqual(expected, 1.324998, places=5)

        psi = self.classifier.calculate_psi(ref, curr)
        self.assertAlmostEqual(psi, expected, places=6)
        self.assertGreater(psi, 0.25)

    def test_low_cardinality_reference_unchanged_scores_zero(self):
        ref = np.concatenate([np.zeros(950), np.ones(50)])
        self.assertAlmostEqual(self.classifier.calculate_psi(ref, ref.copy()), 0.0, places=9)

    def test_concentrated_high_cardinality_reference_still_bins(self):
        # 999 zeros and one outlier: the quantile edges collapse here too, but
        # the reference is not low-cardinality, so the fallback must still
        # produce usable bins rather than a single bin or a nan.
        ref = np.concatenate([np.zeros(999), [5.0]])
        curr = np.concatenate([np.zeros(500), np.linspace(1.0, 50.0, 500)])
        psi = self.classifier.calculate_psi(ref, curr)
        self.assertTrue(math.isfinite(psi))
        self.assertGreater(psi, 0.25)

    def test_calculate_psi_rejects_unusable_input(self):
        ref = np.arange(500.0)
        with self.assertRaises(ValueError):
            self.classifier.calculate_psi(ref, np.array([]))
        with self.assertRaises(ValueError):
            self.classifier.calculate_psi(ref, np.array([1.0, np.nan, 3.0]))
        with self.assertRaises(ValueError):
            self.classifier.calculate_psi(ref.reshape(-1, 1), ref.reshape(-1, 1))


class TestChiSquareCalibration(unittest.TestCase):
    """
    Benchmarks must reproduce Yurdakul and Naranjo (2020), Journal of Risk Model
    Validation 14(4), Table 2 (B = 10, alpha = 0.05) - published values this
    implementation had no part in producing.
    """

    def setUp(self):
        self.classifier = DriftVsStalenessClassifier(psi_significance_level=0.05)

    def test_matches_published_benchmark_table(self):
        for n_ref, n_curr, published in [
            (100, 100, 0.338),
            (200, 200, 0.169),
            (400, 400, 0.085),
            (1000, 1000, 0.034),
            (200, 1000, 0.102),
            (100, 1000, 0.186),
        ]:
            with self.subTest(n=n_ref, m=n_curr):
                self.assertAlmostEqual(
                    self.classifier.psi_benchmark(n_ref, n_curr, num_bins=10), published, places=3)

    def test_calibration_handles_a_low_cardinality_feature(self):
        # REGRESSION. Deriving the benchmark's bin count from collapsed quantile
        # edges yielded B = 1 and raised out of diagnose().
        rng = np.random.default_rng(1)
        ref = np.concatenate([np.zeros(950), np.ones(50)]).reshape(-1, 1)
        curr = np.concatenate([np.zeros(500), np.ones(500)]).reshape(-1, 1)
        residuals = rng.normal(0, 1, 500)
        result = self.classifier.diagnose(0.0, 1.0, ref, curr, residuals, residuals.copy())
        self.assertEqual(result.status, DiagnosisStatus.COVARIATE_SHIFT)

    def test_benchmark_requires_calibration_to_be_enabled(self):
        with self.assertRaises(ValueError):
            DriftVsStalenessClassifier().psi_benchmark(500, 500, num_bins=10)

    def test_calibrated_trigger_catches_shift_the_fixed_rule_misses(self):
        # The fixed 0.10/0.25 bands have no controlled error rate and their
        # power falls as samples grow. Construction: 5% of the reference mass
        # moves from the bottom decile to the top decile, over 5000 samples.
        #   bins: reference 0.10 each; current 0.05 in bin 0, 0.15 in bin 9.
        expected_psi = _psi_from_proportions(
            [0.1] * 10, [0.05] + [0.1] * 8 + [0.15])
        self.assertAlmostEqual(expected_psi, 0.054930, places=5)
        self.assertLess(expected_psi, 0.25)   # invisible to the rule of thumb

        ref = np.repeat(np.arange(1000.0), 5)                      # 5000, 500 per decile
        curr = ref.copy()
        moved = np.flatnonzero(curr < 100)[:250]                   # 250 = 5% of 5000
        curr[moved] = 5000.0                                       # into the top bin
        psi = DriftVsStalenessClassifier().calculate_psi(ref, curr)
        self.assertAlmostEqual(psi, expected_psi, places=6)

        benchmark = self.classifier.psi_benchmark(ref.size, curr.size, num_bins=10)
        self.assertAlmostEqual(benchmark, (2.0 / 5000.0) * 16.9190, places=4)
        self.assertGreater(psi, benchmark)


class TestFeatureShiftAggregation(unittest.TestCase):

    def test_single_broken_feature_is_not_averaged_away(self):
        # REGRESSION. The pre-fix implementation triggered on the mean PSI
        # across the feature universe; one fully relocated feature out of 100
        # produced a mean of 0.075 and a STABLE verdict.
        rng = np.random.default_rng(7)
        X_ref = rng.normal(0, 1, (3000, 100))
        X_curr = rng.normal(0, 1, (3000, 100))
        X_curr[:, 7] = rng.normal(9, 1, 3000)
        names = [f"f{i}" for i in range(100)]

        result = DriftVsStalenessClassifier().diagnose(
            feature_timestamp_sec=1000.0, current_timestamp_sec=1010.0,
            X_ref=X_ref, X_curr=X_curr,
            residuals_ref=rng.normal(0, 1, 500), residuals_curr=rng.normal(0, 1, 500),
            feature_names=names,
        )
        self.assertEqual(result.status, DiagnosisStatus.COVARIATE_SHIFT)
        self.assertEqual(result.shifted_features, ("f7",))
        self.assertGreater(result.max_feature_psi, 0.25)
        # The mean is still reported and is still below the trigger - which is
        # precisely why it must not be the trigger.
        self.assertLess(result.mean_feature_psi, 0.25)
        self.assertIn("f7", result.recommended_action)

    def test_constant_feature_is_reported_separately(self):
        rng = np.random.default_rng(3)
        X_ref = rng.normal(0, 1, (500, 2))
        X_curr = rng.normal(0, 1, (500, 2))
        X_ref[:, 1] = 4.0
        X_curr[:, 1] = 4.0

        result = DriftVsStalenessClassifier().diagnose(
            1000.0, 1001.0, X_ref, X_curr,
            rng.normal(0, 1, 500), rng.normal(0, 1, 500),
            feature_names=["live", "frozen"],
        )
        self.assertEqual(result.degenerate_features, ("frozen",))
        self.assertNotIn("frozen", result.feature_psi_scores)
        self.assertEqual(result.status, DiagnosisStatus.STABLE)

    def test_all_features_constant_is_insufficient_data(self):
        X = np.full((500, 2), 4.0)
        rng = np.random.default_rng(3)
        result = DriftVsStalenessClassifier().diagnose(
            1000.0, 1001.0, X, X.copy(), rng.normal(0, 1, 500), rng.normal(0, 1, 500))
        self.assertEqual(result.status, DiagnosisStatus.INSUFFICIENT_DATA)
        self.assertIsNone(result.max_feature_psi)


class TestClassification(unittest.TestCase):

    def setUp(self):
        self.classifier = DriftVsStalenessClassifier(
            max_staleness_sec=300.0, psi_threshold=0.25, error_ratio_threshold=1.50)
        self.rng = np.random.default_rng(42)

    def _stable_inputs(self):
        return dict(
            X_ref=self.rng.normal(0, 1, (500, 2)),
            X_curr=self.rng.normal(0, 1, (500, 2)),
            residuals_ref=self.rng.normal(0, 1, 500),
            residuals_curr=self.rng.normal(0, 1, 500),
        )

    def test_data_staleness_short_circuits_and_reports_nothing_it_did_not_measure(self):
        result = self.classifier.diagnose(
            feature_timestamp_sec=1000.0, current_timestamp_sec=1600.0, **self._stable_inputs())
        self.assertEqual(result.status, DiagnosisStatus.DATA_STALENESS)
        self.assertEqual(result.staleness_age_sec, 600.0)
        # REGRESSION: the pre-fix result reported psi 0.0 and error ratio 1.0
        # for a snapshot on which neither statistic was ever computed.
        self.assertIsNone(result.max_feature_psi)
        self.assertIsNone(result.mean_feature_psi)
        self.assertIsNone(result.error_mse_ratio)
        self.assertIn("Do NOT retrain", result.recommended_action)

    def test_staleness_boundary_is_exclusive(self):
        result = self.classifier.diagnose(1000.0, 1300.0, **self._stable_inputs())
        self.assertNotEqual(result.status, DiagnosisStatus.DATA_STALENESS)

    def test_future_dated_feature_timestamp_is_clock_skew(self):
        # REGRESSION. A negative age passed the `age > limit` test unchallenged,
        # and the pre-fix code went on to score data of unknown vintage.
        result = self.classifier.diagnose(2000.0, 1000.0, **self._stable_inputs())
        self.assertEqual(result.status, DiagnosisStatus.CLOCK_SKEW)
        self.assertEqual(result.staleness_age_sec, -1000.0)
        self.assertIsNone(result.error_mse_ratio)

    def test_clock_skew_tolerance_absorbs_configured_jitter(self):
        tolerant = DriftVsStalenessClassifier(clock_skew_tolerance_sec=2.0)
        self.assertNotEqual(
            tolerant.diagnose(1001.0, 1000.0, **self._stable_inputs()).status,
            DiagnosisStatus.CLOCK_SKEW)
        self.assertEqual(
            tolerant.diagnose(1005.0, 1000.0, **self._stable_inputs()).status,
            DiagnosisStatus.CLOCK_SKEW)

    def test_covariate_shift(self):
        inputs = self._stable_inputs()
        inputs["X_curr"] = self.rng.normal(5, 1, (500, 2))
        result = self.classifier.diagnose(1000.0, 1010.0, **inputs)
        self.assertEqual(result.status, DiagnosisStatus.COVARIATE_SHIFT)
        self.assertGreater(result.max_feature_psi, 0.25)
        self.assertLess(result.error_mse_ratio, 1.50)

    def test_concept_drift(self):
        inputs = self._stable_inputs()
        inputs["residuals_curr"] = self.rng.normal(0, 3, 500)
        result = self.classifier.diagnose(1000.0, 1010.0, **inputs)
        self.assertEqual(result.status, DiagnosisStatus.CONCEPT_DRIFT)
        self.assertGreater(result.error_mse_ratio, 1.50)
        self.assertEqual(result.shifted_features, ())

    def test_concept_drift_wins_when_both_signals_breach_and_says_so(self):
        inputs = self._stable_inputs()
        inputs["X_curr"] = self.rng.normal(5, 1, (500, 2))
        inputs["residuals_curr"] = self.rng.normal(0, 3, 500)
        result = self.classifier.diagnose(1000.0, 1010.0, **inputs)
        self.assertEqual(result.status, DiagnosisStatus.CONCEPT_DRIFT)
        self.assertTrue(result.shifted_features)
        self.assertIn("extrapolation", result.recommended_action)

    def test_stable(self):
        result = self.classifier.diagnose(1000.0, 1010.0, **self._stable_inputs())
        self.assertEqual(result.status, DiagnosisStatus.STABLE)
        self.assertEqual(result.shifted_features, ())

    def test_error_ratio_threshold_is_inclusive(self):
        # MSE(ref) = 1, MSE(curr) = 1.5 exactly. Built from squares that are
        # exact in binary (1 and 4) so the boundary is not decided by rounding:
        # (100 * 4 + 500 * 1) / 600 = 1.5.
        inputs = self._stable_inputs()
        inputs["residuals_ref"] = np.ones(600)
        inputs["residuals_curr"] = np.concatenate([np.full(100, 2.0), np.ones(500)])
        result = self.classifier.diagnose(1000.0, 1010.0, **inputs)
        self.assertEqual(result.error_mse_ratio, 1.5)
        self.assertEqual(result.status, DiagnosisStatus.CONCEPT_DRIFT)

    def test_status_still_compares_equal_to_the_v1_string_literals(self):
        result = self.classifier.diagnose(1000.0, 1010.0, **self._stable_inputs())
        self.assertEqual(result.status, "STABLE")


class TestUnusableInputIsNeverScoredStable(unittest.TestCase):

    def setUp(self):
        self.classifier = DriftVsStalenessClassifier()
        self.rng = np.random.default_rng(11)

    def _inputs(self):
        return dict(
            X_ref=self.rng.normal(0, 1, (500, 2)),
            X_curr=self.rng.normal(0, 1, (500, 2)),
            residuals_ref=self.rng.normal(0, 1, 500),
            residuals_curr=self.rng.normal(0, 1, 500),
        )

    def test_nan_residual_is_insufficient_data_not_stable(self):
        # REGRESSION. A NaN MSE ratio makes every `>=` comparison False, so the
        # pre-fix implementation fell through to STABLE on poisoned data.
        inputs = self._inputs()
        inputs["residuals_curr"][17] = np.nan
        result = self.classifier.diagnose(1000.0, 1010.0, **inputs)
        self.assertEqual(result.status, DiagnosisStatus.INSUFFICIENT_DATA)
        self.assertIn("residuals_curr", result.detail)
        self.assertIsNone(result.error_mse_ratio)

    def test_infinite_feature_value_is_insufficient_data(self):
        inputs = self._inputs()
        inputs["X_curr"][3, 1] = np.inf
        self.assertEqual(
            self.classifier.diagnose(1000.0, 1010.0, **inputs).status,
            DiagnosisStatus.INSUFFICIENT_DATA)

    def test_empty_residuals_are_insufficient_data(self):
        # REGRESSION. The pre-fix implementation substituted MSE = 1.0 for an
        # empty residual array and produced a confident ratio of 1.0.
        inputs = self._inputs()
        inputs["residuals_ref"] = np.array([])
        inputs["residuals_curr"] = np.array([])
        result = self.classifier.diagnose(1000.0, 1010.0, **inputs)
        self.assertEqual(result.status, DiagnosisStatus.INSUFFICIENT_DATA)
        self.assertIsNone(result.error_mse_ratio)

    def test_sample_below_min_samples_is_insufficient_data(self):
        inputs = self._inputs()
        inputs["residuals_curr"] = self.rng.normal(0, 1, 99)
        self.assertEqual(
            self.classifier.diagnose(1000.0, 1010.0, **inputs).status,
            DiagnosisStatus.INSUFFICIENT_DATA)

    def test_zero_reference_mse_does_not_explode_into_concept_drift(self):
        # REGRESSION. The pre-fix `mse_curr / (mse_ref + 1e-8)` guard turned a
        # zero-residual reference window into a ratio of ~1e8 and a confident
        # CONCEPT_DRIFT verdict.
        inputs = self._inputs()
        inputs["residuals_ref"] = np.zeros(500)
        result = self.classifier.diagnose(1000.0, 1010.0, **inputs)
        self.assertEqual(result.status, DiagnosisStatus.INSUFFICIENT_DATA)
        self.assertIsNone(result.error_mse_ratio)

    def test_staleness_is_resolved_before_data_condition_checks(self):
        # A stale feed replays the reference distribution and looks pristine, so
        # staleness must win even when the snapshot is otherwise unusable.
        inputs = self._inputs()
        inputs["residuals_curr"][0] = np.nan
        self.assertEqual(
            self.classifier.diagnose(1000.0, 1600.0, **inputs).status,
            DiagnosisStatus.DATA_STALENESS)


class TestStructuralValidation(unittest.TestCase):

    def setUp(self):
        self.classifier = DriftVsStalenessClassifier()
        self.rng = np.random.default_rng(5)
        self.res = self.rng.normal(0, 1, 500)

    def test_mismatched_feature_counts_raise(self):
        # REGRESSION. The pre-fix code indexed X_curr by X_ref's column range
        # and raised a bare IndexError, or silently scored the wrong columns.
        with self.assertRaises(ValueError):
            self.classifier.diagnose(
                1000.0, 1010.0,
                self.rng.normal(0, 1, (500, 3)), self.rng.normal(0, 1, (500, 2)),
                self.res, self.res)

    def test_three_dimensional_features_raise(self):
        with self.assertRaises(ValueError):
            self.classifier.diagnose(
                1000.0, 1010.0,
                self.rng.normal(0, 1, (10, 5, 2)), self.rng.normal(0, 1, (10, 5, 2)),
                self.res, self.res)

    def test_one_dimensional_features_are_read_as_a_single_feature(self):
        result = self.classifier.diagnose(
            1000.0, 1010.0,
            self.rng.normal(0, 1, 500), self.rng.normal(0, 1, 500), self.res, self.res)
        self.assertEqual(list(result.feature_psi_scores), ["feature_0"])

    def test_bad_feature_names_raise(self):
        X = self.rng.normal(0, 1, (500, 2))
        with self.assertRaises(ValueError):
            self.classifier.diagnose(1000.0, 1010.0, X, X.copy(), self.res, self.res,
                                     feature_names=["only_one"])
        with self.assertRaises(ValueError):
            self.classifier.diagnose(1000.0, 1010.0, X, X.copy(), self.res, self.res,
                                     feature_names=["dup", "dup"])

    def test_non_finite_timestamps_raise(self):
        X = self.rng.normal(0, 1, (500, 2))
        for bad in (float("nan"), float("inf"), "1000", None):
            with self.subTest(bad=bad):
                with self.assertRaises(ValueError):
                    self.classifier.diagnose(bad, 1010.0, X, X.copy(), self.res, self.res)

    def test_numpy_scalar_timestamps_are_accepted(self):
        # A timestamp read straight out of a DataFrame column is an np.int64,
        # which is not a Python int; rejecting it would break ordinary callers.
        X = self.rng.normal(0, 1, (500, 2))
        result = self.classifier.diagnose(
            np.int64(1000), np.float64(1010.0), X, X.copy(), self.res, self.res)
        self.assertEqual(result.status, DiagnosisStatus.STABLE)

    def test_invalid_configuration_raises(self):
        for kwargs in (
            {"max_staleness_sec": -1.0},
            {"psi_threshold": 0.0},
            {"error_ratio_threshold": -2.0},
            {"num_bins": 1},
            {"min_samples": 1},
            {"clock_skew_tolerance_sec": -0.5},
            {"psi_significance_level": 0.0},
            {"psi_significance_level": 1.0},
        ):
            with self.subTest(**kwargs):
                with self.assertRaises(ValueError):
                    DriftVsStalenessClassifier(**kwargs)


if __name__ == "__main__":
    unittest.main()
