import logging
import unittest

from oncall_escalation_manager import (
    EXECUTIVE,
    PRIMARY,
    SECONDARY,
    EscalationPolicyConfig,
    OnCallEngineer,
    OnCallEscalationManagerEngine,
    SystemIncident,
)

T0 = 1_700_000_000.0          # fixed UTC epoch second; no wall-clock dependence
MIN = 60.0


def mins(n):
    """Epoch second n minutes after T0."""
    return T0 + n * MIN


class BaseEngineTest(unittest.TestCase):
    """Silences engine logging so expected-error paths do not pollute output."""

    def setUp(self):
        logging.disable(logging.CRITICAL)
        self.addCleanup(logging.disable, logging.NOTSET)
        self.roster = [
            OnCallEngineer("E1", "Alice Primary", "PRIMARY", "+1234", "alice@firm.com"),
            OnCallEngineer("E2", "Bob Secondary", "SECONDARY", "+5678", "bob@firm.com"),
            OnCallEngineer("E3", "Carol CTO", "EXECUTIVE", "+9999", "carol@firm.com"),
        ]
        self.engine = OnCallEscalationManagerEngine(self.roster)

    def sev1(self, incident_id="INC_001", created=T0):
        return self.engine.create_incident(
            SystemIncident(incident_id, "SEV_1", "Kill Switch Triggered",
                           "Drawdown limit reached", created)
        )


class TestEscalationLadder(BaseEngineTest):

    def test_sev1_incident_escalation_timeline(self):
        self.sev1()

        rep0 = self.engine.evaluate_escalation("INC_001", T0)
        self.assertEqual(rep0.current_assigned_tier, PRIMARY)
        self.assertEqual(rep0.current_responder_name, "Alice Primary")
        self.assertEqual(rep0.current_responder_id, "E1")
        self.assertEqual(rep0.notification_channel, "PHONE_CALL")
        self.assertFalse(rep0.is_sla_breached)
        self.assertEqual(rep0.status, "ACTIVE_PRIMARY")

        rep3 = self.engine.evaluate_escalation("INC_001", mins(3.5))
        self.assertEqual(rep3.current_assigned_tier, SECONDARY)
        self.assertEqual(rep3.current_responder_name, "Bob Secondary")
        self.assertFalse(rep3.is_sla_breached)
        self.assertEqual(rep3.status, "ESCALATED_WARNING")

        rep5 = self.engine.evaluate_escalation("INC_001", mins(5.5))
        self.assertEqual(rep5.current_assigned_tier, EXECUTIVE)
        self.assertEqual(rep5.current_responder_name, "Carol CTO")
        self.assertTrue(rep5.is_sla_breached)
        self.assertEqual(rep5.status, "SLA_BREACH")

    def test_escalation_thresholds_are_inclusive_at_the_exact_boundary(self):
        """t == threshold must already be the higher tier, not one tick short."""
        self.sev1()
        self.assertEqual(
            self.engine.evaluate_escalation("INC_001", mins(3.0)).current_assigned_tier,
            SECONDARY,
        )
        self.assertEqual(
            self.engine.evaluate_escalation("INC_001", mins(5.0)).current_assigned_tier,
            EXECUTIVE,
        )
        # And one second short of the boundary is still the lower tier.
        self.engine.create_incident(
            SystemIncident("INC_EDGE", "SEV_1", "t", "d", T0))
        self.assertEqual(
            self.engine.evaluate_escalation("INC_EDGE", mins(3.0) - 1).current_assigned_tier,
            PRIMARY,
        )

    def test_sev2_escalates_to_secondary_without_claiming_an_sla_breach(self):
        """
        Regression: SEV-2 escalates to SECONDARY at 10 min but its documented
        acknowledgement SLA is 15 min. The previous version set
        is_sla_breached=True at the escalation threshold, recording a breach
        that had not occurred.
        """
        self.engine.create_incident(
            SystemIncident("INC_002", "SEV_2", "High Latency", "Feed delay > 500ms", T0))

        rep11 = self.engine.evaluate_escalation("INC_002", mins(11))
        self.assertEqual(rep11.current_assigned_tier, SECONDARY)
        self.assertEqual(rep11.notification_channel, "SMS")
        self.assertFalse(rep11.is_sla_breached)
        self.assertEqual(rep11.status, "ESCALATED_WARNING")
        self.assertEqual(rep11.response_sla_minutes, 15.0)

        rep16 = self.engine.evaluate_escalation("INC_002", mins(16))
        self.assertTrue(rep16.is_sla_breached)
        self.assertEqual(rep16.status, "SLA_BREACH")

    def test_sev2_has_a_terminal_executive_rung_but_sev3_does_not(self):
        self.engine.create_incident(SystemIncident("S2", "SEV_2", "t", "d", T0))
        self.engine.create_incident(SystemIncident("S3", "SEV_3", "t", "d", T0))

        self.assertEqual(
            self.engine.evaluate_escalation("S2", mins(45)).current_assigned_tier, EXECUTIVE)
        # SEV-3 tops out at SECONDARY on Slack: no executive is woken for a
        # non-actionable warning.
        rep3 = self.engine.evaluate_escalation("S3", mins(600))
        self.assertEqual(rep3.current_assigned_tier, SECONDARY)
        self.assertEqual(rep3.notification_channel, "SLACK")

    def test_notification_channel_by_severity(self):
        cfg = EscalationPolicyConfig()
        self.assertEqual(cfg.channel_for("SEV_1"), "PHONE_CALL")
        self.assertEqual(cfg.channel_for("SEV_2"), "SMS")
        self.assertEqual(cfg.channel_for("SEV_3"), "SLACK")


class TestSeverityValidation(BaseEngineTest):

    def test_unrecognised_severity_escalates_instead_of_silently_downgrading(self):
        """
        Regression: an unmapped label such as "CRITICAL" previously fell through
        the `else: # SEV_3` branch and was routed to Slack at PRIMARY tier --
        the most severe class silently demoted to the least urgent channel.
        """
        self.engine.create_incident(
            SystemIncident("INC_X", "CRITICAL", "Broker disconnect", "", T0))
        rep = self.engine.evaluate_escalation("INC_X", mins(6))

        self.assertEqual(rep.severity, "SEV_1")
        self.assertEqual(rep.reported_severity, "CRITICAL")
        self.assertTrue(rep.severity_was_coerced)
        self.assertEqual(rep.notification_channel, "PHONE_CALL")
        self.assertEqual(rep.current_assigned_tier, EXECUTIVE)

    def test_reject_policy_refuses_unrecognised_severity(self):
        engine = OnCallEscalationManagerEngine(
            self.roster, EscalationPolicyConfig(unknown_severity_policy="REJECT"))
        with self.assertRaises(ValueError):
            engine.create_incident(SystemIncident("INC_Y", "P1", "t", "d", T0))

    def test_severity_is_normalised_for_case_and_whitespace(self):
        self.engine.create_incident(SystemIncident("INC_Z", " sev_1 ", "t", "d", T0))
        rep = self.engine.evaluate_escalation("INC_Z", T0)
        self.assertEqual(rep.severity, "SEV_1")
        self.assertFalse(rep.severity_was_coerced)

    def test_invalid_unknown_severity_policy_is_rejected(self):
        with self.assertRaises(ValueError):
            EscalationPolicyConfig(unknown_severity_policy="IGNORE")


class TestAcknowledgement(BaseEngineTest):

    def test_incident_acknowledgment(self):
        self.engine.create_incident(
            SystemIncident("INC_002", "SEV_2", "High Latency Warning", "Feed delay", T0))
        self.assertTrue(self.engine.acknowledge_incident("INC_002", "E2", mins(2)))

        rep = self.engine.evaluate_escalation("INC_002", mins(5))
        self.assertEqual(rep.status, "ACKNOWLEDGED")
        self.assertEqual(rep.current_responder_name, "Bob Secondary")
        self.assertEqual(rep.acknowledged_by_id, "E2")
        self.assertEqual(rep.ack_latency_minutes, 2.0)
        self.assertFalse(rep.is_sla_breached)
        self.assertEqual(rep.notification_channel, "NONE")

    def test_late_acknowledgement_does_not_erase_the_sla_breach(self):
        """
        Regression: acknowledging at t=60 min against a 5 min SEV-1 SLA
        previously reported is_sla_breached=False, putting a met SLA in the
        audit trail for a response that was 55 minutes late.
        """
        self.sev1()
        self.engine.acknowledge_incident("INC_001", "E1", mins(60))

        rep = self.engine.evaluate_escalation("INC_001", mins(61))
        self.assertEqual(rep.status, "ACKNOWLEDGED_LATE")
        self.assertTrue(rep.is_sla_breached)
        self.assertEqual(rep.ack_latency_minutes, 60.0)
        self.assertEqual(rep.response_sla_minutes, 5.0)

    def test_acknowledgement_exactly_at_the_sla_is_within_sla(self):
        self.sev1()
        self.engine.acknowledge_incident("INC_001", "E1", mins(5))
        rep = self.engine.evaluate_escalation("INC_001", mins(6))
        self.assertEqual(rep.status, "ACKNOWLEDGED")
        self.assertFalse(rep.is_sla_breached)

    def test_first_acknowledgement_owns_the_sla_measurement(self):
        """A re-acknowledgement must not overwrite the original response time."""
        self.sev1()
        self.engine.acknowledge_incident("INC_001", "E1", mins(2))
        self.engine.acknowledge_incident("INC_001", "E2", mins(45))

        inc = self.engine.active_incidents["INC_001"]
        self.assertEqual(inc.acknowledged_at_utc, mins(2))
        self.assertEqual(inc.last_ack_at_utc, mins(45))
        self.assertEqual(
            self.engine.evaluate_escalation("INC_001", mins(46)).ack_latency_minutes, 2.0)

    def test_acknowledging_an_unknown_incident_returns_false(self):
        self.assertFalse(self.engine.acknowledge_incident("NOPE", "E1", T0))

    def test_unknown_engineer_id_falls_back_to_the_raw_id(self):
        self.sev1()
        self.engine.acknowledge_incident("INC_001", "contractor-7", mins(1))
        rep = self.engine.evaluate_escalation("INC_001", mins(2))
        self.assertEqual(rep.current_responder_name, "contractor-7")
        self.assertEqual(rep.acknowledged_by_id, "contractor-7")


class TestAckTimeoutRetrigger(BaseEngineTest):

    def test_acknowledged_but_unresolved_incident_re_triggers(self):
        """'Ack and go back to sleep' must not silence the pager forever."""
        self.sev1()
        self.engine.acknowledge_incident("INC_001", "E1", mins(1))

        still_acked = self.engine.evaluate_escalation("INC_001", mins(29))
        self.assertEqual(still_acked.status, "ACKNOWLEDGED")
        self.assertEqual(still_acked.notification_channel, "NONE")

        retriggered = self.engine.evaluate_escalation("INC_001", mins(31))
        self.assertEqual(retriggered.status, "RE_TRIGGERED_ACK_TIMEOUT")
        self.assertEqual(retriggered.retrigger_count, 1)
        # Polling again must not inflate the counter.
        self.assertEqual(
            self.engine.evaluate_escalation("INC_001", mins(32)).retrigger_count, 1)
        # 31 minutes elapsed puts a SEV-1 well past the executive rung.
        self.assertEqual(retriggered.current_assigned_tier, EXECUTIVE)
        self.assertEqual(retriggered.notification_channel, "PHONE_CALL")
        self.assertTrue(retriggered.is_sla_breached)

    def test_re_acknowledging_a_re_triggered_incident_silences_it_again(self):
        self.sev1()
        self.engine.acknowledge_incident("INC_001", "E1", mins(1))
        self.assertEqual(
            self.engine.evaluate_escalation("INC_001", mins(31)).status,
            "RE_TRIGGERED_ACK_TIMEOUT")

        self.engine.acknowledge_incident("INC_001", "E1", mins(32))
        rep = self.engine.evaluate_escalation("INC_001", mins(35))
        self.assertEqual(rep.status, "ACKNOWLEDGED")
        self.assertEqual(rep.ack_latency_minutes, 1.0)   # original latency stands
        # The prompt first acknowledgement is genuine, but the incident was then
        # abandoned for half an hour -- that must remain visible in the audit.
        self.assertEqual(rep.retrigger_count, 1)

    def test_retrigger_pages_afresh_even_at_an_already_paged_tier(self):
        """
        An incident that had already escalated to EXECUTIVE before being
        acknowledged must still page on re-trigger. Leaving the paged-tier
        record intact returned is_new_escalation=False, so a caller that pages
        on that flag sent nothing and the ack timeout was silently defeated.
        """
        self.sev1()
        for t in (0, 3.5, 5.5):
            self.engine.evaluate_escalation("INC_001", mins(t))
        self.engine.acknowledge_incident("INC_001", "E3", mins(6))

        rep = self.engine.evaluate_escalation("INC_001", mins(37))
        self.assertEqual(rep.status, "RE_TRIGGERED_ACK_TIMEOUT")
        self.assertEqual(rep.current_assigned_tier, EXECUTIVE)
        self.assertTrue(rep.is_new_escalation)
        # ...but still only once per re-trigger, not on every subsequent poll.
        self.assertFalse(
            self.engine.evaluate_escalation("INC_001", mins(38)).is_new_escalation)

    def test_ack_timeout_can_be_disabled(self):
        engine = OnCallEscalationManagerEngine(
            self.roster, EscalationPolicyConfig(ack_timeout_mins=None))
        engine.create_incident(SystemIncident("INC_A", "SEV_1", "t", "d", T0))
        engine.acknowledge_incident("INC_A", "E1", mins(1))
        self.assertEqual(
            engine.evaluate_escalation("INC_A", mins(500)).status, "ACKNOWLEDGED")

    def test_non_positive_ack_timeout_is_rejected(self):
        with self.assertRaises(ValueError):
            EscalationPolicyConfig(ack_timeout_mins=0)


class TestResolution(BaseEngineTest):

    def test_resolution_stops_escalation(self):
        self.sev1()
        self.engine.acknowledge_incident("INC_001", "E1", mins(2))
        self.assertTrue(self.engine.resolve_incident("INC_001", mins(20)))

        rep = self.engine.evaluate_escalation("INC_001", mins(600))
        self.assertEqual(rep.status, "RESOLVED")
        self.assertEqual(rep.notification_channel, "NONE")
        self.assertFalse(rep.is_sla_breached)

    def test_resolving_an_unacknowledged_breached_incident_keeps_the_breach(self):
        self.sev1()
        self.engine.resolve_incident("INC_001", mins(40))
        rep = self.engine.evaluate_escalation("INC_001", mins(41))
        self.assertEqual(rep.status, "RESOLVED")
        self.assertTrue(rep.is_sla_breached)

    def test_acknowledging_a_resolved_incident_is_refused(self):
        self.sev1()
        self.engine.resolve_incident("INC_001", mins(10))
        self.assertFalse(self.engine.acknowledge_incident("INC_001", "E1", mins(11)))

    def test_resolving_an_unknown_incident_returns_false(self):
        self.assertFalse(self.engine.resolve_incident("NOPE", T0))

    def test_purge_removes_only_old_resolved_incidents(self):
        self.sev1("KEEP_OPEN")
        self.sev1("RESOLVED_OLD")
        self.sev1("RESOLVED_RECENT")
        self.engine.resolve_incident("RESOLVED_OLD", mins(10))
        self.engine.resolve_incident("RESOLVED_RECENT", mins(500))

        self.assertEqual(self.engine.purge_resolved_incidents(mins(100)), 1)
        self.assertIn("KEEP_OPEN", self.engine.active_incidents)
        self.assertIn("RESOLVED_RECENT", self.engine.active_incidents)
        self.assertNotIn("RESOLVED_OLD", self.engine.active_incidents)


class TestRosterAndRotation(BaseEngineTest):
    """
    These tests isolate *who is on call* from *which tier is due*. They use a
    SEV-3 incident under a policy whose secondary rung is far outside the test
    window, so the assigned tier stays PRIMARY and any change in the resolved
    responder is attributable to the shift schedule alone.
    """

    #: Escalation effectively disabled: PRIMARY for the whole test window.
    NO_ESCALATION = EscalationPolicyConfig(
        sev3_sec_escalate_mins=1_000_000.0, sev3_response_sla_mins=1_000_000.0)

    def primary_only_engine(self, roster):
        engine = OnCallEscalationManagerEngine(roster, self.NO_ESCALATION)
        engine.create_incident(SystemIncident("INC_R", "SEV_3", "t", "d", T0))
        return engine

    def test_multiple_engineers_per_tier_are_all_retained(self):
        """
        Regression: the roster was a dict keyed by tier, so registering a
        rotation of engineers on one tier kept only the last one.
        """
        engine = OnCallEscalationManagerEngine([
            OnCallEngineer("E1", "Alice", "PRIMARY", "+1", "a@f.com"),
            OnCallEngineer("E4", "Dave", "PRIMARY", "+4", "d@f.com"),
        ])
        self.assertEqual(len(engine.roster_by_tier[PRIMARY]), 2)

    def test_duplicate_engineer_ids_are_rejected(self):
        with self.assertRaises(ValueError):
            OnCallEscalationManagerEngine([
                OnCallEngineer("E1", "Alice", "PRIMARY", "+1", "a@f.com"),
                OnCallEngineer("E1", "Alicia", "SECONDARY", "+2", "b@f.com"),
            ])

    def test_shift_schedule_selects_the_engineer_on_duty(self):
        day = OnCallEngineer("D1", "Day Shift", "PRIMARY", "+1", "d@f.com",
                             shift_start_utc=T0, shift_end_utc=mins(720))
        night = OnCallEngineer("N1", "Night Shift", "PRIMARY", "+2", "n@f.com",
                               shift_start_utc=mins(720), shift_end_utc=mins(1440))
        engine = self.primary_only_engine([day, night])

        self.assertEqual(
            engine.evaluate_escalation("INC_R", mins(100)).current_responder_id, "D1")
        self.assertEqual(
            engine.evaluate_escalation("INC_R", mins(800)).current_responder_id, "N1")

    def test_handover_boundary_pages_exactly_one_engineer(self):
        """Half-open [start, end): at the handover instant the incoming
        engineer owns the page, not both."""
        day = OnCallEngineer("D1", "Day", "PRIMARY", "+1", "d@f.com",
                             shift_start_utc=T0, shift_end_utc=mins(720))
        night = OnCallEngineer("N1", "Night", "PRIMARY", "+2", "n@f.com",
                               shift_start_utc=mins(720), shift_end_utc=mins(1440))
        engine = self.primary_only_engine([day, night])

        self.assertEqual(
            engine.evaluate_escalation("INC_R", mins(720) - 1).current_responder_id, "D1")
        self.assertEqual(
            engine.evaluate_escalation("INC_R", mins(720)).current_responder_id, "N1")

    def test_hole_in_the_rota_is_reported_as_undeliverable(self):
        gapped = OnCallEngineer("D1", "Day", "PRIMARY", "+1", "d@f.com",
                                shift_start_utc=T0, shift_end_utc=mins(60))
        engine = self.primary_only_engine([gapped])

        rep = engine.evaluate_escalation("INC_R", mins(120))
        self.assertFalse(rep.is_notification_deliverable)
        self.assertEqual(rep.current_responder_name, "UNASSIGNED")
        self.assertIn("hole in the rota", " ".join(rep.delivery_warnings))

    def test_always_on_engineer_is_the_fallback_outside_every_shift(self):
        scheduled = OnCallEngineer("D1", "Day", "PRIMARY", "+1", "d@f.com",
                                   shift_start_utc=T0, shift_end_utc=mins(60))
        fallback = OnCallEngineer("F1", "Fallback", "PRIMARY", "+9", "f@f.com")
        engine = self.primary_only_engine([scheduled, fallback])

        self.assertEqual(
            engine.evaluate_escalation("INC_R", mins(30)).current_responder_id, "D1")
        rep = engine.evaluate_escalation("INC_R", mins(120))
        self.assertEqual(rep.current_responder_id, "F1")
        self.assertTrue(rep.is_notification_deliverable)

    def test_unstaffed_tier_is_reported_as_undeliverable(self):
        """
        Regression: an escalation to an unstaffed tier previously fabricated a
        'Duty Engineer' with an empty phone number, producing a report that
        looked entirely normal while the page reached nobody.
        """
        engine = OnCallEscalationManagerEngine(
            [OnCallEngineer("E1", "Alice", "PRIMARY", "+1", "a@f.com")])
        engine.create_incident(SystemIncident("INC_U", "SEV_1", "t", "d", T0))

        rep = engine.evaluate_escalation("INC_U", mins(10))
        self.assertEqual(rep.current_assigned_tier, EXECUTIVE)
        self.assertEqual(rep.current_responder_name, "UNASSIGNED")
        self.assertIsNone(rep.current_responder_id)
        self.assertFalse(rep.is_notification_deliverable)
        self.assertIn("UNDELIVERABLE", rep.audit_notes)

    def test_engineer_without_contact_for_the_channel_is_undeliverable(self):
        # A phone-less engineer on a PHONE_CALL severity: the page has nowhere
        # to go even though an engineer was successfully resolved.
        engine = OnCallEscalationManagerEngine(
            [OnCallEngineer("E1", "Alice", "PRIMARY", "", "a@f.com")])
        engine.create_incident(SystemIncident("INC_C", "SEV_1", "t", "d", T0))

        rep = engine.evaluate_escalation("INC_C", T0)
        self.assertEqual(rep.current_responder_id, "E1")
        self.assertFalse(rep.is_notification_deliverable)
        self.assertIn("no contact address", " ".join(rep.delivery_warnings))

    def test_invalid_tier_and_inverted_shift_are_rejected(self):
        with self.assertRaises(ValueError):
            OnCallEngineer("E9", "Nobody", "MANAGER", "+1", "n@f.com")
        with self.assertRaises(ValueError):
            OnCallEngineer("E9", "Nobody", "PRIMARY", "+1", "n@f.com",
                           shift_start_utc=mins(10), shift_end_utc=mins(5))

    def test_tier_string_is_normalised(self):
        self.assertEqual(
            OnCallEngineer("E9", "N", " primary ", "+1", "n@f.com").tier, PRIMARY)


class TestClockHandling(BaseEngineTest):

    def test_evaluation_before_creation_is_rejected(self):
        """
        Regression: max(0.0, ...) clamped a backwards clock to zero elapsed, so
        an incident stamped with a local-time (not UTC) timestamp sat at PRIMARY
        forever and never escalated.
        """
        self.engine.create_incident(
            SystemIncident("INC_TZ", "SEV_1", "t", "d", T0 + 19800))  # naive IST stamp
        with self.assertRaises(ValueError) as ctx:
            self.engine.evaluate_escalation("INC_TZ", mins(10))
        self.assertIn("UTC epoch seconds", str(ctx.exception))

    def test_sub_second_clock_jitter_is_tolerated(self):
        self.sev1()
        rep = self.engine.evaluate_escalation("INC_001", T0 - 1.0)
        self.assertEqual(rep.elapsed_minutes, 0.0)
        self.assertEqual(rep.current_assigned_tier, PRIMARY)

    def test_acknowledgement_before_creation_is_rejected(self):
        self.sev1()
        with self.assertRaises(ValueError):
            self.engine.acknowledge_incident("INC_001", "E1", T0 - 600)

    def test_non_finite_timestamps_are_rejected(self):
        with self.assertRaises(ValueError):
            self.engine.create_incident(
                SystemIncident("INC_NAN", "SEV_1", "t", "d", float("nan")))
        self.sev1()
        with self.assertRaises(ValueError):
            self.engine.evaluate_escalation("INC_001", float("inf"))

    def test_numeric_string_timestamps_are_coerced_not_deferred(self):
        """
        A JSON alert payload delivers epoch seconds as a string. Accepting it
        and storing it unconverted raised TypeError from an unrelated
        subtraction much later, inside evaluation.
        """
        inc = self.engine.create_incident(
            SystemIncident("INC_STR", "SEV_1", "t", "d", str(int(T0))))
        self.assertIsInstance(inc.created_at_utc, float)
        rep = self.engine.evaluate_escalation("INC_STR", str(int(mins(4))))
        self.assertEqual(rep.current_assigned_tier, SECONDARY)
        self.assertEqual(rep.elapsed_minutes, 4.0)

    def test_non_numeric_timestamps_are_rejected(self):
        with self.assertRaises(ValueError):
            self.engine.create_incident(
                SystemIncident("INC_BAD", "SEV_1", "t", "d", "2026-08-27T09:00:00Z"))
        self.sev1()
        with self.assertRaises(ValueError):
            self.engine.acknowledge_incident("INC_001", "E1", "not-a-time")
        with self.assertRaises(ValueError):
            OnCallEngineer("E9", "N", "PRIMARY", "+1", "n@f.com", shift_start_utc="soon")


class TestIdempotencyAndDeduplication(BaseEngineTest):

    def test_duplicate_create_does_not_reset_acknowledged_state(self):
        """
        Regression: a redelivered alert webhook overwrote the stored incident,
        resetting created_at_utc and discarding an existing acknowledgement.
        """
        self.sev1()
        self.engine.acknowledge_incident("INC_001", "E1", mins(1))

        returned = self.engine.create_incident(
            SystemIncident("INC_001", "SEV_1", "Kill Switch Triggered", "", mins(30)))
        self.assertTrue(returned.acknowledged)
        self.assertEqual(returned.created_at_utc, T0)
        self.assertEqual(self.engine.evaluate_escalation("INC_001", mins(2)).status,
                         "ACKNOWLEDGED")

    def test_is_new_escalation_fires_once_per_tier(self):
        """A polling caller pages on the transition, not on every tick."""
        self.sev1()
        self.assertTrue(self.engine.evaluate_escalation("INC_001", T0).is_new_escalation)
        self.assertFalse(self.engine.evaluate_escalation("INC_001", mins(1)).is_new_escalation)
        self.assertTrue(self.engine.evaluate_escalation("INC_001", mins(3.5)).is_new_escalation)
        self.assertFalse(self.engine.evaluate_escalation("INC_001", mins(4)).is_new_escalation)
        self.assertTrue(self.engine.evaluate_escalation("INC_001", mins(5.5)).is_new_escalation)

    def test_what_if_evaluation_does_not_consume_the_dedup_token(self):
        self.sev1()
        preview = self.engine.evaluate_escalation("INC_001", mins(4), record_notification=False)
        self.assertTrue(preview.is_new_escalation)
        self.assertEqual(self.engine.active_incidents["INC_001"].notified_tiers, [])
        self.assertTrue(self.engine.evaluate_escalation("INC_001", mins(4)).is_new_escalation)

    def test_empty_incident_id_is_rejected(self):
        with self.assertRaises(ValueError):
            self.engine.create_incident(SystemIncident("", "SEV_1", "t", "d", T0))

    def test_evaluating_an_unknown_incident_raises(self):
        with self.assertRaises(ValueError):
            self.engine.evaluate_escalation("NOPE", T0)


class TestPolicyConfigValidation(BaseEngineTest):

    def test_non_increasing_ladder_is_rejected(self):
        """
        With secondary at 6 and executive at 5, the SECONDARY rung can never be
        selected and the secondary engineer is silently never paged.
        """
        with self.assertRaises(ValueError):
            EscalationPolicyConfig(sev1_sec_escalate_mins=6.0, sev1_exec_escalate_mins=5.0)

    def test_equal_thresholds_are_rejected(self):
        with self.assertRaises(ValueError):
            EscalationPolicyConfig(sev1_sec_escalate_mins=5.0, sev1_exec_escalate_mins=5.0)

    def test_non_positive_sla_is_rejected(self):
        with self.assertRaises(ValueError):
            EscalationPolicyConfig(sev1_response_sla_mins=0.0)

    def test_custom_policy_is_honoured(self):
        engine = OnCallEscalationManagerEngine(
            self.roster,
            EscalationPolicyConfig(sev1_sec_escalate_mins=1.0,
                                   sev1_exec_escalate_mins=2.0,
                                   sev1_response_sla_mins=2.0),
        )
        engine.create_incident(SystemIncident("INC_P", "SEV_1", "t", "d", T0))
        self.assertEqual(
            engine.evaluate_escalation("INC_P", mins(1.5)).current_assigned_tier, SECONDARY)
        rep = engine.evaluate_escalation("INC_P", mins(2.5))
        self.assertEqual(rep.current_assigned_tier, EXECUTIVE)
        self.assertTrue(rep.is_sla_breached)

    def test_default_ladder_matches_documented_thresholds(self):
        """Independently asserts the (minutes, tier) rungs the docs promise."""
        cfg = EscalationPolicyConfig()
        self.assertEqual(cfg.ladder("SEV_1"),
                         [(0.0, PRIMARY), (3.0, SECONDARY), (5.0, EXECUTIVE)])
        self.assertEqual(cfg.ladder("SEV_2"),
                         [(0.0, PRIMARY), (10.0, SECONDARY), (30.0, EXECUTIVE)])
        self.assertEqual(cfg.ladder("SEV_3"), [(0.0, PRIMARY), (30.0, SECONDARY)])


if __name__ == '__main__':
    unittest.main()
