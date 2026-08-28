"""Behavioural tests for the risk-control latency budgeter.

Expected values are derived by hand from the timestamps in each fixture, never by
re-running the module's own arithmetic.
"""
import logging
import threading
import unittest

from risk_latency_budgeter import (
    LatencyEndState,
    LatencyError,
    MeasurementStatus,
    RiskControlLatencyBudgeter,
    logger,
)


class _CaptureHandler(logging.Handler):
    """Collects emitted records so log level can be asserted on any Python 3.8+."""

    def __init__(self):
        super().__init__(level=logging.DEBUG)
        self.records = []

    def emit(self, record):
        self.records.append(record)


class TestStageDecomposition(unittest.TestCase):
    def setUp(self):
        self.budgeter = RiskControlLatencyBudgeter(50.0)

    def test_stages_decompose_the_measured_window(self):
        # event=1000, start=1010, end=1015, sent=1030, ack=1040 =>
        # ingest 10, eval 5, transmission 15, ack 10; to-send 30, to-ack 40.
        trace = self.budgeter.record_trace("drawdown", 1000, 1010, 1015, 1030, t_ack_ms=1040)
        self.assertEqual(trace.ingest_latency_ms, 10.0)
        self.assertEqual(trace.eval_latency_ms, 5.0)
        self.assertEqual(trace.transmission_latency_ms, 15.0)
        self.assertEqual(trace.acknowledgement_latency_ms, 10.0)
        self.assertEqual(trace.total_to_send_ms, 30.0)
        self.assertEqual(trace.total_to_ack_ms, 40.0)
        self.assertEqual(trace.status, MeasurementStatus.PASS)

    def test_stage_sum_equals_total_to_send(self):
        trace = self.budgeter.record_trace("kill", 0, 3, 11, 26)
        self.assertEqual(
            trace.ingest_latency_ms + trace.eval_latency_ms + trace.transmission_latency_ms,
            trace.total_to_send_ms,
        )

    def test_total_latency_alias_tracks_dispatch(self):
        trace = self.budgeter.record_trace("kill", 0, 1, 2, 3, t_ack_ms=900)
        self.assertEqual(trace.total_latency_ms, trace.total_to_send_ms)
        self.assertEqual(trace.total_latency_ms, 3.0)


class TestRequiredEndState(unittest.TestCase):
    """A dispatch timestamp proves local work only; containment needs the acknowledgement."""

    def setUp(self):
        self.budgeter = RiskControlLatencyBudgeter(50.0)

    def test_dispatch_end_state_audits_only_the_send_window(self):
        # Cancel dispatched in 5 ms but acknowledged 3005 ms after the event.
        trace = self.budgeter.record_trace("cancel_all", 0, 1, 2, 5, t_ack_ms=3005)
        self.assertEqual(trace.required_end_state, LatencyEndState.DISPATCH)
        self.assertEqual(trace.audited_latency_ms, 5.0)
        self.assertEqual(trace.total_to_ack_ms, 3005.0)
        self.assertEqual(trace.status, MeasurementStatus.PASS)

    def test_acknowledgement_end_state_breaches_on_the_same_timestamps(self):
        # Regression: the identical trace that passes on DISPATCH must breach on
        # ACKNOWLEDGEMENT. 3005 ms > 50 ms budget.
        trace = self.budgeter.record_trace(
            "cancel_all", 0, 1, 2, 5, t_ack_ms=3005, end_state=LatencyEndState.ACKNOWLEDGEMENT
        )
        self.assertEqual(trace.audited_latency_ms, 3005.0)
        self.assertEqual(trace.status, MeasurementStatus.BREACH)
        self.assertTrue(trace.is_sla_violated)
        self.assertTrue(trace.budget_exceeded)

    def test_missing_acknowledgement_is_uncertain_not_pass(self):
        trace = self.budgeter.record_trace(
            "cancel_all", 0, 1, 2, 5, end_state=LatencyEndState.ACKNOWLEDGEMENT
        )
        self.assertIsNone(trace.audited_latency_ms)
        self.assertIsNone(trace.budget_exceeded)
        self.assertEqual(trace.status, MeasurementStatus.UNCERTAIN)
        self.assertFalse(trace.is_sla_violated)

    def test_constructor_default_end_state_applies(self):
        budgeter = RiskControlLatencyBudgeter(
            50.0, default_end_state=LatencyEndState.ACKNOWLEDGEMENT
        )
        trace = budgeter.record_trace("kill", 0, 1, 2, 5, t_ack_ms=900)
        self.assertEqual(trace.required_end_state, LatencyEndState.ACKNOWLEDGEMENT)
        self.assertEqual(trace.audited_latency_ms, 900.0)
        self.assertEqual(trace.status, MeasurementStatus.BREACH)

    def test_bottleneck_is_scoped_to_the_audited_window(self):
        budgeter = RiskControlLatencyBudgeter(5000.0)
        # Slowest stage overall is the 900 ms acknowledgement, but it is outside a
        # dispatch-budgeted window, so it must not be named the bottleneck there.
        dispatch = budgeter.record_trace("kill", 0, 1, 20, 21, t_ack_ms=921)
        self.assertEqual(dispatch.primary_bottleneck, "EVALUATION")
        acked = budgeter.record_trace(
            "kill", 0, 1, 20, 21, t_ack_ms=921, end_state=LatencyEndState.ACKNOWLEDGEMENT
        )
        self.assertEqual(acked.primary_bottleneck, "ACKNOWLEDGEMENT")


class TestBudgetBoundary(unittest.TestCase):
    def setUp(self):
        self.budgeter = RiskControlLatencyBudgeter(50.0)

    def test_latency_equal_to_budget_passes(self):
        trace = self.budgeter.record_trace("kill", 0, 10, 20, 50)
        self.assertEqual(trace.audited_latency_ms, 50.0)
        self.assertEqual(trace.status, MeasurementStatus.PASS)
        self.assertFalse(trace.budget_exceeded)

    def test_one_microsecond_over_budget_breaches(self):
        trace = self.budgeter.record_trace("kill", 0, 10, 20, 50.001)
        self.assertEqual(trace.status, MeasurementStatus.BREACH)

    def test_comparison_uses_the_unrounded_value(self):
        # 50.0004 ms rounds to 50.0 in the report but is over a 50 ms budget.
        trace = self.budgeter.record_trace("kill", 0, 10, 20, 50.0004)
        self.assertEqual(trace.audited_latency_ms, 50.0)
        self.assertEqual(trace.status, MeasurementStatus.BREACH)

    def test_per_call_budget_overrides_the_default(self):
        trace = self.budgeter.record_trace("kill", 0, 10, 20, 30, sla_budget_ms=25.0)
        self.assertEqual(trace.sla_budget_ms, 25.0)
        self.assertEqual(trace.status, MeasurementStatus.BREACH)


class TestClockTrust(unittest.TestCase):
    def setUp(self):
        self.budgeter = RiskControlLatencyBudgeter(50.0)

    def test_unsynchronized_clock_is_uncertain_not_pass(self):
        trace = self.budgeter.record_trace("kill", 0, 1, 2, 3, clock_synchronized=False)
        self.assertEqual(trace.status, MeasurementStatus.UNCERTAIN)
        self.assertFalse(self.budgeter.summarize_audit().is_risk_pipeline_healthy)

    def test_gross_overrun_stays_visible_under_an_unsynchronized_clock(self):
        # 5000 ms against a 50 ms budget is not explained by clock skew: the status is
        # uncertain, but the raw comparison must still be reported.
        trace = self.budgeter.record_trace("kill", 0, 1, 2, 5000, clock_synchronized=False)
        self.assertEqual(trace.status, MeasurementStatus.UNCERTAIN)
        self.assertTrue(trace.budget_exceeded)
        summary = self.budgeter.summarize_audit()
        self.assertEqual(summary.budget_exceeded_count, 1)
        self.assertEqual(summary.sla_breaches_count, 0)
        self.assertFalse(summary.is_risk_pipeline_healthy)

    def test_backwards_timestamps_are_rejected_not_clamped(self):
        with self.assertRaises(LatencyError):
            self.budgeter.record_trace("kill", 10, 9, 11, 12)
        with self.assertRaises(LatencyError):
            self.budgeter.record_trace("kill", 0, 1, 2, 3, t_ack_ms=2.5)
        self.assertEqual(self.budgeter.summarize_audit().total_traces, 0)


class TestInputValidation(unittest.TestCase):
    def setUp(self):
        self.budgeter = RiskControlLatencyBudgeter(50.0)

    def test_non_finite_timestamps_are_rejected(self):
        for bad in (float("nan"), float("inf"), float("-inf")):
            with self.subTest(bad=bad):
                with self.assertRaises(LatencyError):
                    self.budgeter.record_trace("kill", 0, 1, 2, bad)
        with self.assertRaises(LatencyError):
            self.budgeter.record_trace("kill", 0, 1, 2, 3, t_ack_ms=float("nan"))

    def test_boolean_timestamp_is_rejected(self):
        with self.assertRaises(LatencyError):
            self.budgeter.record_trace("kill", False, 1, 2, 3)

    def test_boolean_budget_is_rejected_before_it_becomes_one_millisecond(self):
        # bool is an int subclass: float(True) == 1.0 would silently install a 1 ms budget.
        with self.assertRaises(LatencyError):
            self.budgeter.record_trace("kill", 0, 1, 2, 3, sla_budget_ms=True)

    def test_non_positive_and_non_numeric_budgets_are_rejected(self):
        for bad in (0, -1.0, "50", float("nan"), float("inf")):
            with self.subTest(bad=bad):
                with self.assertRaises(LatencyError):
                    self.budgeter.record_trace("kill", 0, 1, 2, 3, sla_budget_ms=bad)

    def test_blank_control_name_is_rejected(self):
        for bad in ("", "   ", 7):
            with self.subTest(bad=bad):
                with self.assertRaises(LatencyError):
                    self.budgeter.record_trace(bad, 0, 1, 2, 3)

    def test_control_name_is_stripped(self):
        self.assertEqual(self.budgeter.record_trace("  kill  ", 0, 1, 2, 3).control_name, "kill")

    def test_non_boolean_clock_flag_is_rejected(self):
        with self.assertRaises(LatencyError):
            self.budgeter.record_trace("kill", 0, 1, 2, 3, clock_synchronized="yes")

    def test_invalid_end_state_is_rejected(self):
        with self.assertRaises(LatencyError):
            self.budgeter.record_trace("kill", 0, 1, 2, 3, end_state="ACKNOWLEDGEMENT")
        with self.assertRaises(LatencyError):
            RiskControlLatencyBudgeter(50.0, default_end_state="DISPATCH")

    def test_invalid_constructor_arguments_are_rejected(self):
        for bad in (float("nan"), 0, -5):
            with self.subTest(bad=bad):
                with self.assertRaises(LatencyError):
                    RiskControlLatencyBudgeter(bad)
        for bad in (0, -1, 2.5, True):
            with self.subTest(max_traces=bad):
                with self.assertRaises(LatencyError):
                    RiskControlLatencyBudgeter(50.0, max_traces=bad)


class TestAuditSummary(unittest.TestCase):
    def setUp(self):
        self.budgeter = RiskControlLatencyBudgeter(50.0)

    def test_no_traces_is_unhealthy_not_healthy(self):
        # Silent instrumentation must not read as a compliant risk pipeline.
        summary = self.budgeter.summarize_audit()
        self.assertEqual(summary.total_traces, 0)
        self.assertFalse(summary.is_risk_pipeline_healthy)
        self.assertFalse(summary.p99_resolvable)
        self.assertIn("unevidenced", summary.message)

    def test_unknown_control_filter_is_unhealthy(self):
        self.budgeter.record_trace("kill", 0, 1, 2, 3)
        summary = self.budgeter.summarize_audit("does-not-exist")
        self.assertEqual(summary.total_traces, 0)
        self.assertFalse(summary.is_risk_pipeline_healthy)

    def test_per_control_filtering(self):
        self.budgeter.record_trace("kill", 0, 10, 110, 120)
        self.budgeter.record_trace("other", 0, 1, 2, 3)
        summary = self.budgeter.summarize_audit("kill")
        self.assertEqual(summary.total_traces, 1)
        self.assertEqual(summary.sla_breaches_count, 1)
        self.assertEqual(summary.p99_total_latency_ms, 120.0)
        self.assertFalse(summary.is_risk_pipeline_healthy)

    def test_uncertain_traces_are_excluded_from_the_distribution(self):
        # Trustworthy: 10 ms and 20 ms. Untrustworthy: 1000 ms on an unsynchronized clock.
        # Mean over the measured pair is 15.0; including the third would give 343.333.
        self.budgeter.record_trace("kill", 0, 1, 2, 10)
        self.budgeter.record_trace("kill", 0, 1, 2, 20)
        self.budgeter.record_trace("kill", 0, 1, 2, 1000, clock_synchronized=False)
        summary = self.budgeter.summarize_audit("kill")
        self.assertEqual(summary.total_traces, 3)
        self.assertEqual(summary.measured_traces, 2)
        self.assertEqual(summary.uncertain_count, 1)
        self.assertEqual(summary.avg_total_latency_ms, 15.0)
        self.assertEqual(summary.p99_total_latency_ms, 20.0)

    def test_summary_aggregates_the_audited_window_not_the_send_window(self):
        budgeter = RiskControlLatencyBudgeter(
            5000.0, default_end_state=LatencyEndState.ACKNOWLEDGEMENT
        )
        budgeter.record_trace("cancel", 0, 1, 2, 5, t_ack_ms=100)
        budgeter.record_trace("cancel", 0, 1, 2, 5, t_ack_ms=300)
        summary = budgeter.summarize_audit("cancel")
        self.assertEqual(summary.avg_total_latency_ms, 200.0)
        self.assertEqual(summary.p99_total_latency_ms, 300.0)

    def test_p99_is_unresolvable_below_one_hundred_samples(self):
        # Nearest rank for P99 over 99 samples is 99, i.e. the maximum itself.
        for i in range(1, 100):
            self.budgeter.record_trace("kill", 0, 0, 0, i, sla_budget_ms=1000.0)
        summary = self.budgeter.summarize_audit("kill")
        self.assertEqual(summary.measured_traces, 99)
        self.assertFalse(summary.p99_resolvable)
        self.assertEqual(summary.p99_total_latency_ms, 99.0)

    def test_p99_resolves_at_one_hundred_samples_with_hand_derived_values(self):
        # Samples 1..100 ms: mean 50.5, nearest-rank P99 is the 99th smallest, i.e. 99.
        for i in range(1, 101):
            self.budgeter.record_trace("kill", 0, 0, 0, i, sla_budget_ms=1000.0)
        summary = self.budgeter.summarize_audit("kill")
        self.assertEqual(summary.measured_traces, 100)
        self.assertTrue(summary.p99_resolvable)
        self.assertEqual(summary.p99_total_latency_ms, 99.0)
        self.assertEqual(summary.avg_total_latency_ms, 50.5)
        self.assertTrue(summary.is_risk_pipeline_healthy)

    def test_all_uncertain_reports_no_distribution(self):
        self.budgeter.record_trace("kill", 0, 1, 2, 3, clock_synchronized=False)
        summary = self.budgeter.summarize_audit()
        self.assertEqual(summary.measured_traces, 0)
        self.assertEqual(summary.avg_total_latency_ms, 0.0)
        self.assertEqual(summary.p99_total_latency_ms, 0.0)
        self.assertFalse(summary.p99_resolvable)
        self.assertFalse(summary.is_risk_pipeline_healthy)


class TestBoundedStorage(unittest.TestCase):
    def test_oldest_traces_are_evicted(self):
        budgeter = RiskControlLatencyBudgeter(50.0, max_traces=2)
        for i in range(3):
            budgeter.record_trace("x", i, i + 1, i + 2, i + 3)
        self.assertEqual(budgeter.summarize_audit().total_traces, 2)

    def test_concurrent_recording_loses_no_traces(self):
        budgeter = RiskControlLatencyBudgeter(50.0, max_traces=1000)

        def worker():
            for _ in range(100):
                budgeter.record_trace("kill", 0, 1, 2, 3)

        threads = [threading.Thread(target=worker) for _ in range(8)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()
        self.assertEqual(budgeter.summarize_audit().total_traces, 800)


class TestCriticalPathLogging(unittest.TestCase):
    """Passing traces must not flood the channel the breach alerts arrive on."""

    def setUp(self):
        self.budgeter = RiskControlLatencyBudgeter(50.0)
        self.handler = _CaptureHandler()
        self.previous_level = logger.level
        logger.setLevel(logging.DEBUG)
        logger.addHandler(self.handler)
        self.addCleanup(logger.setLevel, self.previous_level)
        self.addCleanup(logger.removeHandler, self.handler)

    def test_passing_trace_does_not_warn(self):
        self.budgeter.record_trace("kill", 0, 1, 2, 3)
        self.assertEqual([r.levelno for r in self.handler.records], [logging.DEBUG])

    def test_breach_and_uncertain_traces_warn(self):
        self.budgeter.record_trace("kill", 0, 1, 2, 500)
        self.budgeter.record_trace("kill", 0, 1, 2, 3, clock_synchronized=False)
        self.assertEqual(
            [r.levelno for r in self.handler.records], [logging.WARNING, logging.WARNING]
        )


if __name__ == "__main__":
    unittest.main()
