import logging
import unittest

from cross_strategy_shared_infrastructure_resource_contention import (
    RESOURCE_CPU,
    RESOURCE_FIX_GATEWAY,
    STATE_CRITICAL,
    STATE_ELEVATED,
    STATE_NORMAL,
    STATUS_PAUSED,
    STATUS_RUNNING,
    STATUS_THROTTLED,
    SharedInfrastructureContentionManager,
    StrategyProcessInfo,
)


def _quiet(func):
    """Silence the module's CRITICAL/WARNING chatter for a single call."""
    logging.disable(logging.CRITICAL)
    try:
        return func()
    finally:
        logging.disable(logging.NOTSET)


class TestSharedInfrastructureContentionManager(unittest.TestCase):

    def setUp(self):
        self.manager = SharedInfrastructureContentionManager(
            max_fix_gateway_rate_sec=1000.0,
            elevated_threshold_pct=75.0,
            critical_threshold_pct=85.0,
            resume_clear_samples=3,
        )

        self.p_hft = StrategyProcessInfo(
            "HFT_MM", "HIGH_HFT", pinned_cpu_core=1,
            current_cpu_utilization_pct=25.0, current_msg_rate_per_sec=200.0,
            status="RUNNING",
        )
        self.p_arb = StrategyProcessInfo(
            "StatArb", "MEDIUM_ARB", pinned_cpu_core=2,
            current_cpu_utilization_pct=30.0, current_msg_rate_per_sec=150.0,
            status="RUNNING", baseline_msg_rate_per_sec=300.0,
        )
        self.p_batch = StrategyProcessInfo(
            "EOD_Report", "LOW_BATCH", pinned_cpu_core=3,
            current_cpu_utilization_pct=40.0, current_msg_rate_per_sec=10.0,
            status="RUNNING",
        )

        self.manager.register_process(self.p_hft)
        self.manager.register_process(self.p_arb)
        self.manager.register_process(self.p_batch)

    def _evaluate(self, cpu=10.0, ram=10.0, fix=100.0):
        return _quiet(
            lambda: self.manager.evaluate_contention_state(
                overall_cpu_pct=cpu, overall_ram_pct=ram, current_fix_msg_rate_sec=fix
            )
        )

    def _drive_to_critical(self):
        return self._evaluate(cpu=92.0, ram=60.0, fix=400.0)

    # ------------------------------------------------------------------ #
    # State classification
    # ------------------------------------------------------------------ #
    def test_normal_utilization(self):
        report = self._evaluate(cpu=45.0, ram=50.0, fix=360.0)

        self.assertEqual(report.contention_state, STATE_NORMAL)
        self.assertEqual(report.paused_strategies, [])
        self.assertEqual(report.throttled_strategies, [])
        self.assertEqual(self.p_batch.status, STATUS_RUNNING)
        # max(45, 50, 36) = 50, driven by RAM.
        self.assertAlmostEqual(report.max_utilization_pct, 50.0)
        self.assertAlmostEqual(report.fix_gateway_utilization_pct, 36.0)

    def test_critical_contention_preempts_low_priority(self):
        report = self._drive_to_critical()

        self.assertEqual(report.contention_state, STATE_CRITICAL)
        self.assertIn("EOD_Report", report.paused_strategies)
        self.assertIn("StatArb", report.throttled_strategies)

        self.assertEqual(self.p_batch.status, STATUS_PAUSED)
        self.assertEqual(self.p_arb.status, STATUS_THROTTLED)
        self.assertEqual(self.p_hft.status, STATUS_RUNNING)  # Protected.

        # The escalation must be auditable: CPU at 92% was the binding resource.
        self.assertEqual(report.binding_resource, RESOURCE_CPU)
        self.assertAlmostEqual(report.max_utilization_pct, 92.0)

    def test_exactly_at_critical_threshold_escalates(self):
        report = self._evaluate(cpu=85.0, ram=10.0, fix=0.0)
        self.assertEqual(report.contention_state, STATE_CRITICAL)

    def test_exactly_at_elevated_threshold_is_elevated_not_critical(self):
        report = self._evaluate(cpu=75.0, ram=10.0, fix=0.0)
        self.assertEqual(report.contention_state, STATE_ELEVATED)
        self.assertEqual(report.paused_strategies, [])

    def test_just_below_elevated_threshold_is_normal(self):
        report = self._evaluate(cpu=74.99, ram=10.0, fix=0.0)
        self.assertEqual(report.contention_state, STATE_NORMAL)

    def test_fix_gateway_saturation_escalates_while_host_is_idle(self):
        """A saturated shared gateway must escalate even on an idle box.

        950 msgs/sec against a 1000 msgs/sec negotiated limit is 95% of the
        session's budget - breaching it gets the session rejected or terminated
        for every strategy on it, regardless of how quiet the CPU is.
        """
        report = self._evaluate(cpu=5.0, ram=5.0, fix=950.0)

        self.assertEqual(report.contention_state, STATE_CRITICAL)
        self.assertEqual(report.binding_resource, RESOURCE_FIX_GATEWAY)
        self.assertAlmostEqual(report.fix_gateway_utilization_pct, 95.0)
        self.assertAlmostEqual(report.max_utilization_pct, 95.0)
        self.assertEqual(self.p_batch.status, STATUS_PAUSED)

    def test_fix_rate_above_negotiated_limit_reports_over_100_pct(self):
        report = self._evaluate(cpu=5.0, ram=5.0, fix=1250.0)
        self.assertAlmostEqual(report.fix_gateway_utilization_pct, 125.0)
        self.assertEqual(report.contention_state, STATE_CRITICAL)

    # ------------------------------------------------------------------ #
    # Throttle quantification
    # ------------------------------------------------------------------ #
    def test_throttle_cap_is_half_of_declared_baseline(self):
        report = self._drive_to_critical()
        # 300 msgs/sec baseline * 0.5 = 150 msgs/sec, independent of the 150
        # msgs/sec currently observed.
        self.assertAlmostEqual(report.throttle_caps_msg_per_sec["StatArb"], 150.0)

    def test_throttle_cap_falls_back_to_observed_rate_without_baseline(self):
        self.p_arb.baseline_msg_rate_per_sec = None
        report = self._drive_to_critical()
        self.assertAlmostEqual(report.throttle_caps_msg_per_sec["StatArb"], 75.0)
        self.assertTrue(
            any("observed" in d for d in report.mitigation_directives),
            "directive must disclose that the cap is based on an observed rate",
        )

    # ------------------------------------------------------------------ #
    # Hysteresis / resume behaviour
    # ------------------------------------------------------------------ #
    def test_elevated_sample_after_critical_does_not_resume_batch(self):
        """Regression: a single sub-critical sample used to resume everything.

        Resuming a paused batch job at 80% utilisation immediately re-saturates
        the host that the pause was protecting, producing pause/resume flapping.
        """
        self._drive_to_critical()
        self.assertEqual(self.p_batch.status, STATUS_PAUSED)

        report = self._evaluate(cpu=80.0, ram=10.0, fix=0.0)

        self.assertEqual(report.contention_state, STATE_ELEVATED)
        self.assertEqual(self.p_batch.status, STATUS_PAUSED)
        self.assertEqual(self.p_arb.status, STATUS_THROTTLED)
        self.assertIn("EOD_Report", report.paused_strategies)
        self.assertEqual(report.resumed_strategies, [])

    def test_resume_requires_consecutive_clear_samples(self):
        self._drive_to_critical()

        for sample in (1, 2):
            report = self._evaluate(cpu=10.0, ram=10.0, fix=0.0)
            self.assertEqual(report.contention_state, STATE_NORMAL)
            self.assertEqual(
                self.p_batch.status, STATUS_PAUSED,
                f"resumed after only {sample} clear sample(s)",
            )
            self.assertEqual(report.resumed_strategies, [])

        report = self._evaluate(cpu=10.0, ram=10.0, fix=0.0)
        self.assertEqual(self.p_batch.status, STATUS_RUNNING)
        self.assertEqual(self.p_arb.status, STATUS_RUNNING)
        self.assertCountEqual(report.resumed_strategies, ["StatArb", "EOD_Report"])
        self.assertEqual(report.paused_strategies, [])

    def test_clear_streak_resets_on_a_contended_sample(self):
        self._drive_to_critical()
        self._evaluate(cpu=10.0, ram=10.0, fix=0.0)   # clear 1
        self._evaluate(cpu=10.0, ram=10.0, fix=0.0)   # clear 2
        self._evaluate(cpu=88.0, ram=10.0, fix=0.0)   # contended -> streak reset
        self._evaluate(cpu=10.0, ram=10.0, fix=0.0)   # clear 1 again
        self._evaluate(cpu=10.0, ram=10.0, fix=0.0)   # clear 2 again

        self.assertEqual(self.p_batch.status, STATUS_PAUSED)

        self._evaluate(cpu=10.0, ram=10.0, fix=0.0)   # clear 3
        self.assertEqual(self.p_batch.status, STATUS_RUNNING)

    def test_resume_clear_samples_of_one_resumes_immediately(self):
        manager = SharedInfrastructureContentionManager(resume_clear_samples=1)
        batch = StrategyProcessInfo(
            "Batch", "LOW_BATCH", pinned_cpu_core=3,
            current_cpu_utilization_pct=40.0, current_msg_rate_per_sec=1.0,
            status="RUNNING",
        )
        manager.register_process(batch)

        _quiet(lambda: manager.evaluate_contention_state(95.0, 10.0, 0.0))
        self.assertEqual(batch.status, STATUS_PAUSED)

        report = _quiet(lambda: manager.evaluate_contention_state(10.0, 10.0, 0.0))
        self.assertEqual(batch.status, STATUS_RUNNING)
        self.assertEqual(report.resumed_strategies, ["Batch"])

    # ------------------------------------------------------------------ #
    # Telemetry validation - the control must not fail open
    # ------------------------------------------------------------------ #
    def test_nan_cpu_reading_is_rejected_not_treated_as_normal(self):
        """Regression: every comparison against NaN is False.

        An unvalidated NaN CPU reading classified a saturated host as NORMAL and
        silently skipped preemption - the worst possible failure mode for a
        protective control.
        """
        with self.assertRaises(ValueError):
            self.manager.evaluate_contention_state(
                overall_cpu_pct=float("nan"), overall_ram_pct=10.0,
                current_fix_msg_rate_sec=10.0,
            )
        self.assertEqual(self.p_batch.status, STATUS_RUNNING)

    def test_infinite_ram_reading_is_rejected(self):
        with self.assertRaises(ValueError):
            self.manager.evaluate_contention_state(10.0, float("inf"), 10.0)

    def test_unnormalised_multicore_cpu_reading_is_rejected(self):
        # `top` style 400% on a 4-core host would otherwise pin the manager to
        # CRITICAL permanently.
        with self.assertRaises(ValueError) as ctx:
            self.manager.evaluate_contention_state(400.0, 10.0, 10.0)
        self.assertIn("normalised", str(ctx.exception))

    def test_negative_readings_are_rejected(self):
        with self.assertRaises(ValueError):
            self.manager.evaluate_contention_state(-1.0, 10.0, 10.0)
        with self.assertRaises(ValueError):
            self.manager.evaluate_contention_state(10.0, 10.0, -5.0)

    def test_non_numeric_reading_is_rejected(self):
        with self.assertRaises(TypeError):
            self.manager.evaluate_contention_state("90", 10.0, 10.0)

    # ------------------------------------------------------------------ #
    # Configuration validation
    # ------------------------------------------------------------------ #
    def test_zero_gateway_rate_limit_rejected_at_construction(self):
        # Previously surfaced as a ZeroDivisionError inside the telemetry loop.
        with self.assertRaises(ValueError):
            SharedInfrastructureContentionManager(max_fix_gateway_rate_sec=0.0)

    def test_critical_threshold_below_elevated_rejected(self):
        with self.assertRaises(ValueError):
            SharedInfrastructureContentionManager(
                elevated_threshold_pct=90.0, critical_threshold_pct=85.0
            )

    def test_resume_threshold_above_elevated_rejected(self):
        with self.assertRaises(ValueError):
            SharedInfrastructureContentionManager(
                elevated_threshold_pct=75.0, critical_threshold_pct=85.0,
                resume_threshold_pct=80.0,
            )

    def test_invalid_resume_clear_samples_rejected(self):
        with self.assertRaises(ValueError):
            SharedInfrastructureContentionManager(resume_clear_samples=0)

    def test_throttle_factor_above_one_rejected(self):
        with self.assertRaises(ValueError):
            SharedInfrastructureContentionManager(medium_priority_throttle_factor=1.5)

    # ------------------------------------------------------------------ #
    # Registration validation
    # ------------------------------------------------------------------ #
    def test_unknown_priority_class_rejected_at_registration(self):
        """A typo'd class used to fall through every preemption branch."""
        with self.assertRaises(ValueError):
            self.manager.register_process(
                StrategyProcessInfo(
                    "Typo", "HIGH-HFT", pinned_cpu_core=4,
                    current_cpu_utilization_pct=10.0, current_msg_rate_per_sec=1.0,
                    status="RUNNING",
                )
            )
        self.assertNotIn("Typo", self.manager.processes)

    def test_priority_corrupted_after_registration_fails_closed(self):
        self.p_batch.priority_level = "low_batch"  # wrong case, post-registration
        report = self._drive_to_critical()

        self.assertIn("EOD_Report", report.paused_strategies)
        self.assertEqual(self.p_batch.status, STATUS_PAUSED)

    def test_empty_strategy_id_rejected(self):
        with self.assertRaises(ValueError):
            self.manager.register_process(
                StrategyProcessInfo(
                    "", "LOW_BATCH", pinned_cpu_core=4,
                    current_cpu_utilization_pct=10.0, current_msg_rate_per_sec=1.0,
                    status="RUNNING",
                )
            )

    def test_duplicate_registration_warns(self):
        replacement = StrategyProcessInfo(
            "EOD_Report", "HIGH_HFT", pinned_cpu_core=9,
            current_cpu_utilization_pct=10.0, current_msg_rate_per_sec=1.0,
            status="RUNNING",
        )
        with self.assertLogs(
            "cross_strategy_shared_infrastructure_resource_contention", level="WARNING"
        ) as captured:
            self.manager.register_process(replacement)
        self.assertTrue(any("Re-registering" in line for line in captured.output))
        self.assertEqual(len(self.manager.processes), 3)


if __name__ == "__main__":
    unittest.main()
