import dataclasses
import threading
import unittest

from graceful_degradation_router import (
    DEFAULT_SHEDDING_POLICY,
    GracefulDegradationRouterEngine,
    InvalidHealthMetricError,
    LoadSheddingConfigurationError,
    SystemHealthMetrics,
    SystemMode,
    TaskDisposition,
    TaskPriority,
    TradingTask,
    UnknownTaskPriorityError,
)


def health(cpu=40.0, loss=0.1, db=10.0, age=None):
    return SystemHealthMetrics(
        cpu_utilization_pct=cpu,
        network_packet_loss_pct=loss,
        db_connection_latency_ms=db,
        sample_age_seconds=age,
    )


class BaseRouterTest(unittest.TestCase):
    def setUp(self):
        self.engine = GracefulDegradationRouterEngine(
            partial_degradation_cpu_pct=75.0,
            partial_degradation_packet_loss_pct=1.0,
            critical_outage_packet_loss_pct=10.0,
        )
        self.tasks = [
            TradingTask("T1", "MASS_CANCEL", "P1_CRITICAL"),
            TradingTask("T2", "STOP_LOSS_EXIT", "P2_HIGH"),
            TradingTask("T3", "NEW_SIGNAL_ENTRY", "P3_MEDIUM"),
            TradingTask("T4", "ANALYTICS_TICK_LOG", "P4_LOW"),
        ]


class TestModeClassification(BaseRouterTest):
    def test_normal_healthy_when_all_metrics_below_thresholds(self):
        self.assertEqual(
            self.engine.determine_system_mode(health(cpu=40.0, loss=0.1)),
            SystemMode.NORMAL_HEALTHY,
        )

    def test_cpu_exactly_on_partial_threshold_degrades(self):
        # Thresholds are inclusive: a sample sitting exactly on 75.0% degrades.
        self.assertEqual(
            self.engine.determine_system_mode(health(cpu=75.0, loss=0.0)),
            SystemMode.PARTIAL_DEGRADATION,
        )

    def test_cpu_just_below_partial_threshold_stays_healthy(self):
        self.assertEqual(
            self.engine.determine_system_mode(health(cpu=74.999, loss=0.0)),
            SystemMode.NORMAL_HEALTHY,
        )

    def test_packet_loss_exactly_on_partial_threshold_degrades(self):
        self.assertEqual(
            self.engine.determine_system_mode(health(cpu=10.0, loss=1.0)),
            SystemMode.PARTIAL_DEGRADATION,
        )

    def test_packet_loss_exactly_on_critical_threshold_is_critical(self):
        self.assertEqual(
            self.engine.determine_system_mode(health(cpu=10.0, loss=10.0)),
            SystemMode.CRITICAL_OUTAGE,
        )

    def test_cpu_exactly_on_critical_threshold_is_critical(self):
        self.assertEqual(
            self.engine.determine_system_mode(health(cpu=90.0, loss=0.0)),
            SystemMode.CRITICAL_OUTAGE,
        )

    def test_worst_metric_wins(self):
        # CPU healthy, packet loss critical -> critical.
        self.assertEqual(
            self.engine.determine_system_mode(health(cpu=5.0, loss=99.0)),
            SystemMode.CRITICAL_OUTAGE,
        )

    def test_determine_system_mode_does_not_mutate_engine_state(self):
        self.engine.determine_system_mode(health(cpu=95.0, loss=50.0))
        self.assertEqual(self.engine.current_mode, SystemMode.NORMAL_HEALTHY)

    def test_classification_reasons_name_the_breached_metric(self):
        report = self.engine.process_and_filter_tasks(health(cpu=85.0, loss=0.5), self.tasks)
        self.assertTrue(any("CPU" in r for r in report.classification_reasons))
        self.assertFalse(any("packet loss" in r for r in report.classification_reasons))


class TestHealthMetricValidation(BaseRouterTest):
    def test_nan_cpu_is_rejected_not_read_as_healthy(self):
        # Regression: NaN compares False against every threshold, so an
        # unguarded NaN silently disabled load shedding entirely.
        with self.assertRaises(InvalidHealthMetricError):
            health(cpu=float("nan"))

    def test_infinite_packet_loss_is_rejected(self):
        with self.assertRaises(InvalidHealthMetricError):
            health(loss=float("inf"))

    def test_negative_and_out_of_range_percentages_are_rejected(self):
        with self.assertRaises(InvalidHealthMetricError):
            health(cpu=-1.0)
        with self.assertRaises(InvalidHealthMetricError):
            health(loss=100.001)

    def test_negative_latency_and_age_are_rejected(self):
        with self.assertRaises(InvalidHealthMetricError):
            health(db=-5.0)
        with self.assertRaises(InvalidHealthMetricError):
            health(age=-0.1)

    def test_non_numeric_metric_is_rejected(self):
        with self.assertRaises(InvalidHealthMetricError):
            health(cpu="85")
        with self.assertRaises(InvalidHealthMetricError):
            health(cpu=True)

    def test_unreadable_cpu_fails_safe_to_critical_outage(self):
        report = self.engine.process_and_filter_tasks(health(cpu=None), self.tasks)
        self.assertEqual(report.system_mode, SystemMode.CRITICAL_OUTAGE)
        self.assertTrue(report.manual_intervention_required)

    def test_unreadable_packet_loss_fails_safe_to_critical_outage(self):
        self.assertEqual(
            self.engine.determine_system_mode(health(loss=None)),
            SystemMode.CRITICAL_OUTAGE,
        )

    def test_health_sample_is_immutable_after_validation(self):
        # A sampler thread must not be able to write a NaN into a sample that
        # has already passed validation.
        sample = health(cpu=40.0)
        with self.assertRaises(dataclasses.FrozenInstanceError):
            sample.cpu_utilization_pct = float("nan")
        self.assertEqual(sample.cpu_utilization_pct, 40.0)

    def test_health_object_type_is_enforced(self):
        with self.assertRaises(TypeError):
            self.engine.determine_system_mode({"cpu_utilization_pct": 10.0})


class TestTelemetryFreshness(unittest.TestCase):
    def setUp(self):
        self.engine = GracefulDegradationRouterEngine(max_health_sample_age_seconds=5.0)
        self.tasks = [TradingTask("T1", "MASS_CANCEL", "P1_CRITICAL")]

    def test_fresh_sample_is_trusted(self):
        report = self.engine.process_and_filter_tasks(health(age=1.0), self.tasks)
        self.assertEqual(report.system_mode, SystemMode.NORMAL_HEALTHY)
        self.assertTrue(report.telemetry_age_verified)

    def test_stale_sample_forces_capital_preservation(self):
        # A frozen monitoring agent keeps reporting healthy numbers forever.
        report = self.engine.process_and_filter_tasks(health(cpu=5.0, loss=0.0, age=5.0), self.tasks)
        self.assertEqual(report.system_mode, SystemMode.CRITICAL_OUTAGE)
        self.assertFalse(report.telemetry_age_verified)

    def test_unknown_age_with_limit_configured_fails_safe(self):
        report = self.engine.process_and_filter_tasks(health(age=None), self.tasks)
        self.assertEqual(report.system_mode, SystemMode.CRITICAL_OUTAGE)
        self.assertTrue(report.manual_intervention_required)

    def test_age_is_ignored_when_no_limit_configured(self):
        engine = GracefulDegradationRouterEngine()
        report = engine.process_and_filter_tasks(health(age=3600.0), self.tasks)
        self.assertEqual(report.system_mode, SystemMode.NORMAL_HEALTHY)
        self.assertFalse(report.telemetry_age_verified)


class TestDatabaseLatencyThresholds(unittest.TestCase):
    def setUp(self):
        self.tasks = [TradingTask("T1", "MASS_CANCEL", "P1_CRITICAL")]

    def test_db_latency_ignored_when_thresholds_unset(self):
        engine = GracefulDegradationRouterEngine()
        self.assertEqual(
            engine.determine_system_mode(health(db=30_000.0)), SystemMode.NORMAL_HEALTHY
        )

    def test_db_latency_trips_partial_degradation_when_configured(self):
        engine = GracefulDegradationRouterEngine(partial_degradation_db_latency_ms=250.0)
        self.assertEqual(
            engine.determine_system_mode(health(db=250.0)), SystemMode.PARTIAL_DEGRADATION
        )
        self.assertEqual(
            engine.determine_system_mode(health(db=249.0)), SystemMode.NORMAL_HEALTHY
        )

    def test_db_latency_trips_critical_outage_when_configured(self):
        engine = GracefulDegradationRouterEngine(
            partial_degradation_db_latency_ms=250.0,
            critical_outage_db_latency_ms=1000.0,
        )
        self.assertEqual(
            engine.determine_system_mode(health(db=1000.0)), SystemMode.CRITICAL_OUTAGE
        )

    def test_unreadable_db_latency_fails_safe_only_when_relied_upon(self):
        engine = GracefulDegradationRouterEngine(partial_degradation_db_latency_ms=250.0)
        self.assertEqual(
            engine.determine_system_mode(health(db=None)), SystemMode.CRITICAL_OUTAGE
        )
        self.assertEqual(
            GracefulDegradationRouterEngine().determine_system_mode(health(db=None)),
            SystemMode.NORMAL_HEALTHY,
        )


class TestPriorityParsing(unittest.TestCase):
    def test_unknown_priority_raises_instead_of_being_shed(self):
        # Regression: a mis-tagged mass-cancel used to fall through to the
        # "everything else" branch and be shed during a critical outage.
        with self.assertRaises(UnknownTaskPriorityError):
            TradingTask("T9", "MASS_CANCEL", "P1-CRITICAL")

    def test_priority_label_is_normalised(self):
        task = TradingTask("T9", "MASS_CANCEL", " p1_critical ")
        self.assertIs(task.priority, TaskPriority.P1_CRITICAL)

    def test_non_string_priority_raises(self):
        with self.assertRaises(UnknownTaskPriorityError):
            TradingTask("T9", "MASS_CANCEL", 1)

    def test_empty_task_id_raises(self):
        with self.assertRaises(ValueError):
            TradingTask("   ", "MASS_CANCEL", "P1_CRITICAL")

    def test_batch_with_untagged_task_is_rejected_whole(self):
        engine = GracefulDegradationRouterEngine()
        good = TradingTask("T1", "MASS_CANCEL", "P1_CRITICAL")
        rogue = TradingTask("T2", "STOP_LOSS_EXIT", "P2_HIGH")
        object.__setattr__(rogue, "priority", "URGENT")   # bypass __post_init__
        with self.assertRaises(UnknownTaskPriorityError):
            engine.process_and_filter_tasks(health(), [good, rogue])

    def test_non_task_object_in_batch_raises(self):
        engine = GracefulDegradationRouterEngine()
        with self.assertRaises(TypeError):
            engine.process_and_filter_tasks(health(), ["T1"])


class TestRoutingDispositions(BaseRouterTest):
    def test_normal_healthy_processes_all_priorities(self):
        report = self.engine.process_and_filter_tasks(health(cpu=40.0, loss=0.1), self.tasks)
        self.assertEqual(report.system_mode, SystemMode.NORMAL_HEALTHY)
        self.assertEqual(report.processed_tasks_count, 4)
        self.assertEqual(report.shed_tasks_count, 0)
        self.assertFalse(report.manual_intervention_required)

    def test_partial_degradation_drops_p4_and_defers_p3(self):
        report = self.engine.process_and_filter_tasks(health(cpu=85.0, loss=0.5), self.tasks)
        self.assertEqual(report.system_mode, SystemMode.PARTIAL_DEGRADATION)
        self.assertEqual(report.processed_task_ids, ["T1", "T2"])
        self.assertEqual(report.deferred_task_ids, ["T3"])
        self.assertEqual(report.dropped_task_ids, ["T4"])
        self.assertEqual(report.shed_tasks_count, 2)
        # Only P3/P4 were shed, so no human is needed.
        self.assertFalse(report.manual_intervention_required)

    def test_critical_outage_executes_p1_only(self):
        report = self.engine.process_and_filter_tasks(
            health(cpu=95.0, loss=15.0, db=500.0), self.tasks
        )
        self.assertEqual(report.system_mode, SystemMode.CRITICAL_OUTAGE)
        self.assertEqual(report.processed_task_ids, ["T1"])
        self.assertEqual(report.shed_tasks_count, 3)

    def test_critical_outage_defers_exits_rather_than_dropping_them(self):
        # An open position still has to be managed; a shed exit is not a
        # completed one (MiFID II RTS 6 Art. 14(2)(g)).
        report = self.engine.process_and_filter_tasks(health(loss=15.0), self.tasks)
        self.assertEqual(report.dispositions["T2"], TaskDisposition.DEFER)
        self.assertEqual(report.dispositions["T3"], TaskDisposition.DROP)
        self.assertEqual(report.dispositions["T4"], TaskDisposition.DROP)
        self.assertTrue(report.manual_intervention_required)

    def test_critical_outage_with_only_p1_work_needs_no_escalation(self):
        report = self.engine.process_and_filter_tasks(
            health(loss=15.0), [TradingTask("T1", "MASS_CANCEL", "P1_CRITICAL")]
        )
        self.assertEqual(report.processed_tasks_count, 1)
        self.assertFalse(report.manual_intervention_required)

    def test_processed_ids_are_emitted_in_strict_priority_order(self):
        shuffled = [self.tasks[3], self.tasks[2], self.tasks[1], self.tasks[0]]
        report = self.engine.process_and_filter_tasks(health(), shuffled)
        self.assertEqual(report.processed_task_ids, ["T1", "T2", "T3", "T4"])

    def test_input_order_is_preserved_within_a_priority_tier(self):
        batch = [
            TradingTask("C2", "MASS_CANCEL", "P1_CRITICAL"),
            TradingTask("C1", "RISK_CHECK", "P1_CRITICAL"),
        ]
        report = self.engine.process_and_filter_tasks(health(), batch)
        self.assertEqual(report.processed_task_ids, ["C2", "C1"])

    def test_empty_batch_is_handled(self):
        report = self.engine.process_and_filter_tasks(health(), [])
        self.assertEqual(report.total_tasks_received, 0)
        self.assertEqual(report.processed_tasks_count, 0)
        self.assertEqual(report.shed_tasks_count, 0)

    def test_duplicate_task_ids_are_warned_about(self):
        batch = [
            TradingTask("T1", "MASS_CANCEL", "P1_CRITICAL"),
            TradingTask("T1", "MASS_CANCEL", "P1_CRITICAL"),
        ]
        with self.assertLogs("graceful_degradation_router", level="WARNING") as captured:
            self.engine.process_and_filter_tasks(health(), batch)
        self.assertTrue(any("Duplicate task_id" in line for line in captured.output))

    def test_shed_ids_list_deferred_before_dropped(self):
        report = self.engine.process_and_filter_tasks(health(cpu=85.0), self.tasks)
        self.assertEqual(report.shed_task_ids, ["T3", "T4"])

    def test_counts_reconcile_with_the_batch(self):
        report = self.engine.process_and_filter_tasks(health(loss=15.0), self.tasks)
        self.assertEqual(
            report.total_tasks_received,
            report.processed_tasks_count + report.deferred_tasks_count + report.dropped_tasks_count,
        )
        self.assertEqual(
            report.shed_tasks_count,
            report.deferred_tasks_count + report.dropped_tasks_count,
        )


class TestRecoveryDamping(BaseRouterTest):
    def test_escalation_is_immediate(self):
        report = self.engine.process_and_filter_tasks(health(loss=15.0), self.tasks)
        self.assertEqual(report.system_mode, SystemMode.CRITICAL_OUTAGE)
        self.assertTrue(report.mode_changed)
        self.assertEqual(report.previous_mode, SystemMode.NORMAL_HEALTHY)

    def test_recovery_requires_confirmation_and_steps_down_one_level(self):
        self.engine.process_and_filter_tasks(health(loss=15.0), self.tasks)
        healthy = health(cpu=5.0, loss=0.0)

        for _ in range(2):
            report = self.engine.process_and_filter_tasks(healthy, self.tasks)
            self.assertEqual(report.system_mode, SystemMode.CRITICAL_OUTAGE)
            self.assertEqual(report.instantaneous_mode, SystemMode.NORMAL_HEALTHY)

        report = self.engine.process_and_filter_tasks(healthy, self.tasks)
        self.assertEqual(report.system_mode, SystemMode.PARTIAL_DEGRADATION)

        for _ in range(2):
            self.assertEqual(
                self.engine.process_and_filter_tasks(healthy, self.tasks).system_mode,
                SystemMode.PARTIAL_DEGRADATION,
            )
        self.assertEqual(
            self.engine.process_and_filter_tasks(healthy, self.tasks).system_mode,
            SystemMode.NORMAL_HEALTHY,
        )

    def test_a_single_bad_sample_resets_the_recovery_streak(self):
        self.engine.process_and_filter_tasks(health(loss=15.0), self.tasks)
        healthy = health(cpu=5.0, loss=0.0)
        self.engine.process_and_filter_tasks(healthy, self.tasks)
        self.engine.process_and_filter_tasks(healthy, self.tasks)
        # Flap back to critical, then two healthy samples must not be enough.
        self.engine.process_and_filter_tasks(health(loss=15.0), self.tasks)
        self.engine.process_and_filter_tasks(healthy, self.tasks)
        report = self.engine.process_and_filter_tasks(healthy, self.tasks)
        self.assertEqual(report.system_mode, SystemMode.CRITICAL_OUTAGE)

    def test_recovery_never_overshoots_the_instantaneous_classification(self):
        self.engine.process_and_filter_tasks(health(loss=15.0), self.tasks)
        degraded = health(cpu=80.0, loss=0.0)
        for _ in range(3):
            report = self.engine.process_and_filter_tasks(degraded, self.tasks)
        self.assertEqual(report.system_mode, SystemMode.PARTIAL_DEGRADATION)
        for _ in range(5):
            report = self.engine.process_and_filter_tasks(degraded, self.tasks)
        self.assertEqual(report.system_mode, SystemMode.PARTIAL_DEGRADATION)

    def test_single_sample_confirmation_recovers_one_step_per_sample(self):
        engine = GracefulDegradationRouterEngine(recovery_confirmation_samples=1)
        engine.process_and_filter_tasks(health(loss=15.0), self.tasks)
        healthy = health(cpu=5.0, loss=0.0)
        self.assertEqual(
            engine.process_and_filter_tasks(healthy, self.tasks).system_mode,
            SystemMode.PARTIAL_DEGRADATION,
        )
        self.assertEqual(
            engine.process_and_filter_tasks(healthy, self.tasks).system_mode,
            SystemMode.NORMAL_HEALTHY,
        )

    def test_reset_mode_state_clears_the_latch(self):
        self.engine.process_and_filter_tasks(health(loss=15.0), self.tasks)
        self.engine.reset_mode_state()
        self.assertEqual(self.engine.current_mode, SystemMode.NORMAL_HEALTHY)
        report = self.engine.process_and_filter_tasks(health(cpu=5.0, loss=0.0), self.tasks)
        self.assertEqual(report.system_mode, SystemMode.NORMAL_HEALTHY)

    def test_mode_transition_is_logged(self):
        with self.assertLogs("graceful_degradation_router", level="WARNING") as captured:
            self.engine.process_and_filter_tasks(health(loss=15.0), self.tasks)
        self.assertTrue(any("MODE TRANSITION" in line for line in captured.output))


class TestPolicyValidation(unittest.TestCase):
    def test_default_policy_never_sheds_p1(self):
        for mode in SystemMode:
            self.assertEqual(
                DEFAULT_SHEDDING_POLICY[mode][TaskPriority.P1_CRITICAL],
                TaskDisposition.PROCESS,
            )

    def test_policy_that_sheds_p1_is_rejected(self):
        policy = {mode: dict(row) for mode, row in DEFAULT_SHEDDING_POLICY.items()}
        policy[SystemMode.CRITICAL_OUTAGE][TaskPriority.P1_CRITICAL] = TaskDisposition.DROP
        with self.assertRaises(LoadSheddingConfigurationError):
            GracefulDegradationRouterEngine(policy=policy)

    def test_non_monotone_policy_is_rejected(self):
        # Shedding P2 while still processing P3 inverts the hierarchy.
        policy = {mode: dict(row) for mode, row in DEFAULT_SHEDDING_POLICY.items()}
        policy[SystemMode.PARTIAL_DEGRADATION][TaskPriority.P2_HIGH] = TaskDisposition.DROP
        policy[SystemMode.PARTIAL_DEGRADATION][TaskPriority.P3_MEDIUM] = TaskDisposition.PROCESS
        with self.assertRaises(LoadSheddingConfigurationError):
            GracefulDegradationRouterEngine(policy=policy)

    def test_incomplete_policy_is_rejected(self):
        policy = {SystemMode.NORMAL_HEALTHY: dict(DEFAULT_SHEDDING_POLICY[SystemMode.NORMAL_HEALTHY])}
        with self.assertRaises(LoadSheddingConfigurationError):
            GracefulDegradationRouterEngine(policy=policy)

        partial = {mode: dict(row) for mode, row in DEFAULT_SHEDDING_POLICY.items()}
        del partial[SystemMode.PARTIAL_DEGRADATION][TaskPriority.P4_LOW]
        with self.assertRaises(LoadSheddingConfigurationError):
            GracefulDegradationRouterEngine(policy=partial)

    def test_valid_custom_policy_is_applied(self):
        policy = {mode: dict(row) for mode, row in DEFAULT_SHEDDING_POLICY.items()}
        policy[SystemMode.PARTIAL_DEGRADATION][TaskPriority.P3_MEDIUM] = TaskDisposition.PROCESS
        engine = GracefulDegradationRouterEngine(policy=policy)
        report = engine.process_and_filter_tasks(
            health(cpu=85.0), [TradingTask("T3", "NEW_SIGNAL_ENTRY", "P3_MEDIUM")]
        )
        self.assertEqual(report.processed_task_ids, ["T3"])


class TestEngineConfiguration(unittest.TestCase):
    def test_inverted_thresholds_are_rejected(self):
        with self.assertRaises(LoadSheddingConfigurationError):
            GracefulDegradationRouterEngine(
                partial_degradation_packet_loss_pct=12.0,
                critical_outage_packet_loss_pct=10.0,
            )
        with self.assertRaises(LoadSheddingConfigurationError):
            GracefulDegradationRouterEngine(
                partial_degradation_db_latency_ms=900.0,
                critical_outage_db_latency_ms=100.0,
            )

    def test_out_of_range_thresholds_are_rejected(self):
        with self.assertRaises(LoadSheddingConfigurationError):
            GracefulDegradationRouterEngine(partial_degradation_cpu_pct=140.0)
        with self.assertRaises(LoadSheddingConfigurationError):
            GracefulDegradationRouterEngine(critical_outage_cpu_pct=float("nan"))
        with self.assertRaises(LoadSheddingConfigurationError):
            GracefulDegradationRouterEngine(max_health_sample_age_seconds=-1.0)

    def test_zero_recovery_confirmation_is_rejected(self):
        with self.assertRaises(LoadSheddingConfigurationError):
            GracefulDegradationRouterEngine(recovery_confirmation_samples=0)


class TestConcurrentUse(BaseRouterTest):
    def test_parallel_batches_produce_consistent_reports(self):
        results = []
        errors = []

        def worker():
            try:
                for _ in range(50):
                    results.append(
                        self.engine.process_and_filter_tasks(health(cpu=5.0, loss=0.0), self.tasks)
                    )
            except Exception as exc:                      # pragma: no cover - failure path
                errors.append(exc)

        threads = [threading.Thread(target=worker) for _ in range(8)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        self.assertEqual(errors, [])
        self.assertEqual(len(results), 400)
        self.assertTrue(all(r.processed_tasks_count == 4 for r in results))


if __name__ == "__main__":
    unittest.main()
