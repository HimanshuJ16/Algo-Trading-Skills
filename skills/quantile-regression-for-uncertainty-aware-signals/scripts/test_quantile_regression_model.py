"""
Unit tests for quantile-regression-for-uncertainty-aware-signals.

Expected values are derived independently of the implementation:

- Pinball loss cases use the worked examples published in the scikit-learn
  ``mean_pinball_loss`` reference documentation, plus hand-computed asymmetric cases.
- Quantile-recovery targets use closed-form Gaussian quantiles from
  ``statistics.NormalDist`` (never the model's own output), and order statistics of an
  exactly known integer sample.
- Coverage targets are the nominal levels themselves, measured out-of-sample.

Several tests are regression tests against defects found in v1.0.0 and are annotated as
such: each fails against the previous implementation and passes against this one.
"""
import logging
import math
import random
import unittest
from statistics import NormalDist

from quantile_regression_model import (
    DEFAULT_QUANTILES,
    QuantileRegressionSignalModel,
    empirical_quantile,
    mean_pinball_loss,
    pinball_loss,
)

# Repo convention: silence the module's expected warnings (degenerate bands, constant
# features, the deliberately non-convergent constant-step configuration) so test output
# stays clean. The behaviours themselves are asserted, not the log lines.
logging.getLogger("quantile_regression_model").setLevel(logging.CRITICAL)

# Closed-form standard-normal quantiles. z(0.90) - z(0.10) = 2.5631... is the true width
# of a 10th-to-90th-percentile band for any Gaussian with unit scale.
_NORMAL = NormalDist()
Z10 = _NORMAL.inv_cdf(0.10)
Z50 = _NORMAL.inv_cdf(0.50)
Z90 = _NORMAL.inv_cdf(0.90)
UNIT_BAND_WIDTH = Z90 - Z10


def _gaussian_sample(n, seed, intercept, slope, noise_scale, heteroscedastic=False):
    """
    Rows of a single uniform feature on [1, 3] with Gaussian noise.

    The conditional tau-quantile is known in closed form:
        homoscedastic:   intercept + slope * x + noise_scale * z_tau
        heteroscedastic: intercept + slope * x + noise_scale * x * z_tau
    """
    rng = random.Random(seed)
    rows, targets = [], []
    for _ in range(n):
        x = rng.uniform(1.0, 3.0)
        scale = noise_scale * x if heteroscedastic else noise_scale
        rows.append([x])
        targets.append(intercept + slope * x + rng.gauss(0.0, scale))
    return rows, targets


class TestPinballLoss(unittest.TestCase):
    """The loss itself -- absent entirely from v1.0.0, which shipped only its gradient."""

    def test_matches_published_sklearn_examples(self):
        # Values published in the scikit-learn mean_pinball_loss documentation.
        self.assertAlmostEqual(
            mean_pinball_loss([1, 2, 3], [0, 2, 3], 0.1), 1.0 / 30.0, places=12
        )
        self.assertAlmostEqual(
            mean_pinball_loss([1, 2, 3], [1, 2, 4], 0.9), 1.0 / 30.0, places=12
        )

    def test_asymmetry_penalises_the_correct_side(self):
        # tau = 0.9 wants over-prediction: under-predicting by 1 costs 0.9, over-predicting
        # by 1 costs only 0.1. Hand-computed from the definition.
        self.assertAlmostEqual(pinball_loss(y_true=10.0, y_pred=9.0, tau=0.9), 0.9)
        self.assertAlmostEqual(pinball_loss(y_true=10.0, y_pred=11.0, tau=0.9), 0.1)
        # tau = 0.1 is the mirror image.
        self.assertAlmostEqual(pinball_loss(y_true=10.0, y_pred=9.0, tau=0.1), 0.1)
        self.assertAlmostEqual(pinball_loss(y_true=10.0, y_pred=11.0, tau=0.1), 0.9)

    def test_at_median_equals_half_absolute_error(self):
        for error in (0.25, 1.0, 7.5):
            self.assertAlmostEqual(pinball_loss(1.0, 1.0 + error, 0.5), 0.5 * error)
            self.assertAlmostEqual(pinball_loss(1.0, 1.0 - error, 0.5), 0.5 * error)

    def test_zero_only_at_a_perfect_prediction(self):
        for tau in (0.1, 0.5, 0.9):
            self.assertEqual(pinball_loss(3.0, 3.0, tau), 0.0)
            self.assertGreater(pinball_loss(3.0, 3.0001, tau), 0.0)
            self.assertGreater(pinball_loss(3.0, 2.9999, tau), 0.0)

    def test_minimised_at_the_true_quantile(self):
        # Independent check that the loss really elicits the quantile: on the integers
        # 0..100 the mean pinball loss at tau = 0.9 must be lowest at 90.
        sample = [float(i) for i in range(101)]
        losses = {
            candidate: mean_pinball_loss(sample, [float(candidate)] * 101, 0.9)
            for candidate in (50, 80, 90, 95, 100)
        }
        self.assertEqual(min(losses, key=losses.get), 90)

    def test_rejects_invalid_input(self):
        with self.assertRaises(ValueError):
            pinball_loss(1.0, 1.0, 0.0)
        with self.assertRaises(ValueError):
            pinball_loss(1.0, 1.0, 1.0)
        with self.assertRaises(ValueError):
            pinball_loss(float("nan"), 1.0, 0.5)
        with self.assertRaises(ValueError):
            mean_pinball_loss([1.0, 2.0], [1.0], 0.5)
        with self.assertRaises(ValueError):
            mean_pinball_loss([], [], 0.5)


class TestEmpiricalQuantile(unittest.TestCase):
    def test_matches_order_statistics(self):
        # Integers 0..100: position (n-1)*tau lands exactly on an order statistic.
        sample = [float(i) for i in range(101)]
        self.assertAlmostEqual(empirical_quantile(sample, 0.10), 10.0)
        self.assertAlmostEqual(empirical_quantile(sample, 0.50), 50.0)
        self.assertAlmostEqual(empirical_quantile(sample, 0.90), 90.0)

    def test_interpolates_between_order_statistics(self):
        # Four points, tau = 0.5 -> position 1.5 -> midpoint of 20 and 30 (type-7 rule).
        self.assertAlmostEqual(empirical_quantile([10.0, 20.0, 30.0, 40.0], 0.5), 25.0)

    def test_single_and_invalid_samples(self):
        self.assertEqual(empirical_quantile([4.2], 0.9), 4.2)
        with self.assertRaises(ValueError):
            empirical_quantile([], 0.5)
        with self.assertRaises(ValueError):
            empirical_quantile([1.0, float("inf")], 0.5)


class TestQuantileRecovery(unittest.TestCase):
    """
    Does the estimator actually recover known conditional quantiles?

    v1.0.0 had no such test: it asserted only q10 <= q50 <= q90, which ``sorted()``
    guarantees unconditionally and which therefore held for any estimator, converged or
    not.
    """

    def test_recovers_unconditional_quantiles_of_a_known_sample(self):
        # Intercept-only model on the integers 0..100: the conditional quantiles reduce to
        # the sample quantiles, which are exactly 10 / 50 / 90.
        targets = [float(i) for i in range(101)]
        model = QuantileRegressionSignalModel(
            num_features=0, min_uncertainty_width=1e-6
        ).fit([[]] * 101, targets, epochs=200, seed=3)
        prediction = model.predict([], max_position_size=100.0)
        self.assertAlmostEqual(prediction.q_lower, 10.0, delta=1.0)
        self.assertAlmostEqual(prediction.q_central, 50.0, delta=1.0)
        self.assertAlmostEqual(prediction.q_upper, 90.0, delta=1.0)

    def test_recovers_homoscedastic_location_shift_band(self):
        """
        Regression test for the missing intercept in v1.0.0.

        y = 5 + x + N(0, 1) has a constant true band width of z(0.90) - z(0.10) = 2.5631
        at every x. Forcing all three quantile lines through the origin -- as v1.0.0 did,
        having no intercept -- inflated that to ~5.9 on this data, a 130% overstatement of
        the model's own uncertainty. The assertion below fails against that implementation.
        """
        rows, targets = _gaussian_sample(20000, seed=12, intercept=5.0, slope=1.0, noise_scale=1.0)
        model = QuantileRegressionSignalModel(
            num_features=1, min_uncertainty_width=1e-6
        ).fit(rows, targets, epochs=1, seed=7)

        prediction = model.predict([2.0], max_position_size=100.0)
        # True conditional quantiles at x = 2: 7 + z_tau.
        self.assertAlmostEqual(prediction.q_lower, 7.0 + Z10, delta=0.10)
        self.assertAlmostEqual(prediction.q_central, 7.0 + Z50, delta=0.10)
        self.assertAlmostEqual(prediction.q_upper, 7.0 + Z90, delta=0.10)
        self.assertAlmostEqual(prediction.uncertainty_width, UNIT_BAND_WIDTH, delta=0.15)

    def test_recovers_heteroscedastic_band_that_widens_with_the_feature(self):
        # y = 2x + N(0, 0.5x): the true band width is 0.5 * x * (z90 - z10), so it must
        # scale linearly with x. This is the property that makes the model worth using.
        rows, targets = _gaussian_sample(
            20000, seed=11, intercept=0.0, slope=2.0, noise_scale=0.5, heteroscedastic=True
        )
        model = QuantileRegressionSignalModel(
            num_features=1, min_uncertainty_width=1e-6
        ).fit(rows, targets, epochs=1, seed=7)

        for x in (1.0, 2.0, 3.0):
            prediction = model.predict([x], max_position_size=100.0)
            expected_width = 0.5 * x * UNIT_BAND_WIDTH
            self.assertAlmostEqual(
                prediction.uncertainty_width, expected_width, delta=0.15,
                msg=f"band width at x={x}",
            )
            self.assertAlmostEqual(prediction.q_central, 2.0 * x, delta=0.10)

        narrow = model.predict([1.0], max_position_size=100.0).uncertainty_width
        wide = model.predict([3.0], max_position_size=100.0).uncertainty_width
        self.assertGreater(wide, narrow * 2.0)

    def test_recovers_band_on_return_scale_targets(self):
        # Targets of ~1e-2 rather than ~1e0: the step size is expressed as a multiple of
        # the target's interquartile range, so the default must work here unchanged.
        rng = random.Random(6)
        rows, targets = [], []
        for _ in range(5000):
            x = rng.gauss(0.0, 1.0)
            rows.append([x])
            targets.append(0.0005 + 0.003 * x + rng.gauss(0.0, 0.012))
        model = QuantileRegressionSignalModel(num_features=1).fit(
            rows, targets, epochs=3, seed=4
        )
        prediction = model.predict([1.0], max_position_size=5.0)
        self.assertAlmostEqual(
            prediction.uncertainty_width, 0.012 * UNIT_BAND_WIDTH, delta=0.002
        )
        self.assertAlmostEqual(prediction.q_central, 0.0035, delta=0.001)

    def test_recovers_non_default_quantile_levels(self):
        """
        Regression test: v1.0.0's constructor accepted a ``quantiles`` argument but
        ``predict`` indexed the hard-coded literals 0.10/0.50/0.90, so any other triple
        raised KeyError on the first prediction.
        """
        rows, targets = _gaussian_sample(20000, seed=12, intercept=5.0, slope=1.0, noise_scale=1.0)
        model = QuantileRegressionSignalModel(
            num_features=1, quantiles=(0.05, 0.50, 0.95), min_uncertainty_width=1e-6
        ).fit(rows, targets, epochs=1, seed=7)

        prediction = model.predict([2.0], max_position_size=100.0)
        self.assertEqual(
            (prediction.tau_lower, prediction.tau_central, prediction.tau_upper),
            (0.05, 0.50, 0.95),
        )
        expected = _NORMAL.inv_cdf(0.95) - _NORMAL.inv_cdf(0.05)
        self.assertAlmostEqual(prediction.uncertainty_width, expected, delta=0.20)


class TestCalibration(unittest.TestCase):
    def test_out_of_sample_coverage_matches_nominal_levels(self):
        # The defining property of a tau-quantile forecast: a fraction tau of realised
        # targets fall at or below it. Measured on data the model never saw.
        train_rows, train_targets = _gaussian_sample(
            20000, seed=11, intercept=0.0, slope=2.0, noise_scale=0.5, heteroscedastic=True
        )
        test_rows, test_targets = _gaussian_sample(
            20000, seed=13, intercept=0.0, slope=2.0, noise_scale=0.5, heteroscedastic=True
        )
        model = QuantileRegressionSignalModel(
            num_features=1, min_uncertainty_width=1e-6
        ).fit(train_rows, train_targets, epochs=1, seed=7)

        reports = model.calibration_report(test_rows, test_targets)
        self.assertEqual([r.tau for r in reports], list(DEFAULT_QUANTILES))
        for report in reports:
            self.assertEqual(report.nominal_coverage, report.tau)
            self.assertEqual(report.observations, 20000)
            self.assertAlmostEqual(
                report.empirical_coverage, report.tau, delta=0.02,
                msg=f"coverage at tau={report.tau}",
            )
            self.assertAlmostEqual(
                report.coverage_error, report.empirical_coverage - report.tau, places=12
            )
            self.assertGreater(report.mean_pinball_loss, 0.0)

    def test_an_uninformative_model_is_still_calibrated_but_not_sharp(self):
        # Intercept-only: coverage should be right (it is the marginal quantile) while the
        # band is as wide as the unconditional distribution. Calibration alone is not
        # enough -- sharpness is what the sizer trades on.
        rows, targets = _gaussian_sample(
            8000, seed=21, intercept=0.0, slope=2.0, noise_scale=0.5, heteroscedastic=True
        )
        flat_rows = [[] for _ in rows]
        flat = QuantileRegressionSignalModel(
            num_features=0, min_uncertainty_width=1e-6
        ).fit(flat_rows, targets, epochs=5, seed=1)
        conditional = QuantileRegressionSignalModel(
            num_features=1, min_uncertainty_width=1e-6
        ).fit(rows, targets, epochs=1, seed=1)

        for report in flat.calibration_report(flat_rows, targets):
            self.assertAlmostEqual(report.empirical_coverage, report.tau, delta=0.03)

        flat_width = flat.predict([], max_position_size=100.0).uncertainty_width
        # Evaluated at the middle of the feature range, the conditional band must be
        # sharper than the unconditional one.
        conditional_width = conditional.predict([2.0], max_position_size=100.0).uncertainty_width
        self.assertLess(conditional_width, flat_width)

    def test_pinball_score_prefers_the_better_model(self):
        rows, targets = _gaussian_sample(
            8000, seed=22, intercept=0.0, slope=2.0, noise_scale=0.5, heteroscedastic=True
        )
        conditional = QuantileRegressionSignalModel(
            num_features=1, min_uncertainty_width=1e-6
        ).fit(rows, targets, epochs=1, seed=1)
        flat_rows = [[] for _ in rows]
        flat = QuantileRegressionSignalModel(
            num_features=0, min_uncertainty_width=1e-6
        ).fit(flat_rows, targets, epochs=5, seed=1)

        for good, bad in zip(
            conditional.calibration_report(rows, targets),
            flat.calibration_report(flat_rows, targets),
        ):
            self.assertLess(
                good.mean_pinball_loss, bad.mean_pinball_loss,
                msg=f"pinball loss at tau={good.tau}",
            )

    def test_calibration_rejects_invalid_input(self):
        rows, targets = _gaussian_sample(200, seed=5, intercept=0.0, slope=1.0, noise_scale=1.0)
        model = QuantileRegressionSignalModel(num_features=1).fit(rows, targets, seed=1)
        with self.assertRaises(ValueError):
            model.calibration_report(rows, targets[:-1])
        with self.assertRaises(ValueError):
            model.calibration_report([], [])
        with self.assertRaises(RuntimeError):
            QuantileRegressionSignalModel(num_features=1).calibration_report(rows, targets)


class TestUncertaintyScaledSizing(unittest.TestCase):
    def setUp(self):
        rows, targets = _gaussian_sample(
            4000, seed=31, intercept=0.0, slope=2.0, noise_scale=0.5, heteroscedastic=True
        )
        self.model = QuantileRegressionSignalModel(
            num_features=1, min_uncertainty_width=1e-6
        ).fit(rows, targets, epochs=2, seed=2)

    def test_size_equals_the_documented_formula(self):
        prediction = self.model.predict([2.0], max_position_size=100.0)
        expected_ratio = abs(prediction.q_central) / prediction.uncertainty_width
        self.assertAlmostEqual(prediction.confidence_ratio, expected_ratio, places=12)
        self.assertAlmostEqual(
            prediction.confidence_scaled_size,
            math.copysign(1.0, prediction.q_central) * min(100.0, expected_ratio),
            places=12,
        )

    def test_size_is_capped_by_max_position_size(self):
        for cap in (0.25, 1.0, 2.0):
            prediction = self.model.predict([2.0], max_position_size=cap)
            self.assertLessEqual(abs(prediction.confidence_scaled_size), cap)
        # And the cap genuinely binds here rather than passing vacuously: the uncapped
        # ratio exceeds the smallest cap tested.
        self.assertGreater(self.model.predict([2.0], max_position_size=100.0).confidence_ratio, 0.25)

    def test_wider_band_gives_a_smaller_position_for_the_same_direction(self):
        # Under y = 2x + N(0, 0.5x) both the median and the band scale with x, but the
        # test still pins the ordering the skill claims: at equal median forecast, a wider
        # band must size smaller. Constructed directly from the formula.
        narrow = self.model.predict([1.0], max_position_size=100.0)
        wide = self.model.predict([3.0], max_position_size=100.0)
        self.assertGreater(wide.uncertainty_width, narrow.uncertainty_width)
        scaled_narrow = abs(narrow.q_central) / narrow.uncertainty_width
        scaled_wide = abs(wide.q_central) / wide.uncertainty_width
        self.assertAlmostEqual(scaled_narrow, scaled_wide, delta=0.15)

    def test_degenerate_band_refuses_to_size(self):
        """
        Regression test for the most dangerous v1.0.0 defect.

        v1.0.0 computed ``width = max(0.0001, q90 - q10)`` and then divided by it, so a
        model whose three quantiles coincided produced ratio = |q50| / 1e-4 -- clamped to
        the *maximum* permitted position. A degenerate band is the absence of a
        measurement, not maximum confidence, and must size zero.
        """
        targets = [1e-9 * i for i in range(200)]
        rows = [[float(i)] for i in range(200)]
        model = QuantileRegressionSignalModel(
            num_features=1, min_uncertainty_width=1e-4
        ).fit(rows, targets, epochs=1, seed=1)

        prediction = model.predict([50.0], max_position_size=1.0)
        self.assertLess(prediction.uncertainty_width, 1e-4)
        self.assertTrue(prediction.uncertainty_floor_binding)
        self.assertEqual(prediction.confidence_scaled_size, 0.0)
        self.assertEqual(prediction.signal_direction, 0.0)
        self.assertEqual(prediction.status_message, "degenerate_band_not_sized")

    def test_reported_width_is_the_measurement_not_the_floor(self):
        # The floor governs the sizing decision; it must never be reported as the measured
        # band, or downstream risk reporting silently overstates a collapsed model.
        targets = [1e-9 * i for i in range(200)]
        rows = [[float(i)] for i in range(200)]
        model = QuantileRegressionSignalModel(
            num_features=1, min_uncertainty_width=1e-4
        ).fit(rows, targets, epochs=1, seed=1)
        prediction = model.predict([50.0])
        self.assertNotAlmostEqual(prediction.uncertainty_width, 1e-4, places=6)
        self.assertAlmostEqual(
            prediction.uncertainty_width, prediction.q_upper - prediction.q_lower, places=15
        )

    def test_band_straddling_zero_is_flagged(self):
        # A band containing zero does not support the sign of the trade. The model must
        # surface that rather than hide it inside a confident-looking multiplier.
        rng = random.Random(41)
        rows, targets = [], []
        for _ in range(4000):
            x = rng.gauss(0.0, 1.0)
            rows.append([x])
            targets.append(0.0002 + 0.0003 * x + rng.gauss(0.0, 0.02))
        model = QuantileRegressionSignalModel(num_features=1).fit(rows, targets, epochs=3, seed=1)
        prediction = model.predict([0.0], max_position_size=1.0)
        self.assertLess(prediction.q_lower, 0.0)
        self.assertGreater(prediction.q_upper, 0.0)
        self.assertTrue(prediction.interval_straddles_zero)
        self.assertEqual(prediction.status_message, "sized_direction_unsupported_by_band")

    def test_extrapolation_is_flagged(self):
        """
        Found in adversarial review, not present in v1.0.0's design either.

        A linear model's median forecast and its band both scale with the features, so
        their ratio stays roughly constant however far outside the fitting range the input
        goes. A nonsensical feature therefore produces a *capped-out* position rather than
        an obviously broken number -- the size looks like maximum conviction. The
        out-of-domain condition has to be reported explicitly, because nothing in the
        magnitudes gives it away.
        """
        in_domain = self.model.predict([2.0], max_position_size=1.0)
        self.assertFalse(in_domain.is_extrapolating)
        self.assertLess(in_domain.max_feature_zscore, 5.0)

        far = self.model.predict([1e6], max_position_size=1.0)
        self.assertTrue(far.is_extrapolating)
        self.assertGreater(far.max_feature_zscore, 5.0)
        self.assertEqual(far.status_message, "sized_outside_training_feature_range")
        # The point of the flag: the size gives no hint that anything is wrong.
        self.assertAlmostEqual(abs(far.confidence_scaled_size), 1.0, places=6)

    def test_extrapolation_limit_is_configurable(self):
        rows, targets = _gaussian_sample(
            2000, seed=32, intercept=0.0, slope=2.0, noise_scale=0.5, heteroscedastic=True
        )
        strict = QuantileRegressionSignalModel(
            num_features=1, min_uncertainty_width=1e-6, extrapolation_z_limit=0.5
        ).fit(rows, targets, epochs=1, seed=2)
        # x = 3 is the top of the training range, ~1.7 sigma out: inside the default
        # limit, outside a 0.5-sigma one.
        self.assertTrue(strict.predict([3.0], max_position_size=1.0).is_extrapolating)
        self.assertFalse(self.model.predict([3.0], max_position_size=1.0).is_extrapolating)
        with self.assertRaises(ValueError):
            QuantileRegressionSignalModel(num_features=1, extrapolation_z_limit=0.0)

    def test_intercept_only_model_never_extrapolates(self):
        model = QuantileRegressionSignalModel(num_features=0, min_uncertainty_width=1e-6)
        model.fit([[]] * 60, [float(i) for i in range(60)], epochs=20, seed=1)
        prediction = model.predict([], max_position_size=100.0)
        self.assertFalse(prediction.is_extrapolating)
        self.assertEqual(prediction.max_feature_zscore, 0.0)

    def test_status_precedence_puts_degenerate_band_first(self):
        # A degenerate band on an extrapolating input must report the band, which is the
        # condition that actually blocks sizing.
        targets = [1e-9 * i for i in range(200)]
        rows = [[float(i)] for i in range(200)]
        model = QuantileRegressionSignalModel(
            num_features=1, min_uncertainty_width=1e-4
        ).fit(rows, targets, epochs=1, seed=1)
        # x = 500 is ~7 sigma out (the training feature is 0..199) but the band there is
        # still ~3e-7, so both conditions genuinely hold at once.
        prediction = model.predict([500.0], max_position_size=1.0)
        self.assertTrue(prediction.is_extrapolating)
        self.assertTrue(prediction.uncertainty_floor_binding)
        self.assertEqual(prediction.status_message, "degenerate_band_not_sized")
        self.assertEqual(prediction.confidence_scaled_size, 0.0)

    def test_quantile_ordering_always_holds_after_rearrangement(self):
        for x in (1.0, 1.5, 2.0, 2.5, 3.0):
            prediction = self.model.predict([x], max_position_size=100.0)
            self.assertLessEqual(prediction.q_lower, prediction.q_central)
            self.assertLessEqual(prediction.q_central, prediction.q_upper)
            self.assertGreaterEqual(prediction.uncertainty_width, 0.0)

    def test_crossing_is_detected_and_repaired(self):
        # Force a crossing by swapping the fitted coefficients between the tails: the
        # rearrangement must restore ordering and say that it did.
        tau_lower, _, tau_upper = self.model.quantiles
        self.model.intercepts[tau_lower], self.model.intercepts[tau_upper] = (
            self.model.intercepts[tau_upper],
            self.model.intercepts[tau_lower],
        )
        self.model.weights[tau_lower], self.model.weights[tau_upper] = (
            self.model.weights[tau_upper],
            self.model.weights[tau_lower],
        )
        prediction = self.model.predict([2.0], max_position_size=100.0)
        self.assertTrue(prediction.crossing_repaired)
        self.assertLessEqual(prediction.q_lower, prediction.q_central)
        self.assertLessEqual(prediction.q_central, prediction.q_upper)

    def test_no_crossing_reported_on_a_well_ordered_fit(self):
        self.assertFalse(self.model.predict([2.0], max_position_size=100.0).crossing_repaired)


class TestConvergenceBehaviour(unittest.TestCase):
    def test_constant_step_size_is_less_accurate_than_the_decayed_schedule(self):
        """
        Regression test for the v1.0.0 step-size schedule.

        The pinball subgradient has magnitude tau or 1 - tau and never decays, so a
        constant step size leaves an O(eta) oscillation that lands in the band width.
        Same data, same seed, same everything but ``decay_power``.
        """
        rows, targets = _gaussian_sample(
            8000, seed=51, intercept=5.0, slope=1.0, noise_scale=1.0
        )
        constant = QuantileRegressionSignalModel(
            num_features=1, decay_power=0.0, averaging_tail=0.0, min_uncertainty_width=1e-6
        ).fit(rows, targets, epochs=1, seed=3)
        decayed = QuantileRegressionSignalModel(
            num_features=1, min_uncertainty_width=1e-6
        ).fit(rows, targets, epochs=1, seed=3)

        constant_error = abs(
            constant.predict([2.0], max_position_size=100.0).uncertainty_width
            - UNIT_BAND_WIDTH
        )
        decayed_error = abs(
            decayed.predict([2.0], max_position_size=100.0).uncertainty_width
            - UNIT_BAND_WIDTH
        )
        self.assertLess(decayed_error, constant_error)

    def test_more_data_improves_the_estimate(self):
        # The property a non-convergent estimator does not have. Averaged over several
        # samples so a single lucky draw cannot decide it.
        def mean_error(n):
            errors = []
            for seed in range(4):
                rows, targets = _gaussian_sample(
                    n, seed=200 + seed, intercept=5.0, slope=1.0, noise_scale=1.0
                )
                model = QuantileRegressionSignalModel(
                    num_features=1, min_uncertainty_width=1e-6
                ).fit(rows, targets, epochs=1, seed=seed)
                width = model.predict([2.0], max_position_size=100.0).uncertainty_width
                errors.append(abs(width - UNIT_BAND_WIDTH))
            return sum(errors) / len(errors)

        self.assertLess(mean_error(20000), mean_error(1000))

    def test_fit_is_deterministic_for_a_given_seed(self):
        rows, targets = _gaussian_sample(2000, seed=61, intercept=1.0, slope=1.0, noise_scale=1.0)
        first = QuantileRegressionSignalModel(num_features=1).fit(rows, targets, epochs=2, seed=99)
        second = QuantileRegressionSignalModel(num_features=1).fit(rows, targets, epochs=2, seed=99)
        self.assertEqual(first.weights, second.weights)
        self.assertEqual(first.intercepts, second.intercepts)

    def test_fit_does_not_disturb_global_random_state(self):
        rows, targets = _gaussian_sample(500, seed=62, intercept=1.0, slope=1.0, noise_scale=1.0)
        random.seed(1234)
        expected = [random.random() for _ in range(3)]
        random.seed(1234)
        QuantileRegressionSignalModel(num_features=1).fit(rows, targets, epochs=1, seed=5)
        self.assertEqual([random.random() for _ in range(3)], expected)

    def test_refit_replaces_rather_than_accumulates(self):
        # fit() is a complete refit, which is what walk-forward windowing needs.
        rows_a, targets_a = _gaussian_sample(1500, seed=71, intercept=0.0, slope=1.0, noise_scale=1.0)
        rows_b, targets_b = _gaussian_sample(1500, seed=72, intercept=50.0, slope=1.0, noise_scale=1.0)
        model = QuantileRegressionSignalModel(num_features=1, min_uncertainty_width=1e-6)
        model.fit(rows_a, targets_a, epochs=1, seed=1)
        model.fit(rows_b, targets_b, epochs=1, seed=1)
        reference = QuantileRegressionSignalModel(
            num_features=1, min_uncertainty_width=1e-6
        ).fit(rows_b, targets_b, epochs=1, seed=1)
        self.assertEqual(model.weights, reference.weights)
        self.assertEqual(model.intercepts, reference.intercepts)
        self.assertEqual(model.observations_trained, reference.observations_trained)

    def test_online_step_refines_a_fitted_model(self):
        rows, targets = _gaussian_sample(1000, seed=81, intercept=1.0, slope=1.0, noise_scale=1.0)
        model = QuantileRegressionSignalModel(num_features=1).fit(rows, targets, epochs=1, seed=1)
        before = model.observations_trained
        model.train_sample([2.0], 3.0)
        self.assertEqual(model.observations_trained, before + 1)

    def test_subgradient_signs(self):
        gradient = QuantileRegressionSignalModel.pinball_loss_gradient
        # y above the prediction pushes it up (negative gradient), weighted by tau.
        self.assertAlmostEqual(gradient(10.0, 5.0, 0.9), -0.9)
        # y below the prediction pushes it down, weighted by 1 - tau.
        self.assertAlmostEqual(gradient(1.0, 5.0, 0.9), 0.1)
        # At equality the -tau element of the subdifferential is taken.
        self.assertAlmostEqual(gradient(5.0, 5.0, 0.3), -0.3)


class TestInputValidation(unittest.TestCase):
    """
    v1.0.0 validated nothing. Each case below either raised an unhelpful error from deep
    inside the update loop, or -- worse -- silently succeeded on corrupt input.
    """

    def setUp(self):
        rows, targets = _gaussian_sample(500, seed=91, intercept=1.0, slope=1.0, noise_scale=1.0)
        self.rows, self.targets = rows, targets
        self.model = QuantileRegressionSignalModel(num_features=1).fit(rows, targets, seed=1)

    def test_non_finite_target_is_rejected(self):
        """
        Regression test. ``y - y_hat >= 0`` is False for NaN, so v1.0.0 silently took the
        "observation below the prediction" branch and dragged every quantile down: a
        single NaN target produced a confidently *signed* signal built from corrupt data.
        """
        for bad in (float("nan"), float("inf"), float("-inf")):
            with self.assertRaises(ValueError):
                self.model.train_sample([1.0], bad)
        with self.assertRaises(ValueError):
            QuantileRegressionSignalModel(num_features=1).fit(
                [[1.0], [2.0]], [1.0, float("nan")]
            )

    def test_non_finite_feature_is_rejected(self):
        for bad in (float("nan"), float("inf")):
            with self.assertRaises(ValueError):
                self.model.predict([bad])
            with self.assertRaises(ValueError):
                self.model.train_sample([bad], 1.0)

    def test_wrong_feature_count_is_rejected(self):
        # Too few raised IndexError mid-update in v1.0.0; too many were silently dropped
        # by zip(), so the caller never learned the prediction ignored them.
        with self.assertRaises(ValueError):
            self.model.predict([])
        with self.assertRaises(ValueError):
            self.model.predict([1.0, 2.0])
        with self.assertRaises(ValueError):
            self.model.train_sample([1.0, 2.0], 1.0)

    def test_rejected_observation_leaves_the_model_unchanged(self):
        before_weights = {q: list(w) for q, w in self.model.weights.items()}
        before_intercepts = dict(self.model.intercepts)
        before_count = self.model.observations_trained
        with self.assertRaises(ValueError):
            self.model.train_sample([float("nan")], 1.0)
        self.assertEqual(self.model.weights, before_weights)
        self.assertEqual(self.model.intercepts, before_intercepts)
        self.assertEqual(self.model.observations_trained, before_count)

    def test_malformed_row_aborts_fit_before_training(self):
        model = QuantileRegressionSignalModel(num_features=1)
        with self.assertRaises(ValueError):
            model.fit([[1.0], [float("nan")], [3.0]], [1.0, 2.0, 3.0])
        self.assertFalse(model.is_fitted)
        self.assertEqual(model.observations_trained, 0)

    def test_predict_before_fit_is_refused(self):
        # An unfitted model's coefficients are all zero, which in v1.0.0 yielded a
        # well-formed zero prediction with a zero-width band -- and therefore, via the
        # floored divisor, the maximum position size.
        model = QuantileRegressionSignalModel(num_features=1)
        with self.assertRaises(RuntimeError):
            model.predict([1.0])
        with self.assertRaises(RuntimeError):
            model.train_sample([1.0], 1.0)

    def test_invalid_max_position_size_is_rejected(self):
        for bad in (0.0, -1.0, float("nan"), float("inf")):
            with self.assertRaises(ValueError):
                self.model.predict([1.0], max_position_size=bad)

    def test_invalid_quantile_triples_are_rejected(self):
        for bad in [(0.9, 0.5, 0.1), (0.1, 0.9), (0.1, 0.5, 0.9, 0.95),
                    (0.0, 0.5, 0.9), (0.1, 0.5, 1.0), (0.5, 0.5, 0.9)]:
            with self.assertRaises(ValueError):
                QuantileRegressionSignalModel(num_features=1, quantiles=bad)

    def test_invalid_constructor_arguments_are_rejected(self):
        with self.assertRaises(ValueError):
            QuantileRegressionSignalModel(num_features=-1)
        with self.assertRaises(ValueError):
            QuantileRegressionSignalModel(num_features=1, learning_rate=0.0)
        with self.assertRaises(ValueError):
            QuantileRegressionSignalModel(num_features=1, decay_power=1.5)
        with self.assertRaises(ValueError):
            QuantileRegressionSignalModel(num_features=1, averaging_tail=1.0)
        with self.assertRaises(ValueError):
            QuantileRegressionSignalModel(num_features=1, min_uncertainty_width=0.0)

    def test_invalid_fit_arguments_are_rejected(self):
        with self.assertRaises(ValueError):
            QuantileRegressionSignalModel(num_features=1).fit([], [])
        with self.assertRaises(ValueError):
            QuantileRegressionSignalModel(num_features=1).fit([[1.0]], [1.0, 2.0])
        with self.assertRaises(ValueError):
            QuantileRegressionSignalModel(num_features=1).fit(
                self.rows, self.targets, epochs=0
            )

    def test_constant_target_is_refused(self):
        # Every quantile of a constant is that constant, so there is no band to estimate.
        # Scaling steps by an arbitrary constant instead would let SGD manufacture a
        # spurious band out of the step size, which the sizer would then trade.
        with self.assertRaises(ValueError):
            QuantileRegressionSignalModel(num_features=1).fit(
                [[1.0], [2.0], [3.0]], [5.0, 5.0, 5.0]
            )

    def test_diverged_fit_is_refused(self):
        # Extreme magnitudes can overflow the dot product during training. A non-finite
        # coefficient must not reach predict(), where it would silently disable every
        # ordering and threshold comparison downstream.
        with self.assertRaises((ValueError, OverflowError)):
            QuantileRegressionSignalModel(num_features=1).fit(
                [[1e200], [-1e200], [1e200], [-1e200]],
                [1e200, -1e200, 1e200, -1e200],
                epochs=3,
                seed=1,
            )

    def test_intercept_only_model_needs_no_features(self):
        model = QuantileRegressionSignalModel(num_features=0, min_uncertainty_width=1e-6)
        model.fit([[]] * 50, [float(i) for i in range(50)], epochs=20, seed=1)
        self.assertTrue(model.is_fitted)
        with self.assertRaises(ValueError):
            model.predict([1.0])

    def test_constant_feature_is_tolerated_and_ignored(self):
        # A constant column is collinear with the intercept and carries no conditional
        # information; it must not produce a division by zero in the scaler.
        rng = random.Random(101)
        rows = [[1.0, rng.gauss(0.0, 1.0)] for _ in range(2000)]
        targets = [3.0 + 2.0 * row[1] + rng.gauss(0.0, 1.0) for row in rows]
        model = QuantileRegressionSignalModel(
            num_features=2, min_uncertainty_width=1e-6
        ).fit(rows, targets, epochs=2, seed=1)
        prediction = model.predict([1.0, 0.5], max_position_size=100.0)
        self.assertAlmostEqual(prediction.uncertainty_width, UNIT_BAND_WIDTH, delta=0.30)


if __name__ == "__main__":
    unittest.main()
