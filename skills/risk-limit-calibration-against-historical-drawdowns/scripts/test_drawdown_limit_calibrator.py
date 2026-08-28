"""Unit tests for the drawdown-based risk limit calibrator.

Expected values are derived independently of the implementation: closed-form
arithmetic on the designed fixture, or a deliberately different algorithm (the
O(n^2) brute-force drawdown reference below). Tests that exist to pin a fixed
defect are marked "regression" and fail against the pre-fix behaviour.
"""
import logging
import math
import statistics
import unittest

from drawdown_limit_calibrator import (
    ABSOLUTE_MIN_OBSERVATIONS,
    CalibratedRiskLimits,
    CalibrationError,
    CalibrationMethod,
    DrawdownLimitCalibratorEngine,
    InsufficientDataError,
    InvalidParameterError,
    InvalidReturnSeriesError,
    TailFitError,
    fit_gpd_left_tail,
)

WINDOW = 252
LOGGER_NAME = "drawdown_limit_calibrator"


def setUpModule():
    logging.disable(logging.CRITICAL)


def tearDownModule():
    logging.disable(logging.NOTSET)


class LogCaptureMixin:
    """Re-enables logging for the duration of one test, then silences it again."""

    def capture_warnings(self):
        logging.disable(logging.NOTSET)
        self.addCleanup(logging.disable, logging.CRITICAL)
        return self.assertLogs(LOGGER_NAME, level=logging.WARNING)


def brute_force_max_drawdown_pct(returns):
    """Reference max drawdown: O(n^2) over every (peak, trough) pair.

    Deliberately unlike the engine's incremental running-peak loop, so agreement
    between the two is evidence rather than a tautology.
    """
    equity = [1.0]
    for r in returns:
        equity.append(equity[-1] * (1.0 + r))
    worst = 0.0
    for i in range(len(equity)):
        for j in range(i + 1, len(equity)):
            worst = max(worst, (equity[i] - equity[j]) / equity[i])
    return worst * 100.0


def pad(series, total=WINDOW, filler=0.0):
    """Right-pads `series` with `filler` to `total` observations."""
    if len(series) > total:
        raise ValueError("series already longer than the requested window")
    return list(series) + [filler] * (total - len(series))


class TestDrawdownMetrics(unittest.TestCase):
    def setUp(self):
        self.engine = DrawdownLimitCalibratorEngine()

    def test_max_drawdown_agrees_with_brute_force_reference(self):
        series = [
            # deterministic saw-tooth with three separate drawdowns
            0.01, -0.02, 0.015, -0.03, 0.02, -0.01, -0.025, 0.04, -0.05, 0.03
        ] * 25 + [0.001, -0.002]
        self.assertEqual(len(series), WINDOW)
        metrics = self.engine.compute_drawdown_metrics(series)
        self.assertAlmostEqual(
            metrics.max_drawdown_pct, brute_force_max_drawdown_pct(series), places=6
        )

    def test_max_drawdown_matches_closed_form(self):
        # Peak 1.0, then three consecutive losses: 0.98 * 0.97 * 0.95.
        series = pad([0.0] * 100 + [-0.02, -0.03, -0.05])
        expected = (1.0 - 0.98 * 0.97 * 0.95) * 100.0
        metrics = self.engine.compute_drawdown_metrics(series)
        self.assertAlmostEqual(metrics.max_drawdown_pct, expected, places=6)
        self.assertAlmostEqual(
            metrics.max_drawdown_pct, brute_force_max_drawdown_pct(series), places=6
        )

    def test_ulcer_index_matches_martin_definition(self):
        # One -10% day, then flat: every subsequent equity point sits exactly 10%
        # below the running peak, so sqrt(mean(10^2)) == 10 exactly.
        series = pad([-0.10])
        metrics = self.engine.compute_drawdown_metrics(series)
        self.assertAlmostEqual(metrics.ulcer_index, 10.0, places=9)
        self.assertAlmostEqual(metrics.max_drawdown_pct, 10.0, places=9)
        # Every one of the 252 observations closes below the starting peak of 1.0.
        self.assertEqual(metrics.max_drawdown_duration_days, WINDOW)
        self.assertTrue(metrics.drawdown_unrecovered)

    def test_flat_series_is_never_underwater(self):
        """Regression: a day closing exactly at the peak is not a drawdown day.

        The pre-fix loop tested `equity > peak`, so a perfectly flat series was
        reported as 252 consecutive underwater days at 0% drawdown.
        """
        metrics = self.engine.compute_drawdown_metrics([0.0] * WINDOW)
        self.assertEqual(metrics.max_drawdown_pct, 0.0)
        self.assertEqual(metrics.max_drawdown_duration_days, 0)
        self.assertEqual(metrics.ulcer_index, 0.0)
        self.assertFalse(metrics.drawdown_unrecovered)

    def test_drawdown_unrecovered_is_false_after_recovery(self):
        series = pad([0.0] * 100 + [-0.10] + [0.20], filler=0.0)
        metrics = self.engine.compute_drawdown_metrics(series)
        self.assertFalse(metrics.drawdown_unrecovered)
        self.assertGreater(metrics.max_drawdown_duration_days, 0)

    def test_historical_var_and_es_are_order_statistics(self):
        # k = ceil(0.01 * 252) = 3, so VaR is the 3rd-worst return and ES the mean
        # of the three worst.
        series = [0.002] * 249 + [-0.03, -0.04, -0.05]
        metrics = self.engine.compute_drawdown_metrics(series)
        self.assertAlmostEqual(metrics.var_pct, 3.0, places=9)
        self.assertAlmostEqual(metrics.cvar_pct, 4.0, places=9)
        self.assertGreaterEqual(metrics.cvar_pct, metrics.var_pct)

    def test_confidence_level_drives_the_quantile(self):
        """Regression: target_confidence_pct used to be reported but never used.

        The pre-fix engine hard-coded z=2.326 (99%) regardless of the configured
        confidence, so a 95% engine returned the identical VaR.
        """
        losses = [-0.001 * i for i in range(1, 14)]
        series = [0.002] * 239 + losses
        at_99 = DrawdownLimitCalibratorEngine(
            target_confidence_pct=99.0
        ).compute_drawdown_metrics(series)
        at_95 = DrawdownLimitCalibratorEngine(
            target_confidence_pct=95.0
        ).compute_drawdown_metrics(series)

        # 99%: k = 3 -> worst three are -0.013, -0.012, -0.011
        self.assertAlmostEqual(at_99.var_pct, 1.1, places=9)
        self.assertAlmostEqual(at_99.cvar_pct, 1.2, places=9)
        # 95%: k = 13 -> worst thirteen are -0.013 .. -0.001
        self.assertAlmostEqual(at_95.var_pct, 0.1, places=9)
        self.assertAlmostEqual(at_95.cvar_pct, 0.7, places=9)
        self.assertNotAlmostEqual(at_99.var_pct, at_95.var_pct, places=3)

    def test_annualized_volatility_uses_252(self):
        series = [0.01, -0.01] * 126
        metrics = self.engine.compute_drawdown_metrics(series)
        expected = statistics.stdev(series) * math.sqrt(252) * 100.0
        self.assertAlmostEqual(metrics.volatility_annualized, expected, places=6)


class TestReturnSeriesValidation(unittest.TestCase):
    def setUp(self):
        self.engine = DrawdownLimitCalibratorEngine()

    def test_rejects_nan_return(self):
        """Regression: a NaN used to reach the daily loss limit.

        A NaN limit is never breached by any comparison, so the risk control it
        feeds can never fire.
        """
        series = pad([0.01, float("nan"), -0.02])
        with self.assertRaises(InvalidReturnSeriesError):
            self.engine.compute_drawdown_metrics(series)

    def test_rejects_infinite_return(self):
        with self.assertRaises(InvalidReturnSeriesError):
            self.engine.compute_drawdown_metrics(pad([float("inf")]))

    def test_rejects_total_loss_return(self):
        with self.assertRaises(InvalidReturnSeriesError):
            self.engine.compute_drawdown_metrics(pad([-1.0]))

    def test_rejects_return_below_minus_one(self):
        """Regression: r < -1 used to drive equity negative and report a >100% DD."""
        with self.assertRaises(InvalidReturnSeriesError):
            self.engine.compute_drawdown_metrics(pad([-1.5]))

    def test_rejects_non_numeric_element(self):
        with self.assertRaises(InvalidReturnSeriesError):
            self.engine.compute_drawdown_metrics(pad(["0.01"]))

    def test_rejects_boolean_element(self):
        with self.assertRaises(InvalidReturnSeriesError):
            self.engine.compute_drawdown_metrics(pad([True]))

    def test_rejects_string_series(self):
        with self.assertRaises(InvalidReturnSeriesError):
            self.engine.compute_drawdown_metrics("0.01,0.02")

    def test_rejects_short_history(self):
        """Regression: the engine used to calibrate a 99% tail off 10 observations."""
        with self.assertRaises(InsufficientDataError):
            self.engine.compute_drawdown_metrics([0.01, -0.01] * 5)

    def test_rejects_one_observation_short_of_the_window(self):
        with self.assertRaises(InsufficientDataError):
            self.engine.compute_drawdown_metrics([0.001] * (WINDOW - 1))


class TestEngineParameterValidation(unittest.TestCase):
    def test_rejects_buffer_below_one(self):
        with self.assertRaises(InvalidParameterError):
            DrawdownLimitCalibratorEngine(stress_buffer_multiplier=0.9)

    def test_rejects_negative_buffer(self):
        with self.assertRaises(InvalidParameterError):
            DrawdownLimitCalibratorEngine(stress_buffer_multiplier=-3.0)

    def test_rejects_confidence_outside_open_interval(self):
        for bad in (50.0, 100.0, 0.99, 150.0):
            with self.assertRaises(InvalidParameterError):
                DrawdownLimitCalibratorEngine(target_confidence_pct=bad)

    def test_rejects_window_below_absolute_floor(self):
        with self.assertRaises(InvalidParameterError):
            DrawdownLimitCalibratorEngine(
                min_observations=ABSOLUTE_MIN_OBSERVATIONS - 1
            )

    def test_rejects_window_too_short_for_confidence(self):
        # 126 observations cannot contain a single 99.5% tail loss (1/0.005 = 200).
        with self.assertRaises(InvalidParameterError):
            DrawdownLimitCalibratorEngine(
                min_observations=ABSOLUTE_MIN_OBSERVATIONS,
                target_confidence_pct=99.5,
            )

    def test_rejects_non_positive_horizon(self):
        with self.assertRaises(InvalidParameterError):
            DrawdownLimitCalibratorEngine(horizon_days=0)

    def test_rejects_floor_not_below_cap(self):
        with self.assertRaises(InvalidParameterError):
            DrawdownLimitCalibratorEngine(
                drawdown_limit_floor_pct=50.0, drawdown_limit_cap_pct=50.0
            )

    def test_rejects_non_positive_daily_loss_multiple(self):
        with self.assertRaises(InvalidParameterError):
            DrawdownLimitCalibratorEngine(daily_loss_var_multiple=0.0)

    def test_rejects_non_positive_scalar_threshold(self):
        with self.assertRaises(InvalidParameterError):
            DrawdownLimitCalibratorEngine(position_scalar_threshold_pct=0.0)


class TestCalibrateRiskLimits(LogCaptureMixin, unittest.TestCase):
    def setUp(self):
        self.engine = DrawdownLimitCalibratorEngine()
        # Peak 1.0 -> -25% -> full recovery to a new high -> three tiny losses.
        # Max drawdown is therefore exactly 25%, and the tail still contains three
        # losses so a 99% historical VaR exists.
        self.series = (
            [0.0] * 100
            + [-0.25]
            + [0.35, 0.35, 0.35]
            + [0.0] * 145
            + [-0.001, -0.001, -0.001]
        )
        self.assertEqual(len(self.series), WINDOW)

    def test_historical_limit_is_buffer_times_observed_drawdown(self):
        limits = self.engine.calibrate_risk_limits(
            self.series, 1_000_000.0, CalibrationMethod.HISTORICAL_MAX_DD
        )
        self.assertAlmostEqual(limits.metrics.max_drawdown_pct, 25.0, places=9)
        self.assertAlmostEqual(limits.calibrated_max_drawdown_pct, 37.5, places=9)
        self.assertAlmostEqual(limits.calibrated_max_drawdown_usd, 375_000.0, places=2)
        self.assertFalse(limits.floor_binding)
        self.assertFalse(limits.cap_binding)

    def test_position_scalar_is_threshold_over_observed_drawdown(self):
        limits = self.engine.calibrate_risk_limits(self.series, 1_000_000.0)
        # 20% threshold / 25% observed drawdown
        self.assertAlmostEqual(limits.position_size_scalar, 0.8, places=9)

    def test_position_scalar_is_one_below_threshold(self):
        series = pad([0.0] * 100 + [-0.05, -0.02, -0.01])
        limits = self.engine.calibrate_risk_limits(series, 1_000_000.0)
        self.assertLess(limits.metrics.max_drawdown_pct, 20.0)
        self.assertEqual(limits.position_size_scalar, 1.0)

    def test_daily_loss_limit_uses_unrounded_var(self):
        """Regression: the limit used to be built from a VaR rounded to 2 dp.

        VaR = 2.34567%; rounding it to 2.35% moves a $1m limit by $129.90.
        """
        series = [0.002] * 249 + [-0.0234567, -0.03, -0.04]
        limits = self.engine.calibrate_risk_limits(series, 1_000_000.0)
        self.assertAlmostEqual(
            limits.calibrated_daily_loss_limit_usd, 70_370.10, places=2
        )
        self.assertNotAlmostEqual(
            limits.calibrated_daily_loss_limit_usd, 70_500.00, places=2
        )

    def test_floor_binds_and_is_flagged(self):
        series = [0.001] * 249 + [-0.001, -0.001, -0.001]
        limits = self.engine.calibrate_risk_limits(series, 1_000_000.0)
        self.assertTrue(limits.floor_binding)
        self.assertFalse(limits.cap_binding)
        self.assertAlmostEqual(limits.calibrated_max_drawdown_pct, 5.0, places=9)

    def test_cap_binds_and_is_flagged(self):
        series = pad([0.0] * 100 + [-0.40, -0.001, -0.001, -0.001])
        limits = self.engine.calibrate_risk_limits(series, 1_000_000.0)
        self.assertTrue(limits.cap_binding)
        self.assertFalse(limits.floor_binding)
        self.assertAlmostEqual(limits.calibrated_max_drawdown_pct, 50.0, places=9)

    def test_floor_and_cap_apply_to_every_method(self):
        """Regression: the floor used to be applied only to HISTORICAL_MAX_DD.

        PARAMETRIC_VAR and EXTREME_VALUE_THEORY could therefore emit a 0% limit,
        which halts the strategy on its first tick.
        """
        engine = DrawdownLimitCalibratorEngine(
            evt_tail_fraction=0.10, evt_min_exceedances=25
        )
        series = [0.001] * 240 + [-0.0005 * i for i in range(1, 13)]
        for method in CalibrationMethod:
            with self.subTest(method=method):
                limits = engine.calibrate_risk_limits(series, 1_000_000.0, method)
                self.assertGreaterEqual(
                    limits.calibrated_max_drawdown_pct,
                    engine.drawdown_limit_floor_pct,
                )
                self.assertLessEqual(
                    limits.calibrated_max_drawdown_pct,
                    engine.drawdown_limit_cap_pct,
                )

    def test_parametric_limit_scales_drift_linearly_in_horizon(self):
        """Regression: the drift term used to be scaled by sqrt(h), not h.

        Series: r = 0.001 +/- 0.01, so mu = 0.001 exactly and
        sigma = 0.01 * sqrt(252/251). At h = 20 and z = 2.32634787...,
            loss_h = -20*mu + z*sigma*sqrt(20) = 8.42444796%
        and the calibrated limit is 1.5x that. The pre-fix engine computed
        -(mu - z*sigma) * sqrt(20) * 1.5 = 14.96%, over 2.3 points higher.
        """
        series = [0.001 + 0.01, 0.001 - 0.01] * 126
        limits = self.engine.calibrate_risk_limits(
            series, 1_000_000.0, CalibrationMethod.PARAMETRIC_VAR
        )
        self.assertEqual(limits.horizon_days, 20)
        self.assertAlmostEqual(
            limits.calibrated_max_drawdown_pct, 12.636671939872757, places=6
        )
        self.assertGreater(
            abs(limits.calibrated_max_drawdown_pct - 14.963513292609425), 1.0
        )

    def test_parametric_limit_responds_to_horizon(self):
        series = [0.001 + 0.01, 0.001 - 0.01] * 126
        short = DrawdownLimitCalibratorEngine(horizon_days=5).calibrate_risk_limits(
            series, 1_000_000.0, CalibrationMethod.PARAMETRIC_VAR
        )
        long = DrawdownLimitCalibratorEngine(horizon_days=60).calibrate_risk_limits(
            series, 1_000_000.0, CalibrationMethod.PARAMETRIC_VAR
        )
        self.assertLess(short.calibrated_max_drawdown_pct, long.calibrated_max_drawdown_pct)

    def test_every_method_reports_a_distinct_basis(self):
        """No method may silently produce another method's number.

        The removed MONTE_CARLO_BOOTSTRAP member fell through to the parametric
        branch while the audit record named the bootstrap.
        """
        engine = DrawdownLimitCalibratorEngine(
            evt_tail_fraction=0.10, evt_min_exceedances=25
        )
        series = [0.001] * 240 + [-0.0005 * i for i in range(1, 13)]
        results = {}
        for method in CalibrationMethod:
            limits = engine.calibrate_risk_limits(series, 1_000_000.0, method)
            self.assertEqual(limits.calibration_method, method)
            self.assertIn(method.value, limits.audit_notes)
            results[method] = limits.limit_basis
        self.assertEqual(len(set(results.values())), len(CalibrationMethod))

    def test_monte_carlo_bootstrap_member_is_gone(self):
        """It was never implemented; path simulation belongs to the Monte Carlo skill."""
        self.assertNotIn(
            "MONTE_CARLO_BOOTSTRAP", [m.name for m in CalibrationMethod]
        )

    def test_rejects_unknown_method_type(self):
        with self.assertRaises(InvalidParameterError):
            self.engine.calibrate_risk_limits(
                self.series, 1_000_000.0, "HISTORICAL_MAX_DD"
            )

    def test_rejects_non_positive_capital(self):
        """Regression: negative capital used to emit negative limits."""
        for bad in (0.0, -500_000.0):
            with self.subTest(capital=bad):
                with self.assertRaises(InvalidParameterError):
                    self.engine.calibrate_risk_limits(self.series, bad)

    def test_rejects_non_finite_capital(self):
        with self.assertRaises(InvalidParameterError):
            self.engine.calibrate_risk_limits(self.series, float("nan"))

    def test_rejects_boolean_capital(self):
        with self.assertRaises(InvalidParameterError):
            self.engine.calibrate_risk_limits(self.series, True)

    def test_raises_when_sample_has_no_tail_loss(self):
        """Regression: an all-winning sample used to emit a $0 daily loss limit."""
        with self.assertRaises(CalibrationError):
            self.engine.calibrate_risk_limits([0.001] * WINDOW, 1_000_000.0)

    def test_floor_binding_is_warned_not_just_flagged(self):
        with self.capture_warnings() as captured:
            self.engine.calibrate_risk_limits(
                [0.001] * 249 + [-0.001, -0.001, -0.001], 1_000_000.0
            )
        self.assertTrue(
            any("policy floor" in line for line in captured.output), captured.output
        )

    def test_cap_binding_is_warned_not_just_flagged(self):
        with self.capture_warnings() as captured:
            self.engine.calibrate_risk_limits(
                pad([0.0] * 100 + [-0.40, -0.001, -0.001, -0.001]), 1_000_000.0
            )
        self.assertTrue(
            any("policy cap" in line for line in captured.output), captured.output
        )

    def test_censored_drawdown_duration_is_warned(self):
        with self.capture_warnings() as captured:
            self.engine.calibrate_risk_limits(
                pad([0.0] * 100 + [-0.05, -0.02, -0.01]), 1_000_000.0
            )
        self.assertTrue(
            any("right-censored" in line for line in captured.output), captured.output
        )

    def test_result_carries_its_own_evidence(self):
        limits = self.engine.calibrate_risk_limits(self.series, 1_000_000.0)
        self.assertIsInstance(limits, CalibratedRiskLimits)
        self.assertEqual(limits.metrics.observations, WINDOW)
        self.assertEqual(limits.confidence_level_pct, 99.0)
        self.assertIsNone(limits.tail_fit)
        self.assertIn("not a regulatory", limits.audit_notes)


class TestGpdTailFit(unittest.TestCase):
    # 246 gains, one loss exactly at the threshold, five exceedances whose excesses
    # are 0.001 .. 0.005. Hand-computed: mean = 0.003, sample variance = 2.5e-6,
    # mean^2/var = 3.6, so xi = (1 - 3.6)/2 = -1.3 and beta = 0.003 * 4.6/2 = 0.0069.
    TAIL_SERIES = (
        [0.001] * 246
        + [-0.010]
        + [-0.011, -0.012, -0.013, -0.014, -0.015]
    )

    def test_series_fixture_length(self):
        self.assertEqual(len(self.TAIL_SERIES), WINDOW)

    def test_method_of_moments_recovers_hand_computed_parameters(self):
        fit = fit_gpd_left_tail(
            self.TAIL_SERIES,
            confidence_pct=99.0,
            tail_fraction=0.02,
            min_exceedances=5,
        )
        self.assertEqual(fit.exceedances, 5)
        self.assertEqual(fit.observations, WINDOW)
        self.assertAlmostEqual(fit.threshold_loss_pct, 1.0, places=6)
        self.assertAlmostEqual(fit.shape_xi, -1.3, places=8)
        self.assertAlmostEqual(fit.scale_beta, 0.0069, places=10)

    def test_pot_quantile_and_expected_shortfall_match_closed_form(self):
        # u = 0.01, N_u = 5, n = 252, q = 0.99 -> (n/N_u)(1-q) = 0.504
        #   VaR = u + (beta/xi) * (0.504 ** -xi - 1)          = 0.0131296544
        #   ES  = VaR/(1-xi) + (beta - xi*u)/(1-xi)           = 0.0143607193
        fit = fit_gpd_left_tail(
            self.TAIL_SERIES,
            confidence_pct=99.0,
            tail_fraction=0.02,
            min_exceedances=5,
        )
        self.assertAlmostEqual(fit.var_pct, 1.3129654391777879, places=6)
        self.assertAlmostEqual(fit.cvar_pct, 1.436071930077299, places=6)
        self.assertGreater(fit.cvar_pct, fit.var_pct)

    def test_rejects_too_few_exceedances(self):
        with self.assertRaises(TailFitError):
            fit_gpd_left_tail(self.TAIL_SERIES, confidence_pct=99.0, tail_fraction=0.02)

    def test_rejects_confidence_inside_the_threshold(self):
        # (252/5) * 0.05 = 2.52 >= 1: the 95% quantile sits below the threshold, so
        # the POT formula would extrapolate into a region it was not fitted on.
        with self.assertRaises(TailFitError):
            fit_gpd_left_tail(
                self.TAIL_SERIES,
                confidence_pct=95.0,
                tail_fraction=0.02,
                min_exceedances=5,
            )

    def test_rejects_degenerate_tail(self):
        with self.assertRaises(TailFitError):
            fit_gpd_left_tail(
                [-0.01] * WINDOW,
                confidence_pct=99.0,
                tail_fraction=0.10,
                min_exceedances=25,
            )

    def test_rejects_tail_fraction_outside_unit_interval(self):
        for bad in (0.0, 1.0, -0.1, 1.5):
            with self.subTest(tail_fraction=bad):
                with self.assertRaises(InvalidParameterError):
                    fit_gpd_left_tail(
                        self.TAIL_SERIES, confidence_pct=99.0, tail_fraction=bad
                    )

    def test_evt_calibration_records_the_fit(self):
        engine = DrawdownLimitCalibratorEngine(
            evt_tail_fraction=0.10, evt_min_exceedances=25
        )
        series = [0.001] * 240 + [-0.0005 * i for i in range(1, 13)]
        limits = engine.calibrate_risk_limits(
            series, 1_000_000.0, CalibrationMethod.EXTREME_VALUE_THEORY
        )
        self.assertIsNotNone(limits.tail_fit)
        self.assertEqual(limits.tail_fit.exceedances, 25)
        self.assertIn("POT/GPD", limits.limit_basis)
        self.assertIn("assumes IID", limits.limit_basis)

    def test_shipped_defaults_can_run_evt_on_the_default_window(self):
        """The default engine must be able to execute its own EVT method.

        With min_observations=252 and a 10% tail fraction there are exactly 25
        exceedances, so DEFAULT_MIN_EXCEEDANCES may not exceed 25.
        """
        engine = DrawdownLimitCalibratorEngine()
        series = [0.001] * 240 + [-0.0005 * i for i in range(1, 13)]
        limits = engine.calibrate_risk_limits(
            series, 1_000_000.0, CalibrationMethod.EXTREME_VALUE_THEORY
        )
        self.assertEqual(limits.tail_fit.exceedances, 25)

    def test_evt_propagates_tail_fit_failure(self):
        """A failed tail fit must raise, not fall back to another method."""
        engine = DrawdownLimitCalibratorEngine(
            evt_tail_fraction=0.02, evt_min_exceedances=30
        )
        with self.assertRaises(TailFitError):
            engine.calibrate_risk_limits(
                self.TAIL_SERIES, 1_000_000.0, CalibrationMethod.EXTREME_VALUE_THEORY
            )


if __name__ == "__main__":
    unittest.main()
