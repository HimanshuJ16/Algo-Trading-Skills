"""
Unit tests for the multi-horizon forecast synthesizer.

Expected values are derived by hand from clean horizon ratios (perfect squares,
so sqrt(target/tau) is exact) rather than by re-running the implementation's own
formula, so a wrong scaling law fails the test instead of cancelling out.
"""
import logging
import unittest

from multi_horizon_forecaster import (
    ConflictPolicy,
    ForecastStatus,
    HorizonPrediction,
    HorizonScaling,
    MultiHorizonConfig,
    MultiHorizonError,
    MultiHorizonForecasterEngine,
    WeightingScheme,
)


def setUpModule():
    """
    Keeps the engine's (correct, deliberate) audit lines out of the test output.
    `assertLogs` installs its own handler, so log assertions still work.
    """
    engine_logger = logging.getLogger("multi_horizon_forecaster")
    engine_logger.addHandler(logging.NullHandler())
    engine_logger.propagate = False


class TestHorizonScaleNormalization(unittest.TestCase):
    """The core correction: forecasts over different horizons are not addable raw."""

    def setUp(self):
        self.engine = MultiHorizonForecasterEngine()

    def test_naive_averaging_regression_long_horizon_is_scaled_down(self):
        # tau = 5 and tau = 45 -> ratio 9 -> sigma ratio exactly 3.
        # A 90 bps forecast over 45 steps is worth 90 / 3 = 30 bps over 5 steps.
        # Equal-weighted composite at the 5-step horizon = (10 + 30) / 2 = 20 bps.
        # Averaging the raw numbers would give (10 + 90) / 2 = 50 bps, which is
        # the "Naive Horizon Averaging" defect this scaling exists to prevent.
        preds = [
            HorizonPrediction(horizon_steps=5, predicted_return=0.0010),
            HorizonPrediction(horizon_steps=45, predicted_return=0.0090),
        ]
        cfg = MultiHorizonConfig(symbol="TEST", weighting_scheme=WeightingScheme.EQUAL)

        report = self.engine.synthesize_forecast(cfg, preds)

        self.assertEqual(report.target_horizon_steps, 5)
        self.assertAlmostEqual(report.normalized_predictions[45], 0.0030, places=10)
        self.assertAlmostEqual(report.composite_alpha, 0.0020, places=10)
        self.assertLess(report.composite_alpha, 0.0050)

    def test_composite_is_weighted_mean_of_normalized_predictions(self):
        preds = [
            HorizonPrediction(horizon_steps=4, predicted_return=0.0020, ic_score=0.06),
            HorizonPrediction(horizon_steps=16, predicted_return=0.0080, ic_score=0.02, confidence=0.5),
        ]
        cfg = MultiHorizonConfig(symbol="TEST", conflict_policy=ConflictPolicy.REPORT_ONLY)

        report = self.engine.synthesize_forecast(cfg, preds)

        rebuilt = sum(
            report.horizon_weights[h] * report.normalized_predictions[h]
            for h in report.horizon_weights
        )
        self.assertAlmostEqual(report.composite_alpha, rebuilt, places=6)

    def test_target_horizon_override_scales_short_forecast_up(self):
        # Target 45 steps: the 5-step forecast of 10 bps is worth 10 * 3 = 30 bps.
        preds = [
            HorizonPrediction(horizon_steps=5, predicted_return=0.0010),
            HorizonPrediction(horizon_steps=45, predicted_return=0.0090),
        ]
        cfg = MultiHorizonConfig(
            symbol="TEST", weighting_scheme=WeightingScheme.EQUAL, target_horizon_steps=45
        )

        report = self.engine.synthesize_forecast(cfg, preds)

        self.assertEqual(report.target_horizon_steps, 45)
        self.assertAlmostEqual(report.normalized_predictions[5], 0.0030, places=10)
        self.assertAlmostEqual(report.composite_alpha, 0.0060, places=10)

    def test_scaling_none_reproduces_raw_weighted_average(self):
        preds = [
            HorizonPrediction(horizon_steps=5, predicted_return=0.0010),
            HorizonPrediction(horizon_steps=45, predicted_return=0.0090),
        ]
        cfg = MultiHorizonConfig(
            symbol="TEST",
            weighting_scheme=WeightingScheme.EQUAL,
            scaling_mode=HorizonScaling.NONE,
        )

        report = self.engine.synthesize_forecast(cfg, preds)

        self.assertAlmostEqual(report.composite_alpha, 0.0050, places=10)
        self.assertEqual(report.scaling_mode, "NONE")

    def test_explicit_vol_scaling_uses_measured_volatilities(self):
        # sigma(5) = 20 bps, sigma(60) = 80 bps -> the 50 bps / 60-step forecast
        # is 0.625 sigma, worth 0.625 * 20 = 12.5 bps at the 5-step horizon.
        preds = [
            HorizonPrediction(horizon_steps=5, predicted_return=0.0010, horizon_volatility=0.0020),
            HorizonPrediction(horizon_steps=60, predicted_return=0.0050, horizon_volatility=0.0080),
        ]
        cfg = MultiHorizonConfig(
            symbol="TEST",
            weighting_scheme=WeightingScheme.EQUAL,
            scaling_mode=HorizonScaling.EXPLICIT_VOL,
        )

        report = self.engine.synthesize_forecast(cfg, preds)

        self.assertAlmostEqual(report.normalized_predictions[60], 0.00125, places=10)
        self.assertAlmostEqual(report.composite_alpha, 0.001125, places=10)
        # Grinold score: composite expressed in target-horizon volatility units.
        self.assertAlmostEqual(report.composite_score, 0.5625, places=8)

    def test_supplied_volatilities_ignored_under_sqrt_time_are_warned_about(self):
        # Measuring per-horizon volatility and then leaving scaling_mode at its
        # SQRT_TIME default silently discards the measurement.
        preds = [
            HorizonPrediction(horizon_steps=5, predicted_return=0.0010, horizon_volatility=0.0020),
            HorizonPrediction(horizon_steps=60, predicted_return=0.0050, horizon_volatility=0.0080),
        ]
        cfg = MultiHorizonConfig(symbol="TEST", weighting_scheme=WeightingScheme.EQUAL)

        with self.assertLogs("multi_horizon_forecaster", level="WARNING") as captured:
            report = self.engine.synthesize_forecast(cfg, preds)

        self.assertIn("EXPLICIT_VOL", "".join(captured.output))
        # Confirm it really did use sqrt-time, not the supplied volatilities.
        self.assertNotAlmostEqual(report.normalized_predictions[60], 0.00125, places=6)

    def test_explicit_vol_requires_volatility_on_every_prediction(self):
        preds = [
            HorizonPrediction(horizon_steps=5, predicted_return=0.0010, horizon_volatility=0.0020),
            HorizonPrediction(horizon_steps=60, predicted_return=0.0050),
        ]
        cfg = MultiHorizonConfig(symbol="TEST", scaling_mode=HorizonScaling.EXPLICIT_VOL)

        with self.assertRaises(MultiHorizonError):
            self.engine.synthesize_forecast(cfg, preds)

    def test_explicit_vol_target_horizon_must_have_known_volatility(self):
        preds = [
            HorizonPrediction(horizon_steps=5, predicted_return=0.0010, horizon_volatility=0.0020),
            HorizonPrediction(horizon_steps=60, predicted_return=0.0050, horizon_volatility=0.0080),
        ]
        cfg = MultiHorizonConfig(
            symbol="TEST",
            scaling_mode=HorizonScaling.EXPLICIT_VOL,
            target_horizon_steps=7,
        )

        with self.assertRaises(MultiHorizonError):
            self.engine.synthesize_forecast(cfg, preds)


class TestHorizonWeighting(unittest.TestCase):
    def setUp(self):
        self.engine = MultiHorizonForecasterEngine()

    def test_ic_weighted_normalization_matches_hand_computation(self):
        # Raw weights: 0.06 * 1.0 = 0.06 and 0.02 * 0.5 = 0.01; total 0.07.
        # Normalized: 6/7 and 1/7.
        preds = [
            HorizonPrediction(horizon_steps=4, predicted_return=0.0020, ic_score=0.06, confidence=1.0),
            HorizonPrediction(horizon_steps=16, predicted_return=0.0080, ic_score=0.02, confidence=0.5),
        ]
        cfg = MultiHorizonConfig(symbol="TEST", weighting_scheme=WeightingScheme.IC_WEIGHTED)

        report = self.engine.synthesize_forecast(cfg, preds)

        self.assertAlmostEqual(report.horizon_weights[4], 6.0 / 7.0, places=5)
        self.assertAlmostEqual(report.horizon_weights[16], 1.0 / 7.0, places=5)
        # Both forecasts rescale to 20 bps and 40 bps at the 4-step horizon.
        self.assertAlmostEqual(report.composite_alpha, 0.016 / 7.0, places=8)

    def test_inverse_horizon_sqrt_weighting_applies_confidence(self):
        # Raw weights: 1.0 / sqrt(4) = 0.50 and 0.5 / sqrt(16) = 0.125; total 0.625.
        preds = [
            HorizonPrediction(horizon_steps=4, predicted_return=0.0020, confidence=1.0),
            HorizonPrediction(horizon_steps=16, predicted_return=0.0040, confidence=0.5),
        ]
        cfg = MultiHorizonConfig(
            symbol="TEST", weighting_scheme=WeightingScheme.INVERSE_HORIZON_SQRT
        )

        report = self.engine.synthesize_forecast(cfg, preds)

        self.assertAlmostEqual(report.horizon_weights[4], 0.80, places=6)
        self.assertAlmostEqual(report.horizon_weights[16], 0.20, places=6)

    def test_equal_weighting_ignores_ic_and_confidence(self):
        preds = [
            HorizonPrediction(horizon_steps=4, predicted_return=0.0020, ic_score=0.90, confidence=1.0),
            HorizonPrediction(horizon_steps=16, predicted_return=0.0040, ic_score=0.01, confidence=0.1),
        ]
        cfg = MultiHorizonConfig(symbol="TEST", weighting_scheme=WeightingScheme.EQUAL)

        report = self.engine.synthesize_forecast(cfg, preds)

        self.assertAlmostEqual(report.horizon_weights[4], 0.5, places=10)
        self.assertAlmostEqual(report.horizon_weights[16], 0.5, places=10)

    def test_weights_sum_to_one_for_every_scheme(self):
        preds = [
            HorizonPrediction(horizon_steps=5, predicted_return=0.0020, ic_score=0.08),
            HorizonPrediction(horizon_steps=15, predicted_return=0.0015, ic_score=0.06),
            HorizonPrediction(horizon_steps=60, predicted_return=0.0010, ic_score=0.04),
            HorizonPrediction(horizon_steps=390, predicted_return=0.0005, ic_score=0.02),
        ]
        for scheme in WeightingScheme:
            with self.subTest(scheme=scheme):
                cfg = MultiHorizonConfig(symbol="TEST", weighting_scheme=scheme)
                report = self.engine.synthesize_forecast(cfg, preds)
                self.assertAlmostEqual(sum(report.horizon_weights.values()), 1.0, places=12)
                self.assertEqual(report.status, ForecastStatus.SYNTHESIZED.value)

    def test_all_non_positive_ic_zeroes_the_signal_instead_of_equal_weighting(self):
        # No horizon has demonstrated non-negative skill. Falling back to equal
        # weighting here would emit a full-strength alpha from a model set that
        # has none; the engine must refuse to produce a tradeable signal.
        preds = [
            HorizonPrediction(horizon_steps=5, predicted_return=0.0020, ic_score=-0.05),
            HorizonPrediction(horizon_steps=60, predicted_return=0.0010, ic_score=-0.02),
        ]
        cfg = MultiHorizonConfig(symbol="TEST", weighting_scheme=WeightingScheme.IC_WEIGHTED)

        report = self.engine.synthesize_forecast(cfg, preds)

        self.assertEqual(report.composite_alpha, 0.0)
        self.assertEqual(report.composite_score, 0.0)
        self.assertEqual(report.status, ForecastStatus.NO_VALID_HORIZON_WEIGHTS.value)
        self.assertTrue(all(w == 0.0 for w in report.horizon_weights.values()))

    def test_degenerate_weights_are_logged_at_warning(self):
        preds = [
            HorizonPrediction(horizon_steps=5, predicted_return=0.0020, ic_score=-0.05),
            HorizonPrediction(horizon_steps=60, predicted_return=0.0010, ic_score=-0.02),
        ]
        cfg = MultiHorizonConfig(symbol="TEST")

        with self.assertLogs("multi_horizon_forecaster", level="WARNING") as captured:
            self.engine.synthesize_forecast(cfg, preds)

        self.assertIn("NO_VALID_HORIZON_WEIGHTS", "".join(captured.output))

    def test_conflict_is_logged_at_warning(self):
        preds = [
            HorizonPrediction(horizon_steps=20, predicted_return=0.0060),
            HorizonPrediction(horizon_steps=80, predicted_return=-0.0080),
        ]
        cfg = MultiHorizonConfig(symbol="TEST", weighting_scheme=WeightingScheme.EQUAL)

        with self.assertLogs("multi_horizon_forecaster", level="WARNING") as captured:
            self.engine.synthesize_forecast(cfg, preds)

        self.assertIn("Conflict = True", "".join(captured.output))

    def test_zero_confidence_everywhere_zeroes_the_signal(self):
        preds = [
            HorizonPrediction(horizon_steps=5, predicted_return=0.0020, ic_score=0.08, confidence=0.0),
            HorizonPrediction(horizon_steps=60, predicted_return=0.0010, ic_score=0.04, confidence=0.0),
        ]
        cfg = MultiHorizonConfig(symbol="TEST", weighting_scheme=WeightingScheme.IC_WEIGHTED)

        report = self.engine.synthesize_forecast(cfg, preds)

        self.assertEqual(report.composite_alpha, 0.0)
        self.assertEqual(report.status, ForecastStatus.NO_VALID_HORIZON_WEIGHTS.value)


class TestConsensusAndConflict(unittest.TestCase):
    def setUp(self):
        self.engine = MultiHorizonForecasterEngine()

    def test_unanimous_horizons_report_full_consensus(self):
        preds = [
            HorizonPrediction(horizon_steps=5, predicted_return=0.0020, ic_score=0.08),
            HorizonPrediction(horizon_steps=15, predicted_return=0.0015, ic_score=0.06),
            HorizonPrediction(horizon_steps=60, predicted_return=0.0010, ic_score=0.04),
            HorizonPrediction(horizon_steps=390, predicted_return=0.0005, ic_score=0.02),
        ]
        cfg = MultiHorizonConfig(symbol="BTC-USD", weighting_scheme=WeightingScheme.IC_WEIGHTED)

        report = self.engine.synthesize_forecast(cfg, preds)

        self.assertEqual(report.status, ForecastStatus.SYNTHESIZED.value)
        self.assertEqual(report.directional_consensus_pct, 100.0)
        self.assertEqual(report.weighted_consensus_pct, 100.0)
        self.assertFalse(report.has_directional_conflict)
        self.assertGreater(report.composite_alpha, 0.0)

    def test_all_flat_forecasts_report_zero_consensus_not_full_consensus(self):
        # A flat forecast set carries no directional information. Reporting
        # 100% consensus here would read downstream as unanimous conviction.
        preds = [
            HorizonPrediction(horizon_steps=5, predicted_return=0.0),
            HorizonPrediction(horizon_steps=60, predicted_return=0.0),
        ]
        cfg = MultiHorizonConfig(symbol="TEST")

        report = self.engine.synthesize_forecast(cfg, preds)

        self.assertEqual(report.directional_consensus_pct, 0.0)
        self.assertEqual(report.weighted_consensus_pct, 0.0)
        self.assertEqual(report.composite_alpha, 0.0)

    def test_weighted_consensus_reflects_weights_not_head_count(self):
        # One dissenting horizon carrying 10% of the weight is a 50% head-count
        # split but only a 10% dissent by weight.
        preds = [
            HorizonPrediction(horizon_steps=4, predicted_return=0.0020, ic_score=0.09),
            HorizonPrediction(horizon_steps=16, predicted_return=-0.0080, ic_score=0.01),
        ]
        cfg = MultiHorizonConfig(
            symbol="TEST",
            weighting_scheme=WeightingScheme.IC_WEIGHTED,
            conflict_policy=ConflictPolicy.REPORT_ONLY,
        )

        report = self.engine.synthesize_forecast(cfg, preds)

        self.assertEqual(report.directional_consensus_pct, 50.0)
        self.assertAlmostEqual(report.weighted_consensus_pct, 90.0, places=1)
        self.assertAlmostEqual(report.composite_alpha, 0.0014, places=8)

    def test_conflict_flagged_when_both_endpoints_material_after_scaling(self):
        # tau = 20 vs tau = 80 -> sigma ratio 2; -80 bps over 80 steps is
        # -40 bps at the 20-step horizon, comfortably above a 10 bps threshold.
        preds = [
            HorizonPrediction(horizon_steps=20, predicted_return=0.0060),
            HorizonPrediction(horizon_steps=80, predicted_return=-0.0080),
        ]
        cfg = MultiHorizonConfig(
            symbol="TEST",
            weighting_scheme=WeightingScheme.EQUAL,
            conflict_policy=ConflictPolicy.REPORT_ONLY,
            conflict_threshold=0.001,
        )

        report = self.engine.synthesize_forecast(cfg, preds)

        self.assertTrue(report.has_directional_conflict)
        self.assertAlmostEqual(report.normalized_predictions[80], -0.0040, places=10)
        self.assertAlmostEqual(report.composite_alpha, 0.0010, places=10)

    def test_conflict_not_flagged_when_long_view_is_immaterial_at_target_horizon(self):
        # -10 bps over 500 steps is -1 bps at the 5-step horizon: below a 10 bps
        # threshold, so it does not veto a trade that will be re-evaluated long
        # before that view can materialize.
        preds = [
            HorizonPrediction(horizon_steps=5, predicted_return=0.0050),
            HorizonPrediction(horizon_steps=500, predicted_return=-0.0010),
        ]
        cfg = MultiHorizonConfig(
            symbol="TEST",
            weighting_scheme=WeightingScheme.EQUAL,
            conflict_threshold=0.001,
        )

        report = self.engine.synthesize_forecast(cfg, preds)

        self.assertFalse(report.has_directional_conflict)
        self.assertEqual(report.status, ForecastStatus.SYNTHESIZED.value)

    def test_conflict_requires_at_least_two_distinct_horizons(self):
        preds = [HorizonPrediction(horizon_steps=10, predicted_return=0.0030)]
        cfg = MultiHorizonConfig(symbol="TEST")

        report = self.engine.synthesize_forecast(cfg, preds)

        self.assertFalse(report.has_directional_conflict)
        self.assertAlmostEqual(report.composite_alpha, 0.0030, places=10)
        self.assertEqual(report.directional_consensus_pct, 100.0)

    def test_exact_threshold_boundary_counts_as_material(self):
        preds = [
            HorizonPrediction(horizon_steps=20, predicted_return=0.0010),
            HorizonPrediction(horizon_steps=80, predicted_return=-0.0020),
        ]
        cfg = MultiHorizonConfig(
            symbol="TEST",
            weighting_scheme=WeightingScheme.EQUAL,
            conflict_policy=ConflictPolicy.REPORT_ONLY,
            conflict_threshold=0.0010,
        )

        report = self.engine.synthesize_forecast(cfg, preds)

        # Both endpoints land exactly on 10 bps at the 20-step horizon.
        self.assertAlmostEqual(report.normalized_predictions[80], -0.0010, places=10)
        self.assertTrue(report.has_directional_conflict)


class TestConflictArbitration(unittest.TestCase):
    """The conflicting pair below composites to +10 bps before arbitration."""

    def setUp(self):
        self.engine = MultiHorizonForecasterEngine()
        self.preds = [
            HorizonPrediction(horizon_steps=20, predicted_return=0.0060),
            HorizonPrediction(horizon_steps=80, predicted_return=-0.0080),
        ]

    def _run(self, policy, **kwargs):
        cfg = MultiHorizonConfig(
            symbol="TEST",
            weighting_scheme=WeightingScheme.EQUAL,
            conflict_policy=policy,
            **kwargs,
        )
        return self.engine.synthesize_forecast(cfg, self.preds)

    def test_report_only_leaves_composite_untouched(self):
        report = self._run(ConflictPolicy.REPORT_ONLY)
        self.assertTrue(report.has_directional_conflict)
        self.assertAlmostEqual(report.composite_alpha, 0.0010, places=10)
        self.assertEqual(report.status, ForecastStatus.SYNTHESIZED.value)

    def test_dampen_scales_composite_by_damping_factor(self):
        report = self._run(ConflictPolicy.DAMPEN, conflict_damping=0.5)
        self.assertAlmostEqual(report.composite_alpha, 0.0005, places=10)
        self.assertEqual(report.status, ForecastStatus.DAMPENED_ON_CONFLICT.value)

    def test_suppress_zeroes_composite(self):
        report = self._run(ConflictPolicy.SUPPRESS)
        self.assertEqual(report.composite_alpha, 0.0)
        self.assertEqual(report.status, ForecastStatus.SUPPRESSED_ON_CONFLICT.value)

    def test_defer_long_follows_the_longest_horizon_alone(self):
        report = self._run(ConflictPolicy.DEFER_LONG)
        self.assertAlmostEqual(report.composite_alpha, -0.0040, places=10)
        self.assertEqual(report.status, ForecastStatus.DEFERRED_TO_LONG_HORIZON.value)

    def test_arbitration_does_not_fire_without_a_conflict(self):
        preds = [
            HorizonPrediction(horizon_steps=20, predicted_return=0.0060),
            HorizonPrediction(horizon_steps=80, predicted_return=0.0080),
        ]
        cfg = MultiHorizonConfig(
            symbol="TEST",
            weighting_scheme=WeightingScheme.EQUAL,
            conflict_policy=ConflictPolicy.SUPPRESS,
        )

        report = self.engine.synthesize_forecast(cfg, preds)

        self.assertFalse(report.has_directional_conflict)
        self.assertAlmostEqual(report.composite_alpha, 0.0050, places=10)
        self.assertEqual(report.status, ForecastStatus.SYNTHESIZED.value)


class TestInputValidation(unittest.TestCase):
    def setUp(self):
        self.engine = MultiHorizonForecasterEngine()

    def _expect_error(self, cfg, preds):
        with self.assertRaises(MultiHorizonError):
            self.engine.synthesize_forecast(cfg, preds)

    def test_empty_predictions_rejected(self):
        self._expect_error(MultiHorizonConfig(symbol="TEST"), [])

    def test_duplicate_horizon_rejected(self):
        # Regression: keying weights by horizon silently dropped one duplicate
        # from the weight total while still applying its weight to the sum, so
        # the applied weights added to more than 1 and the composite was
        # overstated. Duplicates are now a hard error.
        preds = [
            HorizonPrediction(horizon_steps=60, predicted_return=0.0010),
            HorizonPrediction(horizon_steps=60, predicted_return=-0.0010),
            HorizonPrediction(horizon_steps=5, predicted_return=0.0020),
        ]
        self._expect_error(MultiHorizonConfig(symbol="TEST"), preds)

    def test_non_finite_predicted_return_rejected(self):
        for bad in (float("nan"), float("inf"), float("-inf")):
            with self.subTest(value=bad):
                self._expect_error(
                    MultiHorizonConfig(symbol="TEST"),
                    [HorizonPrediction(horizon_steps=5, predicted_return=bad)],
                )

    def test_non_finite_ic_score_rejected(self):
        self._expect_error(
            MultiHorizonConfig(symbol="TEST"),
            [HorizonPrediction(horizon_steps=5, predicted_return=0.001, ic_score=float("nan"))],
        )

    def test_ic_score_outside_correlation_range_rejected(self):
        for bad in (1.5, -1.5):
            with self.subTest(value=bad):
                self._expect_error(
                    MultiHorizonConfig(symbol="TEST"),
                    [HorizonPrediction(horizon_steps=5, predicted_return=0.001, ic_score=bad)],
                )

    def test_confidence_outside_unit_interval_rejected(self):
        for bad in (-0.1, 1.1):
            with self.subTest(value=bad):
                self._expect_error(
                    MultiHorizonConfig(symbol="TEST"),
                    [HorizonPrediction(horizon_steps=5, predicted_return=0.001, confidence=bad)],
                )

    def test_non_positive_horizon_rejected(self):
        for bad in (0, -5):
            with self.subTest(value=bad):
                self._expect_error(
                    MultiHorizonConfig(symbol="TEST"),
                    [HorizonPrediction(horizon_steps=bad, predicted_return=0.001)],
                )

    def test_non_positive_horizon_volatility_rejected(self):
        self._expect_error(
            MultiHorizonConfig(symbol="TEST", scaling_mode=HorizonScaling.EXPLICIT_VOL),
            [HorizonPrediction(horizon_steps=5, predicted_return=0.001, horizon_volatility=0.0)],
        )

    def test_unknown_weighting_scheme_rejected_not_silently_equal_weighted(self):
        # A typo previously fell through to equal weighting and produced a
        # completely different signal with no warning.
        for bad in ("IC-WEIGHTED", "ic weighted", "TOTALLY_MADE_UP"):
            with self.subTest(value=bad):
                self._expect_error(
                    MultiHorizonConfig(symbol="TEST", weighting_scheme=bad),
                    [HorizonPrediction(horizon_steps=5, predicted_return=0.001)],
                )

    def test_unknown_scaling_mode_and_policy_rejected(self):
        preds = [HorizonPrediction(horizon_steps=5, predicted_return=0.001)]
        self._expect_error(MultiHorizonConfig(symbol="TEST", scaling_mode="LINEAR_TIME"), preds)
        self._expect_error(MultiHorizonConfig(symbol="TEST", conflict_policy="PANIC"), preds)

    def test_lowercase_scheme_string_is_accepted(self):
        report = self.engine.synthesize_forecast(
            MultiHorizonConfig(symbol="TEST", weighting_scheme="ic_weighted"),
            [HorizonPrediction(horizon_steps=5, predicted_return=0.001)],
        )
        self.assertEqual(report.weighting_scheme, "IC_WEIGHTED")

    def test_invalid_conflict_parameters_rejected(self):
        preds = [HorizonPrediction(horizon_steps=5, predicted_return=0.001)]
        self._expect_error(MultiHorizonConfig(symbol="TEST", conflict_threshold=-0.001), preds)
        self._expect_error(MultiHorizonConfig(symbol="TEST", conflict_damping=1.5), preds)
        self._expect_error(MultiHorizonConfig(symbol="TEST", target_horizon_steps=0), preds)


if __name__ == "__main__":
    unittest.main()
