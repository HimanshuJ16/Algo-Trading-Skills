"""
Unit tests for the model-staleness-detection skill.

Expected values are derived independently of the implementation:

  - PSI figures come from the closed form ``sum((a - e) * ln(a / e))`` applied
    to bin proportions that are known by construction (a reference sample of
    100 distinct values over 10 bins puts exactly 0.1 in each), not from
    re-running the module's own binner.
  - The Gaussian-baseline figure comes from the closed form of the
    J-divergence for two equal-variance normals, ``PSI = z**2``.
  - Wilson bounds come from Wilson (1927) evaluated by hand.
  - Binomial false-halt rates come from the exact binomial distribution.

Every ``regression`` test below fails against the v1 implementation and passes
against the current one; each names the v1 behaviour it pins down.
"""
import logging
import math
import unittest
from statistics import NormalDist

from staleness_monitor import (
    DriftMethod,
    FeatureDriftStatus,
    ModelHealthStatus,
    ModelStalenessMonitor,
    RollingAccuracy,
    feature_drift_score,
    population_stability_index,
    wilson_lower_bound,
)

# Several tests deliberately drive the monitor into states it logs loudly
# about. Silence the module logger so the suite's output stays readable;
# assertLogs re-enables it for the one test that asserts on a log record.
logging.getLogger("staleness_monitor").setLevel(logging.CRITICAL)

_ZERO_FLOOR = 1e-4


def psi_closed_form(expected_props, actual_props):
    """PSI straight from known bin proportions, with the conventional floor."""
    total = 0.0
    for e, a in zip(expected_props, actual_props):
        e = max(e, _ZERO_FLOOR)
        a = max(a, _ZERO_FLOOR)
        total += (a - e) * math.log(a / e)
    return total


def deterministic_normal(n, mean=0.0, sigma=1.0):
    """n evenly spaced quantiles of N(mean, sigma). Deterministic: no seed, no
    sampling noise to make a threshold assertion flaky."""
    dist = NormalDist(mean, sigma)
    return [dist.inv_cdf((i + 0.5) / n) for i in range(n)]


class TestPopulationStabilityIndex(unittest.TestCase):
    """The statistic the skill claims to compute."""

    def setUp(self):
        # 100 distinct values over 10 bins -> exactly 0.1 per reference bin.
        self.reference = [float(v) for v in range(100)]

    def test_reference_bins_are_equal_by_construction(self):
        # Underpins every expected value below: if the binner did not produce
        # equal reference bins, the hand-derived proportions would be wrong.
        from staleness_monitor import _bin_proportions, _quantile_edges

        edges = _quantile_edges(self.reference, 10)
        props = _bin_proportions(self.reference, edges)
        self.assertEqual(len(props), 10)
        for p in props:
            self.assertAlmostEqual(p, 0.1, places=12)
        self.assertAlmostEqual(sum(props), 1.0, places=12)

    def test_psi_matches_closed_form_on_known_proportions(self):
        # All current mass relocated into the top bin: actual = (0,...,0, 1.0).
        current = [95.0] * 50
        expected = psi_closed_form([0.1] * 10, [0.0] * 9 + [1.0])
        self.assertAlmostEqual(expected, 8.283089, places=6)
        self.assertAlmostEqual(
            population_stability_index(self.reference, current, bins=10),
            expected,
            places=9,
        )

    def test_psi_is_zero_for_identical_distributions(self):
        self.assertEqual(
            population_stability_index(self.reference, self.reference, bins=10), 0.0
        )

    def test_psi_counts_mass_that_left_the_reference_support(self):
        # 40 of 100 current observations sit far above every reference value.
        # A bounded-edge implementation discards them; here the proportions
        # still sum to 1.0, so the relocation is measured.
        from staleness_monitor import _bin_proportions, _quantile_edges

        current = [1000.0] * 40 + [float(v) for v in range(60)]
        edges = _quantile_edges(self.reference, 10)
        props = _bin_proportions(current, edges)
        self.assertAlmostEqual(sum(props), 1.0, places=12)
        self.assertAlmostEqual(props[-1], 0.40, places=12)
        self.assertGreaterEqual(
            population_stability_index(self.reference, current, bins=10), 0.25
        )

    def test_psi_survives_low_cardinality_bin_collapse(self):
        # A 95/5 regime flag over 10 bins de-duplicates to a single quantile
        # edge; without the fallback, PSI is identically 0.0 whatever the live
        # sample does. Expected value from the two-bin proportions.
        reference = [0.0] * 95 + [1.0] * 5
        current = [0.0] * 50 + [1.0] * 50
        expected = psi_closed_form([0.95, 0.05], [0.50, 0.50])
        self.assertAlmostEqual(expected, 1.324998, places=6)
        self.assertAlmostEqual(
            population_stability_index(reference, current, bins=10), expected, places=9
        )

    def test_psi_rejects_unusable_input_instead_of_returning_zero(self):
        with self.assertRaises(ValueError):
            population_stability_index([], [1.0, 2.0])
        with self.assertRaises(ValueError):
            population_stability_index(self.reference, [])
        with self.assertRaises(ValueError):
            population_stability_index(self.reference, [1.0, float("nan")])
        with self.assertRaises(ValueError):
            population_stability_index(self.reference, [float("inf")] * 5)
        with self.assertRaises(ValueError):
            # Constant reference: every bin scheme collapses.
            population_stability_index([7.0] * 50, [1.0, 2.0, 3.0])
        with self.assertRaises(ValueError):
            population_stability_index(self.reference, [1.0], bins=1)


class TestDriftStatisticRegressions(unittest.TestCase):
    """Regressions against the v1 ``0.5 * z**2`` statistic reported as PSI."""

    def test_regression_variance_only_shift_is_detected(self):
        # v1 derived drift from the standardised *mean* gap alone, so a feature
        # whose spread tripled with its mean unmoved scored psi = 0.0 and
        # is_drifting = False. PSI sees the scale change.
        reference = deterministic_normal(200, 0.0, 1.0)
        current = [3.0 * v for v in reference]

        self.assertAlmostEqual(sum(reference) / 200, sum(current) / 200, places=9)
        # The v1-era location statistic is blind to it.
        self.assertLess(
            feature_drift_score(sum(current) / 200, 0.0, sum(reference) / 200, 1.0),
            1e-9,
        )
        self.assertGreaterEqual(
            population_stability_index(reference, current, bins=10), 0.25
        )

        monitor = ModelStalenessMonitor(window=60, min_predictions=30)
        monitor.set_training_baseline({"vol": {"reference_sample": reference}})
        result = monitor.compute_feature_drift("vol", current)
        self.assertTrue(result.is_drifting)
        self.assertEqual(result.method, DriftMethod.PSI_BINNED)

    def test_regression_gaussian_baseline_uses_jeffreys_not_half_kl(self):
        # With mean/std only, PSI has the closed form z**2 for two equal-
        # variance normals. v1 reported 0.5 * z**2 - the one-directional KL -
        # under the name psi_score, so every value met the 0.10/0.25 bands at
        # half scale. z = 2 here: 4.0 correct, 2.0 under v1.
        monitor = ModelStalenessMonitor(window=60, min_predictions=30, min_live_values=4)
        monitor.set_training_baseline({"vol": {"mean": 0.02, "std": 0.005}})
        result = monitor.compute_feature_drift("vol", [0.03, 0.03, 0.03, 0.03])
        self.assertAlmostEqual(result.z_score_distance, 2.0, places=6)
        self.assertAlmostEqual(result.psi_score, 4.0, places=6)
        self.assertEqual(result.method, DriftMethod.GAUSSIAN_JEFFREYS)

    def test_regression_unregistered_feature_is_not_scored_against_a_guess(self):
        # v1 fell back to an implicit mean=0.0, std=1.0 baseline for any
        # unknown name, so a typo produced a confident number against a
        # distribution nothing was trained on - and, for small-valued features,
        # a reassuring "not drifting".
        monitor = ModelStalenessMonitor(window=60, min_predictions=30, min_live_values=2)
        monitor.set_training_baseline({"volatility": {"mean": 0.02, "std": 0.005}})
        result = monitor.compute_feature_drift("voaltility", [0.001, 0.002])
        self.assertEqual(result.status, FeatureDriftStatus.NO_BASELINE)
        self.assertIsNone(result.psi_score)
        self.assertIsNone(result.z_score_distance)
        self.assertFalse(result.is_measurable)

    def test_regression_non_finite_live_values_are_not_reported_as_clean(self):
        # NaN propagated through v1's arithmetic and every `>=` comparison
        # against NaN is False, so a NaN-poisoned feature came back
        # is_drifting=False. Inf came back is_drifting=True with psi=inf.
        monitor = ModelStalenessMonitor(window=60, min_predictions=30, min_live_values=2)
        monitor.set_training_baseline({"vol": {"mean": 0.02, "std": 0.005}})
        for bad in (float("nan"), float("inf"), float("-inf")):
            result = monitor.compute_feature_drift("vol", [0.02, bad])
            self.assertEqual(result.status, FeatureDriftStatus.NON_FINITE)
            self.assertIsNone(result.psi_score)

    def test_regression_empty_live_batch_is_not_reported_as_clean(self):
        # v1: a feature that stopped being produced scored psi = 0.0,
        # is_drifting = False - indistinguishable from a healthy feed.
        monitor = ModelStalenessMonitor(window=60, min_predictions=30)
        monitor.set_training_baseline({"vol": {"mean": 0.02, "std": 0.005}})
        result = monitor.compute_feature_drift("vol", [])
        self.assertEqual(result.status, FeatureDriftStatus.NO_LIVE_DATA)
        self.assertIsNone(result.psi_score)

    def test_regression_constant_training_baseline_is_reported_not_scored(self):
        # v1 returned (0.0, 0.0, False) for train_std == 0, so a feature that
        # never varied in training and swings wildly live read as healthy.
        monitor = ModelStalenessMonitor(window=60, min_predictions=30, min_live_values=2)
        monitor.set_training_baseline({"flag": {"mean": 1.0, "std": 0.0}})
        result = monitor.compute_feature_drift("flag", [9.0, 11.0])
        self.assertEqual(result.status, FeatureDriftStatus.DEGENERATE_BASELINE)
        self.assertIsNone(result.psi_score)
        self.assertFalse(result.is_drifting)

    def test_insufficient_live_values_are_not_scored(self):
        monitor = ModelStalenessMonitor(window=60, min_predictions=30, min_live_values=20)
        monitor.set_training_baseline({"vol": {"mean": 0.02, "std": 0.005}})
        result = monitor.compute_feature_drift("vol", [0.9] * 5)
        self.assertEqual(result.status, FeatureDriftStatus.INSUFFICIENT_LIVE_DATA)
        self.assertIsNone(result.psi_score)
        self.assertEqual(result.live_sample_size, 5)

    def test_baseline_validation_rejects_malformed_specs(self):
        monitor = ModelStalenessMonitor(window=60, min_predictions=30, min_live_values=5)
        with self.assertRaises(ValueError):
            monitor.set_training_baseline({"a": {"mean": 1.0}})  # no std
        with self.assertRaises(ValueError):
            monitor.set_training_baseline({"a": {"mean": float("nan"), "std": 1.0}})
        with self.assertRaises(ValueError):
            monitor.set_training_baseline({"a": {"mean": 0.0, "std": -1.0}})
        with self.assertRaises(ValueError):
            monitor.set_training_baseline({"a": {"reference_sample": [1.0, 2.0]}})
        with self.assertRaises(ValueError):
            monitor.set_training_baseline(
                {"a": {"reference_sample": [1.0, 2.0, 3.0, 4.0, float("nan")]}}
            )
        with self.assertRaises(ValueError):
            monitor.set_training_baseline({"a": "not-a-dict"})


class TestWilsonLowerBound(unittest.TestCase):
    def test_known_values(self):
        # Wilson (1927), one-sided at 95% (z = 1.6448536269514722).
        self.assertAlmostEqual(wilson_lower_bound(33, 60), 0.444482, places=6)
        self.assertAlmostEqual(wilson_lower_bound(140, 250), 0.507992, places=6)

    def test_stays_inside_the_unit_interval_at_the_extremes(self):
        # The normal approximation gives a zero-width interval at p_hat = 1.
        self.assertAlmostEqual(wilson_lower_bound(10, 10), 0.787058, places=6)
        self.assertEqual(wilson_lower_bound(0, 10), 0.0)

    def test_rejects_invalid_input(self):
        with self.assertRaises(ValueError):
            wilson_lower_bound(1, 0)
        with self.assertRaises(ValueError):
            wilson_lower_bound(11, 10)
        with self.assertRaises(ValueError):
            wilson_lower_bound(-1, 10)


class TestRollingMetrics(unittest.TestCase):
    def setUp(self):
        self.monitor = ModelStalenessMonitor(window=10, min_predictions=5)

    def test_regression_empty_window_is_none_not_perfect(self):
        # v1 returned 1.0 from get_rolling_accuracy() on an empty window.
        self.assertIsNone(self.monitor.get_rolling_accuracy())
        self.assertIsNone(self.monitor.accuracy_lower_bound())
        self.assertIsNone(self.monitor.get_rolling_precision())

    def test_accuracy_counts_matches(self):
        for _ in range(8):
            self.monitor.record_prediction(1, 1)
        for _ in range(2):
            self.monitor.record_prediction(1, 0)
        self.assertEqual(self.monitor.get_rolling_accuracy(), 0.8)

    def test_window_evicts_oldest_outcomes(self):
        for _ in range(10):
            self.monitor.record_prediction(1, 0)
        for _ in range(10):
            self.monitor.record_prediction(1, 1)
        self.assertEqual(len(self.monitor.predictions), 10)
        self.assertEqual(self.monitor.get_rolling_accuracy(), 1.0)

    def test_precision_is_conditioned_on_the_predicted_label(self):
        # 4 long calls, 3 right; 6 short calls, all wrong. Accuracy 0.3,
        # precision on the long side 0.75.
        for _ in range(3):
            self.monitor.record_prediction(1, 1)
        self.monitor.record_prediction(1, -1)
        for _ in range(6):
            self.monitor.record_prediction(-1, 1)
        self.assertEqual(self.monitor.get_rolling_accuracy(), 0.3)
        self.assertEqual(self.monitor.get_rolling_precision(1), 0.75)
        self.assertEqual(self.monitor.get_rolling_precision(-1), 0.0)
        self.assertIsNone(self.monitor.get_rolling_precision("never-predicted"))

    def test_regression_labels_are_not_coerced(self):
        # v1 compared int(predicted) == int(actual): "1" equalled 1, and a
        # regressor's 1.2 and 1.7 collapsed onto the same class.
        with self.assertRaises(ValueError):
            self.monitor.record_prediction(1.7, 1.2)
        with self.assertRaises(ValueError):
            self.monitor.record_prediction(None, 1)
        self.monitor.record_prediction("1", 1)
        self.assertEqual(self.monitor.get_rolling_accuracy(), 0.0)

    def test_restore_history_rebuilds_the_window_after_a_restart(self):
        # The window is in memory; without this a restart leaves the health
        # gate with no evidence.
        source = ModelStalenessMonitor(window=10, min_predictions=5)
        for i in range(10):
            source.record_prediction(1, 1 if i % 2 == 0 else 0)
        restored = ModelStalenessMonitor(window=10, min_predictions=5)
        restored.restore_history(source.export_history())
        self.assertEqual(restored.export_history(), source.export_history())
        self.assertEqual(
            restored.get_rolling_accuracy(), source.get_rolling_accuracy()
        )

    def test_restore_history_keeps_only_the_most_recent_window(self):
        restored = ModelStalenessMonitor(window=10, min_predictions=5)
        restored.restore_history([(1, 0)] * 5 + [(1, 1)] * 20)
        self.assertEqual(len(restored.export_history()), 10)
        self.assertEqual(restored.get_rolling_accuracy(), 1.0)


class TestHealthStateMachine(unittest.TestCase):
    def setUp(self):
        self.alerts = []
        self.monitor = ModelStalenessMonitor(
            window=60,
            min_accuracy_threshold=0.55,
            min_predictions=30,
            alert_fn=self.alerts.append,
        )
        self.monitor.set_training_baseline(
            {"volatility": {"reference_sample": deterministic_normal(200, 0.02, 0.005)}}
        )
        self.clean_batch = deterministic_normal(60, 0.02, 0.005)

    def _record(self, correct, incorrect):
        for _ in range(correct):
            self.monitor.record_prediction(1, 1)
        for _ in range(incorrect):
            self.monitor.record_prediction(1, 0)

    def test_regression_cold_start_is_not_healthy(self):
        # v1: an empty window gave accuracy 1.0 -> HEALTHY at 1.0x sizing, so a
        # freshly restarted monitor certified a model it had never observed.
        report = self.monitor.evaluate_health()
        self.assertEqual(report.status, ModelHealthStatus.INSUFFICIENT_DATA)
        self.assertEqual(report.sizing_multiplier, 0.0)
        self.assertIsNone(report.rolling_accuracy)
        self.assertEqual(report.sample_size, 0)

    def test_regression_one_correct_prediction_does_not_certify_the_model(self):
        self._record(1, 0)
        report = self.monitor.evaluate_health()
        self.assertEqual(report.status, ModelHealthStatus.INSUFFICIENT_DATA)
        self.assertEqual(report.sizing_multiplier, 0.0)

    def test_healthy_model_gets_full_size(self):
        self._record(48, 12)  # 0.80
        report = self.monitor.evaluate_health({"volatility": self.clean_batch})
        self.assertEqual(report.status, ModelHealthStatus.HEALTHY)
        self.assertEqual(report.sizing_multiplier, 1.0)
        self.assertEqual(report.rolling_accuracy, 0.8)
        self.assertEqual(report.drifted_features_count, 0)
        self.assertAlmostEqual(report.max_psi, 0.0, places=9)

    def test_regression_a_single_accuracy_breach_does_not_halt(self):
        # A genuinely 55%-accurate model breaches a 52% threshold on 34.7% of
        # independent 60-observation windows (exact binomial). v1 halted and
        # zeroed the size on the first such window.
        monitor = ModelStalenessMonitor(
            window=60, min_accuracy_threshold=0.52, min_predictions=30
        )
        for i in range(60):
            monitor.record_prediction(1, 1 if i < 30 else 0)  # 0.50
        report = monitor.evaluate_health()
        self.assertEqual(report.status, ModelHealthStatus.DEGRADED_WARNING)
        self.assertEqual(report.sizing_multiplier, 0.5)
        self.assertEqual(report.consecutive_accuracy_breaches, 1)

    def test_sustained_accuracy_breach_halts(self):
        self._record(24, 36)  # 0.40, below 0.55
        for expected in (
            ModelHealthStatus.DEGRADED_WARNING,
            ModelHealthStatus.DEGRADED_WARNING,
            ModelHealthStatus.HALTED_STALE,
        ):
            report = self.monitor.evaluate_health()
            self.assertEqual(report.status, expected)
        self.assertEqual(report.sizing_multiplier, 0.0)
        self.assertEqual(report.consecutive_accuracy_breaches, 3)
        self.assertIn("HALT MODEL", report.action_required)

    def test_accuracy_exactly_at_threshold_is_not_a_breach(self):
        monitor = ModelStalenessMonitor(
            window=60, min_accuracy_threshold=0.50, min_predictions=30
        )
        for i in range(60):
            monitor.record_prediction(1, 1 if i < 30 else 0)  # exactly 0.50
        report = monitor.evaluate_health()
        self.assertEqual(report.consecutive_accuracy_breaches, 0)
        self.assertEqual(report.status, ModelHealthStatus.DEGRADED_WARNING)

    def test_breach_streak_resets_when_accuracy_recovers(self):
        self._record(24, 36)
        self.monitor.evaluate_health()
        self.assertEqual(self.monitor.evaluate_health().consecutive_accuracy_breaches, 2)
        self.monitor.restore_history([(1, 1)] * 60)
        self.assertEqual(self.monitor.evaluate_health().consecutive_accuracy_breaches, 0)

    def test_regression_a_single_feature_at_the_halt_threshold_halts(self):
        # v1 accepted psi_halt_threshold and never read it; the halt condition
        # was a hard-coded "3 or more drifting features", so one catastrophically
        # relocated feature out of two could not halt the model.
        monitor = ModelStalenessMonitor(
            window=60, min_predictions=30, min_live_values=4, psi_halt_threshold=4.0
        )
        monitor.set_training_baseline({"vol": {"mean": 0.02, "std": 0.005}})
        monitor.restore_history([(1, 1)] * 60)
        report = monitor.evaluate_health({"vol": [0.03] * 4})  # z = 2 -> PSI = 4.0
        self.assertEqual(report.status, ModelHealthStatus.HALTED_STALE)
        self.assertEqual(report.sizing_multiplier, 0.0)
        self.assertEqual(report.max_psi_feature, "vol")
        self.assertAlmostEqual(report.max_psi, 4.0, places=6)

    def test_moderate_drift_degrades_without_halting(self):
        self._record(48, 12)
        drifted = deterministic_normal(60, 0.0225, 0.005)  # z = 0.5 -> PSI ~ 0.25
        monitor = ModelStalenessMonitor(
            window=60,
            min_predictions=30,
            min_live_values=4,
            psi_warning_threshold=0.10,
            psi_halt_threshold=4.0,
        )
        monitor.set_training_baseline({"vol": {"mean": 0.02, "std": 0.005}})
        monitor.restore_history([(1, 1)] * 60)
        report = monitor.evaluate_health({"vol": drifted})
        self.assertEqual(report.status, ModelHealthStatus.DEGRADED_WARNING)
        self.assertEqual(report.sizing_multiplier, 0.5)
        self.assertEqual(report.drifted_features_count, 1)

    def test_regression_non_finite_features_halt_the_model(self):
        self._record(48, 12)
        report = self.monitor.evaluate_health({"volatility": [float("nan")] * 60})
        self.assertEqual(report.status, ModelHealthStatus.HALTED_STALE)
        self.assertEqual(report.sizing_multiplier, 0.0)
        self.assertEqual(
            report.unevaluable_features,
            (("volatility", FeatureDriftStatus.NON_FINITE),),
        )

    def test_registered_feature_absent_from_the_batch_is_reported(self):
        # Monitoring 3 of 5 registered features silently looks identical to
        # monitoring all 5 and finding nothing.
        self._record(48, 12)
        report = self.monitor.evaluate_health({})
        self.assertEqual(report.status, ModelHealthStatus.DEGRADED_WARNING)
        self.assertEqual(
            report.unevaluable_features,
            (("volatility", FeatureDriftStatus.MISSING_FROM_BATCH),),
        )

    def test_no_batch_at_all_is_accuracy_only_monitoring(self):
        # ``None`` is a deliberate mode, distinct from an empty dict.
        self._record(48, 12)
        report = self.monitor.evaluate_health()
        self.assertEqual(report.status, ModelHealthStatus.HEALTHY)
        self.assertEqual(report.unevaluable_features, ())

    def test_boolean_indicator_features_are_measured_not_rejected(self):
        # A binary flag is a legitimate feature; rejecting it as "non-finite"
        # would halt the model on a wrong diagnosis.
        monitor = ModelStalenessMonitor(window=60, min_predictions=30, min_live_values=4)
        monitor.set_training_baseline(
            {"halted": {"reference_sample": [0.0] * 95 + [1.0] * 5}}
        )
        result = monitor.compute_feature_drift("halted", [False, False, True, True])
        self.assertEqual(result.status, FeatureDriftStatus.OK)
        self.assertIsNotNone(result.psi_score)

    def test_unmeasurable_feature_degrades_and_is_named(self):
        self._record(48, 12)
        report = self.monitor.evaluate_health({"voaltility": self.clean_batch})
        self.assertEqual(report.status, ModelHealthStatus.DEGRADED_WARNING)
        self.assertEqual(report.sizing_multiplier, 0.5)
        # A typo produces two findings, and both matter: the name that resolves
        # to nothing, and the real feature that is consequently unmonitored.
        self.assertEqual(
            report.unevaluable_features,
            (
                ("voaltility", FeatureDriftStatus.NO_BASELINE),
                ("volatility", FeatureDriftStatus.MISSING_FROM_BATCH),
            ),
        )
        self.assertIn("NO_BASELINE", report.action_required)

    def test_halt_latches_until_a_human_clears_it(self):
        self._record(48, 12)
        self.monitor.evaluate_health({"volatility": [float("nan")] * 60})
        # Feed a perfectly clean batch: the halt must not lift on its own.
        for _ in range(3):
            report = self.monitor.evaluate_health({"volatility": self.clean_batch})
            self.assertEqual(report.status, ModelHealthStatus.HALTED_STALE)
            self.assertTrue(report.halt_latched)
            self.assertEqual(report.sizing_multiplier, 0.0)
        self.monitor.clear_halt(operator="risk-desk", reason="feed repaired, model refit")
        report = self.monitor.evaluate_health({"volatility": self.clean_batch})
        self.assertEqual(report.status, ModelHealthStatus.HEALTHY)
        self.assertFalse(report.halt_latched)

    def test_clear_halt_requires_attribution(self):
        with self.assertRaises(ValueError):
            self.monitor.clear_halt(operator="", reason="because")
        with self.assertRaises(ValueError):
            self.monitor.clear_halt(operator="risk-desk", reason="")

    def test_unlatched_monitor_can_recover_on_its_own(self):
        monitor = ModelStalenessMonitor(
            window=60, min_predictions=30, min_live_values=4, latch_halt=False
        )
        monitor.set_training_baseline({"vol": {"mean": 0.02, "std": 0.005}})
        monitor.restore_history([(1, 1)] * 60)
        self.assertEqual(
            monitor.evaluate_health({"vol": [float("nan")] * 4}).status,
            ModelHealthStatus.HALTED_STALE,
        )
        # Recovery still passes through the DEGRADED hold-down.
        statuses = [
            monitor.evaluate_health({"vol": [0.02] * 4}).status for _ in range(4)
        ]
        self.assertEqual(statuses[-1], ModelHealthStatus.HEALTHY)

    def test_recovery_from_degraded_requires_consecutive_healthy_evaluations(self):
        # Without the hold-down, sizing flaps 1.0 -> 0.5 -> 1.0 as the estimate
        # crosses back and forth, churning position size on noise.
        self._record(33, 27)  # 0.55, inside the warning band
        self.assertEqual(
            self.monitor.evaluate_health().status, ModelHealthStatus.DEGRADED_WARNING
        )
        self.monitor.restore_history([(1, 1)] * 60)
        statuses = [self.monitor.evaluate_health().status for _ in range(3)]
        self.assertEqual(
            statuses,
            [
                ModelHealthStatus.DEGRADED_WARNING,
                ModelHealthStatus.DEGRADED_WARNING,
                ModelHealthStatus.HEALTHY,
            ],
        )

    def test_regression_alerts_fire_on_transitions_not_every_evaluation(self):
        # v1 re-fired the alert on every evaluation while halted. A channel that
        # repeats itself gets muted, and a muted channel misses the next event.
        self._record(24, 36)
        for _ in range(6):
            self.monitor.evaluate_health()
        self.assertEqual(
            self.alerts.count(self.alerts[-1]), 1, msg=f"duplicate alerts: {self.alerts}"
        )
        self.assertEqual(len(self.alerts), 2)  # DEGRADED, then HALTED
        self.assertIn("DEGRADED_WARNING", self.alerts[0])
        self.assertIn("HALTED_STALE", self.alerts[1])

    def test_a_broken_alert_channel_does_not_break_the_risk_gate(self):
        def explode(_message):
            raise RuntimeError("pager is down")

        monitor = ModelStalenessMonitor(
            window=60, min_predictions=30, alert_fn=explode
        )
        monitor.restore_history([(1, 0)] * 60)
        with self.assertLogs("staleness_monitor", level=logging.ERROR):
            report = monitor.evaluate_health()
        self.assertEqual(report.status, ModelHealthStatus.DEGRADED_WARNING)

    def test_report_carries_the_confidence_bound_next_to_the_point_estimate(self):
        self._record(33, 27)
        report = self.monitor.evaluate_health()
        self.assertEqual(report.rolling_accuracy, 0.55)
        self.assertAlmostEqual(report.accuracy_lower_bound, 0.4445, places=4)
        self.assertLess(report.accuracy_lower_bound, report.rolling_accuracy)


class TestConfigurationValidation(unittest.TestCase):
    def test_regression_zero_window_is_rejected(self):
        # v1 accepted window=0, deque(maxlen=0) discarded every outcome, and
        # the monitor reported accuracy 1.0 / HEALTHY forever.
        with self.assertRaises(ValueError):
            ModelStalenessMonitor(window=0)
        with self.assertRaises(ValueError):
            ModelStalenessMonitor(window=-5)

    def test_min_predictions_cannot_exceed_the_window(self):
        with self.assertRaises(ValueError):
            ModelStalenessMonitor(window=10, min_predictions=30)

    def test_threshold_validation(self):
        with self.assertRaises(ValueError):
            ModelStalenessMonitor(min_accuracy_threshold=1.5)
        with self.assertRaises(ValueError):
            ModelStalenessMonitor(min_accuracy_threshold=0.0)
        with self.assertRaises(ValueError):
            ModelStalenessMonitor(psi_warning_threshold=0.30, psi_halt_threshold=0.25)
        with self.assertRaises(ValueError):
            ModelStalenessMonitor(psi_warning_threshold=0.0)
        with self.assertRaises(ValueError):
            ModelStalenessMonitor(consecutive_breaches_to_halt=0)
        with self.assertRaises(ValueError):
            ModelStalenessMonitor(recovery_evaluations=0)
        with self.assertRaises(ValueError):
            ModelStalenessMonitor(warmup_sizing_multiplier=1.5)
        with self.assertRaises(ValueError):
            ModelStalenessMonitor(psi_bins=1)
        with self.assertRaises(ValueError):
            ModelStalenessMonitor(confidence_level=1.0)


class TestBackwardCompatibility(unittest.TestCase):
    def test_rolling_accuracy_helper(self):
        ra = RollingAccuracy(window=5)
        self.assertIsNone(ra.accuracy())
        ra.record(1, 1)
        ra.record(1, 0)
        self.assertEqual(ra.accuracy(), 0.5)

    def test_feature_drift_score_helper(self):
        self.assertEqual(
            feature_drift_score(live_mean=15, live_std=2, train_mean=10, train_std=2),
            2.5,
        )
        self.assertEqual(
            feature_drift_score(live_mean=15, live_std=2, train_mean=10, train_std=0),
            float("inf"),
        )
        self.assertEqual(
            feature_drift_score(live_mean=10, live_std=2, train_mean=10, train_std=0),
            0.0,
        )

    def test_status_enum_still_compares_equal_to_v1_string_literals(self):
        self.assertEqual(ModelHealthStatus.HEALTHY, "HEALTHY")
        self.assertEqual(ModelHealthStatus.DEGRADED_WARNING, "DEGRADED_WARNING")
        self.assertEqual(ModelHealthStatus.HALTED_STALE, "HALTED_STALE")


if __name__ == "__main__":
    unittest.main()
