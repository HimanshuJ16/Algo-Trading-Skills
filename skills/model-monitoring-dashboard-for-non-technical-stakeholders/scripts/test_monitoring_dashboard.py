"""Unit tests for model-monitoring-dashboard-for-non-technical-stakeholders.

The band edges under test are the *documented contract* from
`references/standards.md`, written out as explicit points rather than recomputed
from the implementation's own expressions, so a change to a comparison operator
fails a test instead of moving the expectation with it.

Tests marked "regression" fail against a naive implementation.
"""
import json
import logging
import unittest

from monitoring_dashboard import (
    ACTION_HALT,
    ACTION_NONE,
    ACTION_RESTORE_TELEMETRY,
    ACTION_RETRAIN,
    DashboardConfigError,
    DashboardInputError,
    DashboardThresholds,
    HealthStatus,
    NonTechnicalMonitoringDashboard,
)

# Keep the suite output clean; the dashboard logs an ERROR on every RED case.
logging.getLogger("monitoring_dashboard").addHandler(logging.NullHandler())
logging.getLogger("monitoring_dashboard").propagate = False

LATENCY_BUDGET = DashboardThresholds(
    latency_green_max_ms=50.0, latency_amber_max_ms=100.0)

# A snapshot in which every component is comfortably GREEN, so a single varied
# argument is the only thing that can drive the overall status.
HEALTHY = dict(accuracy_pct=58.5, staleness_days=5, feature_drift_psi=0.04, latency_ms=12.0)


class TestHealthyAndBreachingSnapshots(unittest.TestCase):
    def setUp(self):
        self.dashboard = NonTechnicalMonitoringDashboard(LATENCY_BUDGET)

    def test_healthy_model_returns_green(self):
        report = self.dashboard.evaluate_health(model_name="alpha_v1", **HEALTHY)

        self.assertEqual(report.overall_status, HealthStatus.GREEN)
        self.assertEqual(report.recommended_action, ACTION_NONE)
        self.assertEqual(report.driving_components, [])
        self.assertTrue(all(c.measured for c in report.components))

    def test_severe_feature_drift_returns_red_and_halt_action(self):
        report = self.dashboard.evaluate_health(
            model_name="alpha_v1", **{**HEALTHY, "feature_drift_psi": 0.35})

        self.assertEqual(report.overall_status, HealthStatus.RED)
        self.assertEqual(report.recommended_action, ACTION_HALT)
        self.assertIn("CRITICAL DASHBOARD ALERT", report.summary_message)

    def test_red_headline_names_the_breaching_component(self):
        """A colour with no named cause sends the reader back to the raw telemetry."""
        report = self.dashboard.evaluate_health(
            model_name="alpha_v1", **{**HEALTHY, "feature_drift_psi": 0.35})

        self.assertEqual(report.driving_components, ["Feature Drift PSI"])
        self.assertIn("Feature Drift PSI", report.summary_message)

    def test_red_headline_states_the_action_is_advisory(self):
        report = self.dashboard.evaluate_health(
            model_name="alpha_v1", **{**HEALTHY, "accuracy_pct": 30.0})

        self.assertIn("advisory", report.summary_message)

    def test_accuracy_crash_is_not_masked_by_healthy_components(self):
        """The documented aggregation rule is worst-component, never an average."""
        report = self.dashboard.evaluate_health(
            model_name="alpha_v1",
            accuracy_pct=30.0, staleness_days=0, feature_drift_psi=0.0, latency_ms=0.1)

        self.assertEqual(report.overall_status, HealthStatus.RED)
        self.assertEqual(report.driving_components, ["Prediction Accuracy"])

    def test_red_dominates_a_simultaneous_amber(self):
        report = self.dashboard.evaluate_health(
            model_name="alpha_v1",
            accuracy_pct=52.0,        # AMBER
            staleness_days=45,        # RED
            feature_drift_psi=0.15,   # AMBER
            latency_ms=12.0)

        self.assertEqual(report.overall_status, HealthStatus.RED)
        self.assertEqual(report.recommended_action, ACTION_HALT)
        self.assertEqual(report.driving_components, ["Model Age"])

    def test_multiple_red_components_are_all_named(self):
        report = self.dashboard.evaluate_health(
            model_name="alpha_v1",
            accuracy_pct=30.0, staleness_days=90, feature_drift_psi=0.04, latency_ms=12.0)

        self.assertEqual(report.driving_components, ["Prediction Accuracy", "Model Age"])

    def test_repeated_evaluation_is_deterministic(self):
        first = self.dashboard.evaluate_health(model_name="alpha_v1", **HEALTHY)
        second = self.dashboard.evaluate_health(model_name="alpha_v1", **HEALTHY)

        self.assertEqual(first.to_dict(), second.to_dict())


class TestLatencyComponent(unittest.TestCase):
    """Regression: older accepted latency_ms and never evaluated it."""

    def setUp(self):
        self.dashboard = NonTechnicalMonitoringDashboard(LATENCY_BUDGET)

    def test_latency_breach_is_red_not_green(self):
        report = self.dashboard.evaluate_health(
            model_name="alpha_v1", **{**HEALTHY, "latency_ms": 250.0})

        self.assertEqual(report.overall_status, HealthStatus.RED)
        self.assertEqual(report.recommended_action, ACTION_HALT)
        self.assertEqual(report.driving_components, ["Inference Latency"])

    def test_latency_band_edges(self):
        cases = [(49.9, HealthStatus.GREEN), (50.0, HealthStatus.GREEN),
                 (50.1, HealthStatus.AMBER), (100.0, HealthStatus.AMBER),
                 (100.1, HealthStatus.RED)]
        for latency_ms, expected in cases:
            with self.subTest(latency_ms=latency_ms):
                report = self.dashboard.evaluate_health(
                    model_name="alpha_v1", **{**HEALTHY, "latency_ms": latency_ms})
                self.assertEqual(report.components[3].status, expected)

    def test_unconfigured_latency_budget_is_amber_not_green(self):
        """Regression: an ungoverned metric must be a visible gap, not a pass."""
        dashboard = NonTechnicalMonitoringDashboard()  # no latency bounds

        report = dashboard.evaluate_health(model_name="alpha_v1", **HEALTHY)

        self.assertEqual(report.overall_status, HealthStatus.AMBER)
        self.assertEqual(report.recommended_action, ACTION_RESTORE_TELEMETRY)
        latency = report.components[3]
        self.assertFalse(latency.measured)
        self.assertIsNone(latency.value)

    def test_latency_can_be_declared_out_of_scope(self):
        dashboard = NonTechnicalMonitoringDashboard(monitor_latency=False)

        report = dashboard.evaluate_health(
            model_name="eod_rebalance", accuracy_pct=58.5,
            staleness_days=5, feature_drift_psi=0.04)

        self.assertEqual(report.overall_status, HealthStatus.GREEN)
        self.assertFalse(report.latency_monitored)
        self.assertEqual(len(report.components), 3)

    def test_latency_supplied_while_out_of_scope_warns(self):
        dashboard = NonTechnicalMonitoringDashboard(monitor_latency=False)

        with self.assertLogs("monitoring_dashboard", level="WARNING") as captured:
            report = dashboard.evaluate_health(
                model_name="eod_rebalance", accuracy_pct=58.5,
                staleness_days=5, feature_drift_psi=0.04, latency_ms=99999.0)

        self.assertEqual(report.overall_status, HealthStatus.GREEN)
        self.assertIn("not graded", "\n".join(captured.output))


class TestBandEdges(unittest.TestCase):
    def setUp(self):
        self.dashboard = NonTechnicalMonitoringDashboard(LATENCY_BUDGET)

    def _status_of(self, index, **overrides):
        report = self.dashboard.evaluate_health(model_name="alpha_v1", **{**HEALTHY, **overrides})
        return report.components[index].status

    def test_accuracy_band_edges(self):
        cases = [(55.0, HealthStatus.GREEN), (54.99, HealthStatus.AMBER),
                 (50.0, HealthStatus.AMBER), (49.99, HealthStatus.RED)]
        for accuracy_pct, expected in cases:
            with self.subTest(accuracy_pct=accuracy_pct):
                self.assertEqual(self._status_of(0, accuracy_pct=accuracy_pct), expected)

    def test_staleness_band_edges(self):
        cases = [(14, HealthStatus.GREEN), (15, HealthStatus.AMBER),
                 (30, HealthStatus.AMBER), (31, HealthStatus.RED)]
        for staleness_days, expected in cases:
            with self.subTest(staleness_days=staleness_days):
                self.assertEqual(self._status_of(1, staleness_days=staleness_days), expected)

    def test_psi_band_edges_follow_the_lewis_convention(self):
        """Regression: older put exactly 0.10 in GREEN and exactly 0.25 in AMBER.

        Yurdakul & Naranjo (2020) state the rule of thumb as PSI < 0.10 little
        change, 0.10 <= PSI < 0.25 moderate, 0.25 <= PSI significant, so each
        edge belongs to the worse band.
        """
        cases = [(0.0999, HealthStatus.GREEN), (0.10, HealthStatus.AMBER),
                 (0.2499, HealthStatus.AMBER), (0.25, HealthStatus.RED),
                 (0.2501, HealthStatus.RED)]
        for psi, expected in cases:
            with self.subTest(psi=psi):
                self.assertEqual(self._status_of(2, feature_drift_psi=psi), expected)

    def test_custom_thresholds_are_honoured(self):
        strict = NonTechnicalMonitoringDashboard(DashboardThresholds(
            accuracy_green_min_pct=60.0, accuracy_amber_min_pct=57.0,
            staleness_green_max_days=2, staleness_amber_max_days=4,
            drift_psi_green_max=0.02, drift_psi_amber_max=0.05,
            latency_green_max_ms=1.0, latency_amber_max_ms=2.0))

        report = strict.evaluate_health(model_name="alpha_v1", **HEALTHY)

        # The same snapshot that is uniformly GREEN under the defaults:
        # 58.5% -> AMBER, 5 days -> RED, PSI 0.04 -> AMBER, 12ms -> RED.
        self.assertEqual(
            [c.status for c in report.components],
            [HealthStatus.AMBER, HealthStatus.RED, HealthStatus.AMBER, HealthStatus.RED])
        self.assertEqual(report.overall_status, HealthStatus.RED)
        self.assertEqual(report.driving_components, ["Model Age", "Inference Latency"])


class TestUnmeasuredTelemetry(unittest.TestCase):
    def setUp(self):
        self.dashboard = NonTechnicalMonitoringDashboard(LATENCY_BUDGET)

    def test_each_missing_metric_is_amber_with_a_null_value(self):
        for index, key in enumerate(
                ["accuracy_pct", "staleness_days", "feature_drift_psi", "latency_ms"]):
            with self.subTest(metric=key):
                report = self.dashboard.evaluate_health(
                    model_name="alpha_v1", **{**HEALTHY, key: None})
                component = report.components[index]
                self.assertEqual(component.status, HealthStatus.AMBER)
                self.assertFalse(component.measured)
                self.assertIsNone(component.value)
                self.assertEqual(report.overall_status, HealthStatus.AMBER)

    def test_missing_metric_recommends_restoring_telemetry_not_retraining(self):
        """Retraining is the wrong instruction when the fault is in the pipeline."""
        report = self.dashboard.evaluate_health(
            model_name="alpha_v1", **{**HEALTHY, "feature_drift_psi": None})

        self.assertEqual(report.recommended_action, ACTION_RESTORE_TELEMETRY)
        self.assertIn("not measured", report.summary_message)
        self.assertIn("unverified, not proven good", report.summary_message)

    def test_measured_amber_recommends_retrain(self):
        report = self.dashboard.evaluate_health(
            model_name="alpha_v1", **{**HEALTHY, "feature_drift_psi": 0.15})

        self.assertEqual(report.recommended_action, ACTION_RETRAIN)
        self.assertEqual(report.driving_components, ["Feature Drift PSI"])

    def test_measured_amber_takes_precedence_over_a_telemetry_gap(self):
        report = self.dashboard.evaluate_health(
            model_name="alpha_v1",
            accuracy_pct=None, staleness_days=5, feature_drift_psi=0.15, latency_ms=12.0)

        self.assertEqual(report.recommended_action, ACTION_RETRAIN)

    def test_a_red_metric_still_outranks_a_telemetry_gap(self):
        report = self.dashboard.evaluate_health(
            model_name="alpha_v1",
            accuracy_pct=None, staleness_days=5, feature_drift_psi=0.40, latency_ms=12.0)

        self.assertEqual(report.overall_status, HealthStatus.RED)
        self.assertEqual(report.recommended_action, ACTION_HALT)


class TestInputValidation(unittest.TestCase):
    """Regression: older graded impossible telemetry GREEN, and NaN silently RED."""

    def setUp(self):
        self.dashboard = NonTechnicalMonitoringDashboard(LATENCY_BUDGET)

    def _assert_rejected(self, **overrides):
        with self.assertRaises(DashboardInputError):
            self.dashboard.evaluate_health(model_name="alpha_v1", **{**HEALTHY, **overrides})

    def test_negative_model_age_is_rejected(self):
        """A clock skew must not present as a fresh model."""
        self._assert_rejected(staleness_days=-5)

    def test_accuracy_outside_zero_to_one_hundred_is_rejected(self):
        self._assert_rejected(accuracy_pct=150.0)
        self._assert_rejected(accuracy_pct=-1.0)

    def test_negative_psi_is_rejected(self):
        self._assert_rejected(feature_drift_psi=-3.0)

    def test_negative_latency_is_rejected(self):
        self._assert_rejected(latency_ms=-1.0)

    def test_non_finite_metrics_are_rejected(self):
        for key in ("accuracy_pct", "feature_drift_psi", "latency_ms"):
            for bad in (float("nan"), float("inf"), float("-inf")):
                with self.subTest(metric=key, value=bad):
                    self._assert_rejected(**{key: bad})

    def test_non_numeric_metrics_are_rejected(self):
        self._assert_rejected(staleness_days="7")
        self._assert_rejected(accuracy_pct="58.5")
        self._assert_rejected(feature_drift_psi=[0.04])

    def test_boolean_metrics_are_rejected(self):
        """`isinstance(True, int)` is True, so bools need an explicit guard."""
        self._assert_rejected(staleness_days=True)
        self._assert_rejected(accuracy_pct=True)

    def test_fractional_model_age_is_rejected(self):
        self._assert_rejected(staleness_days=5.5)

    def test_empty_model_name_is_rejected(self):
        for name in ("", "   ", None, 42):
            with self.subTest(model_name=name):
                with self.assertRaises(DashboardInputError):
                    self.dashboard.evaluate_health(model_name=name, **HEALTHY)

    def test_numeric_scalars_from_a_telemetry_frame_are_accepted(self):
        """A numpy/pandas integer scalar must not be rejected as "not a number".

        Uses a stdlib ``numbers.Integral`` stand-in so the test does not take a
        numpy dependency; the guard under test is ``numbers.Integral``/``Real``
        rather than ``int``/``float``, which is what a ``numpy.int64`` needs.
        """
        import numbers

        class FrameInt(numbers.Integral):
            def __init__(self, v):
                self._v = v

            def __int__(self):
                return self._v

            def __index__(self):
                return self._v

            # numbers.Integral is abstract; the dashboard only needs int() and
            # ordering, so the rest delegate to the wrapped value.
            def __lt__(self, other):
                return self._v < other

            def __le__(self, other):
                return self._v <= other

            def __eq__(self, other):
                return self._v == other

            def __hash__(self):
                return hash(self._v)

            def _unsupported(self, *_args, **_kwargs):
                raise NotImplementedError

            __abs__ = __add__ = __and__ = __ceil__ = __floor__ = _unsupported
            __floordiv__ = __invert__ = __lshift__ = __mod__ = __mul__ = _unsupported
            __neg__ = __or__ = __pos__ = __pow__ = __radd__ = __rand__ = _unsupported
            __rfloordiv__ = __rlshift__ = __rmod__ = __rmul__ = __ror__ = _unsupported
            __round__ = __rpow__ = __rrshift__ = __rshift__ = __rtruediv__ = _unsupported
            __rxor__ = __truediv__ = __trunc__ = __xor__ = _unsupported

        report = self.dashboard.evaluate_health(
            model_name="alpha_v1", **{**HEALTHY, "staleness_days": FrameInt(5)})

        self.assertEqual(report.components[1].status, HealthStatus.GREEN)
        self.assertEqual(report.components[1].value, 5.0)

    def test_fraction_scale_accuracy_warns_about_units(self):
        """0.58 is a legal percentage but almost always a mis-scaled 58%."""
        with self.assertLogs("monitoring_dashboard", level="WARNING") as captured:
            report = self.dashboard.evaluate_health(
                model_name="alpha_v1", **{**HEALTHY, "accuracy_pct": 0.58})

        self.assertIn("looks like a fraction", "\n".join(captured.output))
        self.assertEqual(report.components[0].status, HealthStatus.RED)


class TestThresholdConfiguration(unittest.TestCase):
    def test_inverted_accuracy_bands_are_rejected(self):
        with self.assertRaises(DashboardConfigError):
            DashboardThresholds(accuracy_green_min_pct=50.0, accuracy_amber_min_pct=55.0)

    def test_accuracy_band_above_one_hundred_is_rejected(self):
        with self.assertRaises(DashboardConfigError):
            DashboardThresholds(accuracy_green_min_pct=120.0)

    def test_inverted_staleness_bands_are_rejected(self):
        with self.assertRaises(DashboardConfigError):
            DashboardThresholds(staleness_green_max_days=30, staleness_amber_max_days=14)

    def test_negative_staleness_band_is_rejected(self):
        with self.assertRaises(DashboardConfigError):
            DashboardThresholds(staleness_green_max_days=-1)

    def test_inverted_psi_bands_are_rejected(self):
        with self.assertRaises(DashboardConfigError):
            DashboardThresholds(drift_psi_green_max=0.30, drift_psi_amber_max=0.25)

    def test_zero_psi_band_is_rejected(self):
        with self.assertRaises(DashboardConfigError):
            DashboardThresholds(drift_psi_green_max=0.0)

    def test_half_configured_latency_budget_is_rejected(self):
        with self.assertRaises(DashboardConfigError):
            DashboardThresholds(latency_green_max_ms=50.0)
        with self.assertRaises(DashboardConfigError):
            DashboardThresholds(latency_amber_max_ms=100.0)

    def test_inverted_latency_bands_are_rejected(self):
        with self.assertRaises(DashboardConfigError):
            DashboardThresholds(latency_green_max_ms=100.0, latency_amber_max_ms=50.0)

    def test_non_finite_band_is_rejected(self):
        with self.assertRaises(DashboardConfigError):
            DashboardThresholds(drift_psi_green_max=float("nan"))

    def test_wrong_threshold_type_is_rejected(self):
        with self.assertRaises(DashboardConfigError):
            NonTechnicalMonitoringDashboard(thresholds={"accuracy_green_min_pct": 55.0})

    def test_non_boolean_monitor_latency_is_rejected(self):
        with self.assertRaises(DashboardConfigError):
            NonTechnicalMonitoringDashboard(monitor_latency="yes")


class TestReportSerialisation(unittest.TestCase):
    def test_report_is_json_serialisable_and_keeps_unmeasured_values_null(self):
        dashboard = NonTechnicalMonitoringDashboard(LATENCY_BUDGET)

        report = dashboard.evaluate_health(
            model_name="alpha_v1", **{**HEALTHY, "latency_ms": None})
        payload = json.loads(json.dumps(report.to_dict()))

        self.assertEqual(payload["overall_status"], "AMBER")
        self.assertEqual(payload["recommended_action"], ACTION_RESTORE_TELEMETRY)
        self.assertTrue(payload["latency_monitored"])
        latency = payload["components"][3]
        self.assertIsNone(latency["value"])
        self.assertFalse(latency["measured"])


if __name__ == "__main__":
    unittest.main()
