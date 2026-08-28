"""Unit tests for the runbook incident automation engine.

The regression tests here are written against the specific defects the 1.0.0
engine shipped with: a hard-coded ``SUCCESS`` for every step, a dry run that
reported ``RESOLVED``, an unmapped incident type that silently ran
``CANCEL_OPEN_ORDERS``, no idempotency on ``incident_id``, and a halt-on-failure
rule that would have skipped the kill switch. Each of those has a test that
fails against the old behaviour and passes against this one.
"""
import logging
import threading
import unittest

from runbook_incident_automator import (
    DEFAULT_STEP_TIMEOUT_SECONDS,
    IncidentAlert,
    IncidentStatus,
    IncidentType,
    PlaybookStep,
    RemediationAction,
    RunbookConfigurationError,
    RunbookIncidentAutomationEngine,
    RunbookInputError,
    StepStatus,
)

# The engine logs escalations at ERROR by design; keep the test output readable.
logging.getLogger("runbook_incident_automator").addHandler(logging.NullHandler())
logging.getLogger("runbook_incident_automator").propagate = False


def make_alert(
    incident_id="INC_1",
    incident_type=IncidentType.DRAWDOWN_BREACH,
    severity="CRITICAL",
    source_service="RISK_ENGINE",
    metric_value=-150000.0,
    threshold_value=-100000.0,
    timestamp_iso="2026-08-05T10:00:00Z",
):
    return IncidentAlert(
        incident_id=incident_id,
        incident_type=incident_type,
        severity=severity,
        source_service=source_service,
        metric_value=metric_value,
        threshold_value=threshold_value,
        timestamp_iso=timestamp_iso,
    )


class RecordingHandler:
    """A handler that records its calls and can be told how to behave."""

    def __init__(self, outcome=True, raises=None):
        self.calls = []
        self.outcome = outcome
        self.raises = raises

    def __call__(self, alert):
        self.calls.append(alert)
        if self.raises is not None:
            raise self.raises
        return self.outcome

    @property
    def call_count(self):
        return len(self.calls)


def wire(engine, *actions, outcome=True):
    """Register a RecordingHandler for each action; return them by action."""
    handlers = {}
    for action in actions:
        handler = RecordingHandler(outcome=outcome)
        engine.register_handler(action, handler)
        handlers[action] = handler
    return handlers


ALL_ACTIONS = tuple(RemediationAction)


class TestIncidentAlertValidation(unittest.TestCase):
    def test_rejects_empty_incident_id(self):
        with self.assertRaises(RunbookInputError):
            make_alert(incident_id="   ")

    def test_strips_incident_id(self):
        self.assertEqual(make_alert(incident_id="  INC_7 ").incident_id, "INC_7")

    def test_accepts_incident_type_as_string(self):
        alert = make_alert(incident_type="drawdown_breach")
        self.assertIs(alert.incident_type, IncidentType.DRAWDOWN_BREACH)

    def test_rejects_unknown_incident_type_string(self):
        """An unclassifiable alert must not reach playbook selection at all."""
        with self.assertRaises(RunbookInputError) as ctx:
            make_alert(incident_type="DISK_FULL")
        self.assertIn("DISK_FULL", str(ctx.exception))

    def test_rejects_non_string_non_enum_incident_type(self):
        with self.assertRaises(RunbookInputError):
            make_alert(incident_type=7)

    def test_rejects_nan_metric(self):
        with self.assertRaises(RunbookInputError):
            make_alert(metric_value=float("nan"))

    def test_rejects_infinite_threshold(self):
        with self.assertRaises(RunbookInputError):
            make_alert(threshold_value=float("inf"))

    def test_coerces_numeric_strings_from_json_payloads(self):
        alert = make_alert(metric_value="-150000.0", threshold_value="-100000.0")
        self.assertEqual(alert.metric_value, -150000.0)
        self.assertEqual(alert.threshold_value, -100000.0)

    def test_rejects_naive_timestamp(self):
        with self.assertRaises(RunbookInputError):
            make_alert(timestamp_iso="2026-08-05T10:00:00")

    def test_rejects_unparseable_timestamp(self):
        with self.assertRaises(RunbookInputError):
            make_alert(timestamp_iso="last Tuesday")

    def test_normalises_offset_timestamp_to_utc(self):
        """05:30 at +05:30 is midnight UTC -- derived independently of the code."""
        alert = make_alert(timestamp_iso="2026-08-05T05:30:00+05:30")
        self.assertEqual(alert.timestamp_iso, "2026-08-05T00:00:00Z")

    def test_accepts_lowercase_z_suffix(self):
        self.assertEqual(
            make_alert(timestamp_iso="2026-08-05T10:00:00z").timestamp_iso,
            "2026-08-05T10:00:00Z",
        )

    def test_unknown_severity_is_raised_to_critical_and_flagged(self):
        alert = make_alert(severity="P1")
        self.assertEqual(alert.severity, "CRITICAL")
        self.assertTrue(alert.severity_was_coerced)

    def test_known_severity_is_normalised_without_flagging(self):
        alert = make_alert(severity=" high ")
        self.assertEqual(alert.severity, "HIGH")
        self.assertFalse(alert.severity_was_coerced)

    def test_rejects_empty_source_service(self):
        with self.assertRaises(RunbookInputError):
            make_alert(source_service="")


class TestHandlerWiring(unittest.TestCase):
    def setUp(self):
        self.engine = RunbookIncidentAutomationEngine()

    def test_no_handler_is_never_reported_as_success(self):
        """Regression: 1.0.0 hard-coded SUCCESS and reported RESOLVED."""
        report = self.engine.execute_runbook(make_alert())
        self.assertEqual(report.status, IncidentStatus.ESCALATED)
        self.assertTrue(report.requires_human_escalation)
        for step in report.steps_executed:
            self.assertEqual(step.status, StepStatus.NO_HANDLER_REGISTERED)

    def test_unhandled_actions_lists_every_unwired_reachable_action(self):
        self.assertEqual(set(self.engine.unhandled_actions()), set(ALL_ACTIONS))
        self.engine.register_handler(RemediationAction.TRIGGER_KILL_SWITCH, lambda a: True)
        self.assertNotIn(RemediationAction.TRIGGER_KILL_SWITCH, self.engine.unhandled_actions())

    def test_unhandled_actions_is_empty_when_fully_wired(self):
        wire(self.engine, *ALL_ACTIONS)
        self.assertEqual(self.engine.unhandled_actions(), ())

    def test_rejects_non_callable_handler(self):
        with self.assertRaises(RunbookConfigurationError):
            self.engine.register_handler(RemediationAction.FAILOVER_VENUE, "cancel_everything")

    def test_rejects_handler_for_non_action(self):
        with self.assertRaises(RunbookConfigurationError):
            self.engine.register_handler("FAILOVER_VENUE", lambda a: True)

    def test_handler_receives_the_alert(self):
        handlers = wire(self.engine, RemediationAction.THROTTLE_ORDER_RATE)
        alert = make_alert(incident_id="INC_A", incident_type=IncidentType.ORDER_THROTTLE)
        self.engine.execute_runbook(alert)
        self.assertEqual(handlers[RemediationAction.THROTTLE_ORDER_RATE].call_count, 1)
        self.assertEqual(
            handlers[RemediationAction.THROTTLE_ORDER_RATE].calls[0].incident_id, "INC_A"
        )


class TestPlaybookExecution(unittest.TestCase):
    def setUp(self):
        self.engine = RunbookIncidentAutomationEngine()
        self.handlers = wire(self.engine, *ALL_ACTIONS)

    def actions_of(self, report):
        return [step.action for step in report.steps_executed]

    def statuses_of(self, report):
        return [step.status for step in report.steps_executed]

    def test_drawdown_breach_cancels_then_kills(self):
        report = self.engine.execute_runbook(make_alert())
        self.assertEqual(report.status, IncidentStatus.RESOLVED)
        self.assertEqual(
            self.actions_of(report),
            [RemediationAction.CANCEL_OPEN_ORDERS, RemediationAction.TRIGGER_KILL_SWITCH],
        )
        self.assertFalse(report.requires_human_escalation)

    def test_kill_switch_still_fires_when_the_cancel_fails(self):
        """Regression: 'halt on failure' would have skipped the kill switch."""
        self.engine.register_handler(
            RemediationAction.CANCEL_OPEN_ORDERS, RecordingHandler(outcome=False)
        )
        report = self.engine.execute_runbook(make_alert())
        self.assertEqual(self.statuses_of(report), [StepStatus.FAILED, StepStatus.SUCCESS])
        self.assertEqual(self.handlers[RemediationAction.TRIGGER_KILL_SWITCH].call_count, 1)
        self.assertEqual(report.status, IncidentStatus.ESCALATED)
        self.assertTrue(report.requires_human_escalation)

    def test_kill_switch_still_fires_when_the_cancel_raises(self):
        self.engine.register_handler(
            RemediationAction.CANCEL_OPEN_ORDERS,
            RecordingHandler(raises=ConnectionResetError("broker socket closed")),
        )
        report = self.engine.execute_runbook(make_alert())
        self.assertEqual(self.statuses_of(report), [StepStatus.FAILED, StepStatus.SUCCESS])
        self.assertIn("ConnectionResetError", report.steps_executed[0].detail)
        self.assertEqual(self.handlers[RemediationAction.TRIGGER_KILL_SWITCH].call_count, 1)

    def test_broker_outage_fails_over_even_when_the_cancel_cannot_reach_the_broker(self):
        self.engine.register_handler(
            RemediationAction.CANCEL_OPEN_ORDERS,
            RecordingHandler(raises=TimeoutError("no route to broker")),
        )
        report = self.engine.execute_runbook(
            make_alert(incident_type=IncidentType.BROKER_API_OUTAGE, source_service="GATEWAY")
        )
        self.assertEqual(self.statuses_of(report), [StepStatus.FAILED, StepStatus.SUCCESS])
        self.assertEqual(self.handlers[RemediationAction.FAILOVER_VENUE].call_count, 1)

    def test_successful_reconnect_skips_the_venue_failover(self):
        report = self.engine.execute_runbook(
            make_alert(incident_type=IncidentType.FEED_DISCONNECT, source_service="MD_FEED")
        )
        self.assertEqual(
            self.statuses_of(report),
            [StepStatus.SUCCESS, StepStatus.SKIPPED_ALREADY_REMEDIATED],
        )
        self.assertEqual(self.handlers[RemediationAction.FAILOVER_VENUE].call_count, 0)
        self.assertEqual(report.status, IncidentStatus.RESOLVED)

    def test_failed_reconnect_triggers_the_venue_failover(self):
        self.engine.register_handler(
            RemediationAction.RECONNECT_SOCKET, RecordingHandler(outcome=False)
        )
        report = self.engine.execute_runbook(
            make_alert(incident_type=IncidentType.FEED_DISCONNECT, source_service="MD_FEED")
        )
        self.assertEqual(self.statuses_of(report), [StepStatus.FAILED, StepStatus.SUCCESS])
        self.assertEqual(self.handlers[RemediationAction.FAILOVER_VENUE].call_count, 1)

    def test_halt_on_failure_skips_the_remaining_steps(self):
        self.engine.register_playbook(
            IncidentType.ORDER_THROTTLE,
            [
                PlaybookStep(RemediationAction.THROTTLE_ORDER_RATE, halt_on_failure=True),
                PlaybookStep(RemediationAction.FAILOVER_VENUE),
            ],
        )
        self.engine.register_handler(
            RemediationAction.THROTTLE_ORDER_RATE, RecordingHandler(outcome=False)
        )
        report = self.engine.execute_runbook(
            make_alert(incident_type=IncidentType.ORDER_THROTTLE, source_service="SOR")
        )
        self.assertEqual(
            self.statuses_of(report), [StepStatus.FAILED, StepStatus.SKIPPED_AFTER_HALT]
        )
        self.assertEqual(self.handlers[RemediationAction.FAILOVER_VENUE].call_count, 0)

    def test_handler_returning_none_counts_as_success(self):
        self.engine.register_handler(RemediationAction.THROTTLE_ORDER_RATE, lambda alert: None)
        report = self.engine.execute_runbook(
            make_alert(incident_type=IncidentType.ORDER_THROTTLE, source_service="SOR")
        )
        self.assertEqual(self.statuses_of(report), [StepStatus.SUCCESS])
        self.assertEqual(report.status, IncidentStatus.RESOLVED)

    def test_steps_are_numbered_from_one_in_order(self):
        report = self.engine.execute_runbook(make_alert())
        self.assertEqual([s.step_number for s in report.steps_executed], [1, 2])

    def test_durations_are_recorded_and_non_negative(self):
        report = self.engine.execute_runbook(make_alert())
        self.assertGreaterEqual(report.total_time_taken_ms, 0.0)
        for step in report.steps_executed:
            self.assertGreaterEqual(step.duration_ms, 0.0)

    def test_report_records_the_alert_context_in_the_audit_note(self):
        report = self.engine.execute_runbook(make_alert(incident_id="INC_NOTE"))
        self.assertIn("INC_NOTE", report.audit_notes)
        self.assertIn("DRAWDOWN_BREACH", report.audit_notes)
        self.assertIn("RISK_ENGINE", report.audit_notes)
        self.assertTrue(report.executed_at_utc_iso.endswith("Z"))

    def test_coerced_severity_is_surfaced_without_faking_a_failed_remediation(self):
        report = self.engine.execute_runbook(make_alert(severity="P1"))
        self.assertTrue(report.severity_was_coerced)
        self.assertIn("severity=CRITICAL", report.audit_notes)
        # The remediation itself succeeded; saying otherwise would misreport it.
        self.assertEqual(report.status, IncidentStatus.RESOLVED)
        self.assertFalse(report.requires_human_escalation)

    def test_escalation_flag_always_matches_the_reason_list(self):
        for alert in (make_alert(incident_id="OK_1"), make_alert(incident_id="OK_2", severity="P1")):
            report = self.engine.execute_runbook(alert)
            self.assertEqual(report.requires_human_escalation, bool(report.escalation_reasons))


class TestMissingPlaybook(unittest.TestCase):
    def test_incident_type_without_a_playbook_does_not_cancel_orders(self):
        """Regression: 1.0.0 defaulted an unmapped type to CANCEL_OPEN_ORDERS."""
        engine = RunbookIncidentAutomationEngine()
        handlers = wire(engine, *ALL_ACTIONS)
        # Simulate a member added to IncidentType with no playbook behind it.
        engine._playbooks.pop(IncidentType.LATENCY_SPIKE)

        report = engine.execute_runbook(
            make_alert(incident_type=IncidentType.LATENCY_SPIKE, source_service="TICK_BUS")
        )
        self.assertEqual(report.steps_executed, [])
        self.assertEqual(report.status, IncidentStatus.ESCALATED)
        self.assertTrue(report.requires_human_escalation)
        self.assertEqual(handlers[RemediationAction.CANCEL_OPEN_ORDERS].call_count, 0)

    def test_empty_playbook_registration_is_rejected(self):
        engine = RunbookIncidentAutomationEngine()
        with self.assertRaises(RunbookConfigurationError):
            engine.register_playbook(IncidentType.ORDER_THROTTLE, [])

    def test_playbook_entries_must_be_playbook_steps(self):
        engine = RunbookIncidentAutomationEngine()
        with self.assertRaises(RunbookConfigurationError):
            engine.register_playbook(
                IncidentType.ORDER_THROTTLE, [RemediationAction.THROTTLE_ORDER_RATE]
            )

    def test_get_playbook_returns_empty_tuple_for_unmapped_type(self):
        engine = RunbookIncidentAutomationEngine()
        engine._playbooks.pop(IncidentType.ORDER_THROTTLE)
        self.assertEqual(engine.get_playbook(IncidentType.ORDER_THROTTLE), ())


class TestDryRun(unittest.TestCase):
    def setUp(self):
        self.engine = RunbookIncidentAutomationEngine(is_dry_run=True)
        self.handlers = wire(self.engine, *ALL_ACTIONS)

    def test_dry_run_is_not_reported_as_resolved(self):
        """Regression: 1.0.0 returned RESOLVED for a run that resolved nothing."""
        report = self.engine.execute_runbook(make_alert())
        self.assertEqual(report.status, IncidentStatus.DRY_RUN_COMPLETE)
        self.assertNotEqual(report.status, IncidentStatus.RESOLVED)
        self.assertTrue(report.is_dry_run)

    def test_dry_run_invokes_no_handler(self):
        self.engine.execute_runbook(make_alert())
        for handler in self.handlers.values():
            self.assertEqual(handler.call_count, 0)

    def test_dry_run_marks_every_step_skipped(self):
        report = self.engine.execute_runbook(make_alert())
        self.assertEqual(
            [s.status for s in report.steps_executed],
            [StepStatus.SKIPPED_DRY_RUN, StepStatus.SKIPPED_DRY_RUN],
        )

    def test_dry_run_still_reports_an_unwired_action(self):
        """A dry run's job is to prove the wiring, not to rehearse a happy path."""
        engine = RunbookIncidentAutomationEngine(is_dry_run=True)
        engine.register_handler(RemediationAction.CANCEL_OPEN_ORDERS, lambda a: True)
        report = engine.execute_runbook(make_alert())
        self.assertEqual(
            [s.status for s in report.steps_executed],
            [StepStatus.SKIPPED_DRY_RUN, StepStatus.NO_HANDLER_REGISTERED],
        )
        self.assertTrue(report.requires_human_escalation)
        self.assertEqual(report.status, IncidentStatus.DRY_RUN_COMPLETE)


class TestIdempotency(unittest.TestCase):
    def setUp(self):
        self.engine = RunbookIncidentAutomationEngine()
        self.handlers = wire(self.engine, *ALL_ACTIONS)

    def test_redelivered_alert_does_not_re_execute_the_playbook(self):
        """Regression: 1.0.0 re-ran the kill switch on every redelivery."""
        alert = make_alert(incident_id="INC_DUP")
        self.engine.execute_runbook(alert)
        self.engine.execute_runbook(make_alert(incident_id="INC_DUP"))
        self.engine.execute_runbook(make_alert(incident_id="INC_DUP"))
        self.assertEqual(self.handlers[RemediationAction.TRIGGER_KILL_SWITCH].call_count, 1)
        self.assertEqual(self.handlers[RemediationAction.CANCEL_OPEN_ORDERS].call_count, 1)

    def test_redelivery_counts_are_recorded(self):
        self.engine.execute_runbook(make_alert(incident_id="INC_DUP"))
        second = self.engine.execute_runbook(make_alert(incident_id="INC_DUP"))
        third = self.engine.execute_runbook(make_alert(incident_id="INC_DUP"))
        self.assertEqual(second.duplicate_delivery_count, 1)
        self.assertEqual(third.duplicate_delivery_count, 2)

    def test_redelivery_does_not_add_a_second_audit_record(self):
        self.engine.execute_runbook(make_alert(incident_id="INC_DUP"))
        self.engine.execute_runbook(make_alert(incident_id="INC_DUP"))
        self.assertEqual(len(self.engine.get_audit_history()), 1)

    def test_forced_re_execution_runs_again_and_keeps_both_records(self):
        self.engine.execute_runbook(make_alert(incident_id="INC_DUP"))
        self.engine.execute_runbook(make_alert(incident_id="INC_DUP"), force_reexecute=True)
        self.assertEqual(self.handlers[RemediationAction.TRIGGER_KILL_SWITCH].call_count, 2)
        self.assertEqual(len(self.engine.get_audit_history()), 2)

    def test_distinct_incidents_each_execute(self):
        self.engine.execute_runbook(make_alert(incident_id="INC_1"))
        self.engine.execute_runbook(make_alert(incident_id="INC_2"))
        self.assertEqual(self.handlers[RemediationAction.TRIGGER_KILL_SWITCH].call_count, 2)

    def test_concurrent_delivery_of_the_same_alert_executes_once(self):
        barrier = threading.Barrier(4)

        def deliver():
            barrier.wait(timeout=5)
            self.engine.execute_runbook(make_alert(incident_id="INC_RACE"))

        threads = [threading.Thread(target=deliver) for _ in range(4)]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=5)
            self.assertFalse(t.is_alive())
        self.assertEqual(self.handlers[RemediationAction.TRIGGER_KILL_SWITCH].call_count, 1)

    def test_a_handler_mutating_the_alert_cannot_defeat_deduplication(self):
        """A handler is handed the live alert; it must not be able to re-key it."""
        def rename(alert):
            alert.incident_id = "INC_SOMETHING_ELSE"
            return True

        self.engine.register_handler(RemediationAction.CANCEL_OPEN_ORDERS, rename)
        first = self.engine.execute_runbook(make_alert(incident_id="INC_MUT"))
        self.assertEqual(first.incident_id, "INC_MUT")
        self.engine.execute_runbook(make_alert(incident_id="INC_MUT"))
        self.assertEqual(self.handlers[RemediationAction.TRIGGER_KILL_SWITCH].call_count, 1)
        self.assertIsNotNone(self.engine.get_report("INC_MUT"))

    def test_rejects_a_non_alert_argument(self):
        with self.assertRaises(RunbookInputError):
            self.engine.execute_runbook({"incident_id": "INC_1"})


class TestStepTimeout(unittest.TestCase):
    def test_slow_handler_is_reported_as_timed_out_and_escalates(self):
        engine = RunbookIncidentAutomationEngine(step_timeout_seconds=0.05)
        release = threading.Event()
        self.addCleanup(release.set)

        def slow_handler(alert):
            release.wait(timeout=10)
            return True

        engine.register_handler(RemediationAction.CANCEL_OPEN_ORDERS, slow_handler)
        engine.register_handler(RemediationAction.TRIGGER_KILL_SWITCH, lambda a: True)

        report = engine.execute_runbook(make_alert())
        self.assertEqual(
            [s.status for s in report.steps_executed],
            [StepStatus.TIMED_OUT, StepStatus.SUCCESS],
        )
        self.assertEqual(report.status, IncidentStatus.ESCALATED)
        self.assertIn("stopped waiting", report.steps_executed[0].detail)

    def test_timeout_none_calls_handlers_inline(self):
        engine = RunbookIncidentAutomationEngine(step_timeout_seconds=None)
        calling_threads = []
        engine.register_handler(
            RemediationAction.THROTTLE_ORDER_RATE,
            lambda a: calling_threads.append(threading.current_thread()),
        )
        engine.execute_runbook(
            make_alert(incident_type=IncidentType.ORDER_THROTTLE, source_service="SOR")
        )
        self.assertEqual(calling_threads, [threading.current_thread()])

    def test_rejects_non_positive_timeout(self):
        with self.assertRaises(RunbookConfigurationError):
            RunbookIncidentAutomationEngine(step_timeout_seconds=0)
        with self.assertRaises(RunbookConfigurationError):
            RunbookIncidentAutomationEngine(step_timeout_seconds=-1.0)

    def test_default_timeout_is_bounded(self):
        engine = RunbookIncidentAutomationEngine()
        self.assertEqual(engine.step_timeout_seconds, DEFAULT_STEP_TIMEOUT_SECONDS)
        self.assertIsNotNone(engine.step_timeout_seconds)


class TestAuditHistory(unittest.TestCase):
    def setUp(self):
        self.engine = RunbookIncidentAutomationEngine()
        wire(self.engine, *ALL_ACTIONS)

    def test_history_records_each_executed_incident_in_order(self):
        self.engine.execute_runbook(make_alert(incident_id="INC_1"))
        self.engine.execute_runbook(
            make_alert(incident_id="INC_2", incident_type=IncidentType.ORDER_THROTTLE,
                       source_service="SOR")
        )
        history = self.engine.get_audit_history()
        self.assertEqual([r.incident_id for r in history], ["INC_1", "INC_2"])

    def test_history_entries_cannot_be_edited_through_the_accessor(self):
        self.engine.execute_runbook(make_alert(incident_id="INC_1"))
        history = self.engine.get_audit_history()
        history[0].status = IncidentStatus.RESOLVED
        history[0].audit_notes = "tampered"
        history[0].steps_executed.clear()
        refetched = self.engine.get_audit_history()
        self.assertNotEqual(refetched[0].audit_notes, "tampered")
        self.assertEqual(len(refetched[0].steps_executed), 2)

    def test_remediation_step_records_are_frozen(self):
        report = self.engine.execute_runbook(make_alert())
        with self.assertRaises(Exception):
            report.steps_executed[0].status = StepStatus.SUCCESS

    def test_history_is_bounded_and_drops_oldest_first(self):
        engine = RunbookIncidentAutomationEngine(max_audit_history=2)
        wire(engine, *ALL_ACTIONS)
        for i in range(4):
            engine.execute_runbook(make_alert(incident_id=f"INC_{i}"))
        history = engine.get_audit_history()
        self.assertEqual([r.incident_id for r in history], ["INC_2", "INC_3"])

    def test_dropped_incident_is_no_longer_deduplicated(self):
        """Honest consequence of trimming: a very old id can execute again."""
        engine = RunbookIncidentAutomationEngine(max_audit_history=1)
        handlers = wire(engine, *ALL_ACTIONS)
        engine.execute_runbook(make_alert(incident_id="INC_OLD"))
        engine.execute_runbook(make_alert(incident_id="INC_NEW"))
        engine.execute_runbook(make_alert(incident_id="INC_OLD"))
        self.assertEqual(handlers[RemediationAction.TRIGGER_KILL_SWITCH].call_count, 3)

    def test_rejects_zero_max_audit_history(self):
        with self.assertRaises(RunbookConfigurationError):
            RunbookIncidentAutomationEngine(max_audit_history=0)

    def test_get_report_returns_a_copy_or_none(self):
        self.engine.execute_runbook(make_alert(incident_id="INC_1"))
        self.assertIsNone(self.engine.get_report("INC_MISSING"))
        report = self.engine.get_report("  INC_1 ")
        self.assertIsNotNone(report)
        report.audit_notes = "tampered"
        self.assertNotEqual(self.engine.get_report("INC_1").audit_notes, "tampered")


class TestEngineIsolation(unittest.TestCase):
    def test_playbook_edits_do_not_leak_between_engines(self):
        first = RunbookIncidentAutomationEngine()
        second = RunbookIncidentAutomationEngine()
        first.register_playbook(
            IncidentType.ORDER_THROTTLE, [PlaybookStep(RemediationAction.TRIGGER_KILL_SWITCH)]
        )
        self.assertEqual(
            second.get_playbook(IncidentType.ORDER_THROTTLE),
            (PlaybookStep(RemediationAction.THROTTLE_ORDER_RATE),),
        )

    def test_handlers_do_not_leak_between_engines(self):
        first = RunbookIncidentAutomationEngine()
        second = RunbookIncidentAutomationEngine()
        wire(first, *ALL_ACTIONS)
        self.assertEqual(set(second.unhandled_actions()), set(ALL_ACTIONS))


if __name__ == "__main__":
    unittest.main()
