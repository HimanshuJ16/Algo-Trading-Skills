"""Unit tests for exchange-matching-engine-behavior-under-load.

Expected latencies are derived independently of the implementation, from the closed-form
queueing results rather than by re-running the module's own expression:

  M/M/1 sojourn   W  = (1/mu) / (1 - rho)
  M/M/1 queueing  Wq = (1/mu) * rho / (1 - rho)
  M/D/1 queueing  Wq = (1/mu) * rho / (2 * (1 - rho))     [Pollaczek-Khinchine, cs^2 = 0]
  M/D/1 sojourn   W  = (1/mu) * (2 - rho) / (2 * (1 - rho))
"""
import dataclasses
import logging
import unittest

from exchange_matching_engine_behavior_under_load import (
    ExchangeMatchingEngineLoadSimulator,
    EngineLoadMetrics,
    MatchingEngineLoadAuditReport,
    SERVICE_MODEL_MD1,
    SERVICE_MODEL_MM1,
    SATURATION_RHO_CAP,
)

# Reference engine: 50,000 msgs/sec capacity => mean service time 1e6/50000 = 20.0 us,
# so baseline_latency_us and 1/mu are mutually consistent (ratio 1.0).
CAPACITY = 50_000.0
SERVICE_US = 20.0


def metrics(arrival: float, **overrides) -> EngineLoadMetrics:
    kwargs = dict(
        venue_id="CME_GLOBEX",
        baseline_latency_us=SERVICE_US,
        engine_capacity_msgs_per_sec=CAPACITY,
        arrival_rate_msgs_per_sec=arrival,
    )
    kwargs.update(overrides)
    return EngineLoadMetrics(**kwargs)


class TestDocumentedVerificationCases(unittest.TestCase):
    """The two cases SKILL.md's Verification section promises."""

    def setUp(self):
        self.simulator = ExchangeMatchingEngineLoadSimulator(
            high_congestion_threshold=0.85, moderate_congestion_threshold=0.50
        )

    def test_normal_conditions_low_utilization(self):
        # rho = 10,000 / 50,000 = 0.20 -> W = 20.0 / 0.80 = 25.0 us, Wq = 5.0 us
        report = self.simulator.simulate_matching_engine_load(metrics(10_000))
        self.assertEqual(report.utilization_factor_rho, 0.20)
        self.assertEqual(report.effective_latency_us, 25.0)
        self.assertEqual(report.queuing_delay_penalty_us, 5.0)
        self.assertEqual(report.latency_multiplier, 1.25)
        self.assertEqual(report.strategy_adaptation_directive, "NORMAL_OPERATIONS")
        self.assertEqual(report.adverse_selection_risk_level, "LOW")
        self.assertFalse(report.is_saturated)

    def test_high_congestion_spikes_latency_pauses_quoting(self):
        # rho = 45,000 / 50,000 = 0.90 -> W = 20.0 / 0.10 = 200.0 us, Wq = 180.0 us
        report = self.simulator.simulate_matching_engine_load(metrics(45_000))
        self.assertEqual(report.utilization_factor_rho, 0.90)
        self.assertEqual(report.effective_latency_us, 200.0)
        self.assertEqual(report.queuing_delay_penalty_us, 180.0)
        self.assertEqual(report.latency_multiplier, 10.0)
        self.assertEqual(report.strategy_adaptation_directive, "PAUSE_PASSIVE_QUOTING")
        self.assertEqual(report.adverse_selection_risk_level, "HIGH_SNIPING_RISK")
        self.assertFalse(report.is_saturated)

    def test_moderate_band_widens_spreads(self):
        # rho = 30,000 / 50,000 = 0.60 -> W = 20.0 / 0.40 = 50.0 us, Wq = 30.0 us
        report = self.simulator.simulate_matching_engine_load(metrics(30_000))
        self.assertEqual(report.effective_latency_us, 50.0)
        self.assertEqual(report.queuing_delay_penalty_us, 30.0)
        self.assertEqual(report.latency_multiplier, 2.5)
        self.assertEqual(report.strategy_adaptation_directive, "WIDEN_PASSIVE_SPREADS")
        self.assertEqual(report.adverse_selection_risk_level, "MODERATE")

    def test_zero_arrival_rate_is_unloaded_baseline(self):
        report = self.simulator.simulate_matching_engine_load(metrics(0.0))
        self.assertEqual(report.utilization_factor_rho, 0.0)
        self.assertEqual(report.effective_latency_us, SERVICE_US)
        self.assertEqual(report.queuing_delay_penalty_us, 0.0)
        self.assertEqual(report.latency_multiplier, 1.0)
        self.assertEqual(report.strategy_adaptation_directive, "NORMAL_OPERATIONS")


class TestThresholdBoundaries(unittest.TestCase):
    """Thresholds are non-strict lower bounds, evaluated on the EXACT rho."""

    def setUp(self):
        self.simulator = ExchangeMatchingEngineLoadSimulator(
            high_congestion_threshold=0.85, moderate_congestion_threshold=0.50
        )

    def _directive(self, arrival: float) -> str:
        return self.simulator.simulate_matching_engine_load(
            metrics(arrival)
        ).strategy_adaptation_directive

    def test_exactly_moderate_threshold_widens(self):
        self.assertEqual(self._directive(25_000), "WIDEN_PASSIVE_SPREADS")  # rho == 0.50

    def test_just_below_moderate_threshold_is_normal(self):
        self.assertEqual(self._directive(24_999), "NORMAL_OPERATIONS")  # rho == 0.49998

    def test_exactly_high_threshold_pauses(self):
        self.assertEqual(self._directive(42_500), "PAUSE_PASSIVE_QUOTING")  # rho == 0.85

    def test_just_below_high_threshold_does_not_pause(self):
        # Regression: rho = 42,499/50,000 = 0.84998. Rounding rho to 4 dp BEFORE the
        # threshold comparison yields 0.85 and wrongly halts quoting. The decision must
        # use the exact value; only the reported field is rounded.
        report = self.simulator.simulate_matching_engine_load(metrics(42_499))
        self.assertEqual(report.utilization_factor_rho, 0.85)  # rounded for reporting
        self.assertEqual(report.strategy_adaptation_directive, "WIDEN_PASSIVE_SPREADS")


class TestFixedLatencyIsAdditive(unittest.TestCase):
    """Load-independent latency must be added, never scaled by 1/(1 - rho)."""

    def setUp(self):
        self.simulator = ExchangeMatchingEngineLoadSimulator()

    def test_fixed_latency_added_not_multiplied(self):
        # 50 us of network transit + 20 us service + 180 us queueing at rho = 0.90.
        # Correct: 250.0 us. Folding the 50 us into the multiplied term instead would
        # give 70.0 / 0.10 = 700.0 us -- a 2.8x overstatement.
        report = self.simulator.simulate_matching_engine_load(
            metrics(45_000, fixed_latency_us=50.0)
        )
        self.assertEqual(report.effective_latency_us, 250.0)
        self.assertEqual(report.queuing_delay_penalty_us, 180.0)
        self.assertEqual(report.fixed_latency_us, 50.0)
        # multiplier is relative to the unloaded total (50 + 20 = 70): 250/70 = 3.571...
        self.assertEqual(report.latency_multiplier, 3.57)

    def test_fixed_latency_does_not_change_queueing_delay(self):
        without = self.simulator.simulate_matching_engine_load(metrics(45_000))
        with_fixed = self.simulator.simulate_matching_engine_load(
            metrics(45_000, fixed_latency_us=1_000.0)
        )
        self.assertEqual(
            without.queuing_delay_penalty_us, with_fixed.queuing_delay_penalty_us
        )

    def test_report_components_sum_to_effective_latency(self):
        report = self.simulator.simulate_matching_engine_load(
            metrics(30_000, fixed_latency_us=12.5)
        )
        self.assertAlmostEqual(
            report.effective_latency_us,
            report.fixed_latency_us
            + report.baseline_latency_us
            + report.queuing_delay_penalty_us,
            places=2,
        )


class TestServiceModels(unittest.TestCase):

    def test_md1_queueing_delay_is_exactly_half_of_mm1(self):
        mm1 = ExchangeMatchingEngineLoadSimulator(service_model=SERVICE_MODEL_MM1)
        md1 = ExchangeMatchingEngineLoadSimulator(service_model=SERVICE_MODEL_MD1)
        m = metrics(45_000)
        self.assertEqual(
            md1.simulate_matching_engine_load(m).queuing_delay_penalty_us,
            mm1.simulate_matching_engine_load(m).queuing_delay_penalty_us / 2.0,
        )

    def test_md1_sojourn_matches_closed_form(self):
        # W(M/D/1) = (1/mu) * (2 - rho) / (2 * (1 - rho)) = 20 * 1.10 / 0.20 = 110.0 us
        md1 = ExchangeMatchingEngineLoadSimulator(service_model=SERVICE_MODEL_MD1)
        report = md1.simulate_matching_engine_load(metrics(45_000))
        self.assertEqual(report.effective_latency_us, 110.0)
        self.assertEqual(report.queuing_delay_penalty_us, 90.0)
        self.assertEqual(report.service_model, SERVICE_MODEL_MD1)

    def test_default_service_model_is_the_conservative_mm1(self):
        self.assertEqual(
            ExchangeMatchingEngineLoadSimulator().service_model, SERVICE_MODEL_MM1
        )

    def test_service_models_agree_when_queue_is_empty(self):
        mm1 = ExchangeMatchingEngineLoadSimulator(service_model=SERVICE_MODEL_MM1)
        md1 = ExchangeMatchingEngineLoadSimulator(service_model=SERVICE_MODEL_MD1)
        m = metrics(0.0)
        self.assertEqual(
            mm1.simulate_matching_engine_load(m).effective_latency_us,
            md1.simulate_matching_engine_load(m).effective_latency_us,
        )


class TestSaturation(unittest.TestCase):
    """rho >= 1 has no steady state: the number reported is a censored lower bound."""

    def setUp(self):
        self.simulator = ExchangeMatchingEngineLoadSimulator()

    def test_rho_exactly_one_is_saturated(self):
        report = self.simulator.simulate_matching_engine_load(metrics(CAPACITY))
        self.assertEqual(report.utilization_factor_rho, 1.0)
        self.assertTrue(report.is_saturated)
        self.assertTrue(report.effective_latency_is_lower_bound)
        self.assertEqual(report.strategy_adaptation_directive, "PAUSE_PASSIVE_QUOTING")
        self.assertIn("LOWER BOUND", report.audit_notes)

    def test_overload_reports_true_rho_not_the_clamped_one(self):
        report = self.simulator.simulate_matching_engine_load(metrics(500_000))
        self.assertEqual(report.utilization_factor_rho, 10.0)
        self.assertTrue(report.is_saturated)

    def test_latency_is_censored_at_the_cap_across_overload_levels(self):
        # Both are the same censored figure: W at rho = SATURATION_RHO_CAP.
        expected = SERVICE_US / (1.0 - SATURATION_RHO_CAP)
        for arrival in (75_000, 500_000):
            report = self.simulator.simulate_matching_engine_load(metrics(arrival))
            self.assertAlmostEqual(report.effective_latency_us, expected, places=2)

    def test_below_saturation_is_not_flagged(self):
        report = self.simulator.simulate_matching_engine_load(metrics(49_400))  # rho = 0.988
        self.assertFalse(report.is_saturated)
        self.assertFalse(report.effective_latency_is_lower_bound)
        self.assertNotIn("LOWER BOUND", report.audit_notes)

    def test_clamped_but_stable_rho_is_marked_a_lower_bound(self):
        # rho = 0.99999998 is below saturation, so there IS a steady state -- but the
        # modelling cap binds at 0.99, and the true sojourn time is ~500,000x the figure
        # reported. Flagging only rho >= 1 would present that number as an estimate.
        report = self.simulator.simulate_matching_engine_load(metrics(49_999.999))
        self.assertFalse(report.is_saturated)
        self.assertTrue(report.effective_latency_is_lower_bound)
        self.assertIn("LOWER BOUND", report.audit_notes)

    def test_saturated_reports_are_also_lower_bounds(self):
        report = self.simulator.simulate_matching_engine_load(metrics(75_000))
        self.assertTrue(report.is_saturated)
        self.assertTrue(report.effective_latency_is_lower_bound)


class TestNumericOverflow(unittest.TestCase):

    def test_overflow_to_infinity_raises_rather_than_being_reported(self):
        # Every input here is finite, but 1/(1 - rho) amplification overflows. An
        # infinite "latency" must never be handed back as if it were a reading.
        m = EngineLoadMetrics(
            venue_id="V",
            baseline_latency_us=1e308,
            engine_capacity_msgs_per_sec=1e-302,
            arrival_rate_msgs_per_sec=0.99e-302,
        )
        with self.assertRaises(ValueError):
            ExchangeMatchingEngineLoadSimulator().simulate_matching_engine_load(m)

    def test_large_but_representable_values_are_returned(self):
        report = ExchangeMatchingEngineLoadSimulator().simulate_matching_engine_load(
            metrics(45_000, fixed_latency_us=1e6)
        )
        self.assertEqual(report.effective_latency_us, 1_000_200.0)


class TestInputValidation(unittest.TestCase):
    """Corrupt telemetry must raise, never resolve to an 'engine healthy' verdict."""

    def test_non_finite_values_rejected(self):
        for field in (
            "baseline_latency_us",
            "engine_capacity_msgs_per_sec",
            "arrival_rate_msgs_per_sec",
            "fixed_latency_us",
        ):
            for bad in (float("nan"), float("inf"), float("-inf")):
                with self.subTest(field=field, value=bad):
                    with self.assertRaises(ValueError):
                        metrics(10_000, **{field: bad})

    def test_nan_capacity_does_not_silently_report_low_load(self):
        # Regression: clamping a NaN rho used to collapse to 0.0, producing
        # NORMAL_OPERATIONS / LOW risk from unusable data.
        with self.assertRaises(ValueError):
            metrics(10_000, engine_capacity_msgs_per_sec=float("nan"))

    def test_non_positive_capacity_rejected(self):
        for bad in (0.0, -1.0):
            with self.subTest(capacity=bad):
                with self.assertRaises(ValueError):
                    metrics(10_000, engine_capacity_msgs_per_sec=bad)

    def test_non_positive_service_time_rejected(self):
        # 0.0 previously raised an unhandled ZeroDivisionError; negatives produced a
        # negative "latency" with no error at all.
        for bad in (0.0, -20.0):
            with self.subTest(baseline=bad):
                with self.assertRaises(ValueError):
                    metrics(10_000, baseline_latency_us=bad)

    def test_negative_arrival_rate_rejected(self):
        with self.assertRaises(ValueError):
            metrics(-10_000)

    def test_negative_fixed_latency_rejected(self):
        with self.assertRaises(ValueError):
            metrics(10_000, fixed_latency_us=-1.0)

    def test_empty_venue_id_rejected(self):
        for bad in ("", "   "):
            with self.subTest(venue_id=bad):
                with self.assertRaises(ValueError):
                    metrics(10_000, venue_id=bad)

    def test_wrong_argument_type_rejected(self):
        with self.assertRaises(TypeError):
            ExchangeMatchingEngineLoadSimulator().simulate_matching_engine_load(
                {"venue_id": "CME_GLOBEX"}
            )


class TestConstructorValidation(unittest.TestCase):

    def test_inverted_thresholds_rejected(self):
        # Left unchecked, the high branch is evaluated first and every rho above the
        # (lower) high threshold pauses quoting, so the moderate band never fires.
        with self.assertRaises(ValueError):
            ExchangeMatchingEngineLoadSimulator(
                high_congestion_threshold=0.50, moderate_congestion_threshold=0.85
            )

    def test_equal_thresholds_rejected(self):
        with self.assertRaises(ValueError):
            ExchangeMatchingEngineLoadSimulator(
                high_congestion_threshold=0.85, moderate_congestion_threshold=0.85
            )

    def test_out_of_range_thresholds_rejected(self):
        for kwargs in (
            {"high_congestion_threshold": 1.5},
            {"moderate_congestion_threshold": 0.0},
            {"moderate_congestion_threshold": -0.1},
            {"high_congestion_threshold": float("nan")},
        ):
            with self.subTest(**kwargs):
                with self.assertRaises(ValueError):
                    ExchangeMatchingEngineLoadSimulator(**kwargs)

    def test_unknown_service_model_rejected(self):
        # M/G/1 is named in the standards note but is not implemented here.
        with self.assertRaises(ValueError):
            ExchangeMatchingEngineLoadSimulator(service_model="M/G/1")


class TestServiceTimeConsistency(unittest.TestCase):
    """Guards the commonest misuse: a round-trip latency passed as a service time."""

    def setUp(self):
        self.simulator = ExchangeMatchingEngineLoadSimulator()

    def test_consistent_inputs_emit_no_warning(self):
        logger_name = "exchange_matching_engine_behavior_under_load"
        with self.assertLogs(logger_name, level=logging.DEBUG) as captured:
            logging.getLogger(logger_name).debug("probe")
            report = self.simulator.simulate_matching_engine_load(metrics(10_000))
        self.assertEqual(report.service_time_consistency_ratio, 1.0)
        self.assertEqual(report.implied_service_time_us, 20.0)
        self.assertFalse([r for r in captured.records if r.levelno >= logging.WARNING])

    def test_round_trip_latency_passed_as_service_time_warns(self):
        # 100 us wire-to-wire against a 50,000 msg/s engine implies 1/mu = 20 us: 5x off.
        logger_name = "exchange_matching_engine_behavior_under_load"
        with self.assertLogs(logger_name, level=logging.WARNING) as captured:
            report = self.simulator.simulate_matching_engine_load(
                metrics(10_000, baseline_latency_us=100.0)
            )
        self.assertEqual(report.service_time_consistency_ratio, 5.0)
        self.assertTrue(
            any("SERVICE TIME INCONSISTENT" in r.getMessage() for r in captured.records)
        )


class TestReportIsImmutable(unittest.TestCase):

    def test_audit_report_cannot_be_mutated(self):
        report = ExchangeMatchingEngineLoadSimulator().simulate_matching_engine_load(
            metrics(45_000)
        )
        self.assertIsInstance(report, MatchingEngineLoadAuditReport)
        with self.assertRaises(dataclasses.FrozenInstanceError):
            report.strategy_adaptation_directive = "NORMAL_OPERATIONS"

    def test_metrics_cannot_be_mutated_after_validation(self):
        m = metrics(10_000)
        with self.assertRaises(dataclasses.FrozenInstanceError):
            m.arrival_rate_msgs_per_sec = 90_000


if __name__ == "__main__":
    unittest.main()
