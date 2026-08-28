"""Unit tests for risk-control-bypass-audit-logging."""
import logging
import re
import threading
import unittest
from datetime import datetime, timedelta, timezone

from risk_override_audit_logger import (
    SEVERITY_CRITICAL,
    SEVERITY_HIGH,
    SEVERITY_MEDIUM,
    STATUS_BYPASSES_LOGGED,
    STATUS_NO_BYPASSES,
    STATUS_SUSPICIOUS_BYPASSES,
    RiskBypassAuditError,
    RiskBypassEvent,
    RiskControlBypassAuditEngine,
)

logging.getLogger("risk_override_audit_logger").setLevel(logging.CRITICAL)

# A fixed recording clock keeps hashes and forward-dating checks deterministic.
RECORDED_AT = datetime(2024, 6, 15, 12, 0, 0, tzinfo=timezone.utc)


def make_event(**overrides) -> RiskBypassEvent:
    """A clean, well-authorised critical bypass; override to introduce a flaw."""
    base = dict(
        event_id="BYP_001",
        timestamp_iso="2024-06-15T10:30:00Z",
        bypassed_control="MAX_POSITION_SIZE",
        original_limit_value="1000",
        override_value="2000",
        authorized_by="risk_officer",
        justification="Temporary increase for block trade execution per CRO approval.",
        strategy_id="STRAT_ALPHA",
        instrument="AAPL",
    )
    base.update(overrides)
    return RiskBypassEvent(**base)


class TestSeverityClassification(unittest.TestCase):

    def setUp(self):
        self.engine = RiskControlBypassAuditEngine()

    def test_critical_control_is_critical(self):
        entry = self.engine.log_bypass(
            make_event(bypassed_control="KILL_SWITCH"), recorded_at=RECORDED_AT)
        self.assertEqual(entry.severity, SEVERITY_CRITICAL)

    def test_rts6_mandated_control_is_high_not_medium(self):
        """MAX_ORDER_VALUE is an RTS 6 Art. 15(1) mandated control.

        It contains neither "LIMIT" nor "CAP", so the name heuristic alone would
        misclassify a bypass of a *mandated* pre-trade control as MEDIUM.
        """
        for control in ("MAX_ORDER_VALUE", "MAX_ORDER_VOLUME", "PRICE_COLLAR",
                        "MAX_MESSAGE_RATE"):
            with self.subTest(control=control):
                engine = RiskControlBypassAuditEngine()
                entry = engine.log_bypass(
                    make_event(bypassed_control=control), recorded_at=RECORDED_AT)
                self.assertEqual(entry.severity, SEVERITY_HIGH)

    def test_unregistered_control_falls_back_to_medium(self):
        entry = self.engine.log_bypass(
            make_event(bypassed_control="SPREAD_VETO"), recorded_at=RECORDED_AT)
        self.assertEqual(entry.severity, SEVERITY_MEDIUM)

    def test_control_name_is_case_and_whitespace_insensitive(self):
        entry = self.engine.log_bypass(
            make_event(bypassed_control="  kill_switch  "), recorded_at=RECORDED_AT)
        self.assertEqual(entry.severity, SEVERITY_CRITICAL)

    def test_custom_control_sets_are_honoured(self):
        engine = RiskControlBypassAuditEngine(
            critical_controls={"HOUSE_HALT"}, high_severity_controls=set())
        self.assertEqual(
            engine.log_bypass(make_event(bypassed_control="HOUSE_HALT"),
                              recorded_at=RECORDED_AT).severity,
            SEVERITY_CRITICAL)
        # MAX_POSITION_SIZE is no longer critical once the set is replaced.
        self.assertEqual(
            engine.log_bypass(make_event(event_id="BYP_002",
                                         bypassed_control="MAX_POSITION_SIZE"),
                              recorded_at=RECORDED_AT).severity,
            SEVERITY_MEDIUM)


class TestSuspicionFlags(unittest.TestCase):

    def setUp(self):
        self.engine = RiskControlBypassAuditEngine()

    def test_authorized_bypass_with_justification_is_clean(self):
        entry = self.engine.log_bypass(make_event(), recorded_at=RECORDED_AT)
        self.assertFalse(entry.is_suspicious)
        self.assertIsNone(entry.flag_reason)
        self.assertEqual(entry.flag_reasons, [])

    def test_unauthorized_principal_flagged(self):
        entry = self.engine.log_bypass(
            make_event(authorized_by="junior_trader"), recorded_at=RECORDED_AT)
        self.assertTrue(entry.is_suspicious)
        self.assertIn("Unauthorized principal", entry.flag_reason)

    def test_principal_match_is_case_and_whitespace_insensitive(self):
        entry = self.engine.log_bypass(
            make_event(authorized_by="  Risk_Officer "), recorded_at=RECORDED_AT)
        self.assertFalse(entry.is_suspicious)

    def test_blank_principal_flagged_but_still_recorded(self):
        """A bypass with no named authoriser must be kept, not rejected."""
        entry = self.engine.log_bypass(
            make_event(authorized_by="   "), recorded_at=RECORDED_AT)
        self.assertTrue(entry.is_suspicious)
        self.assertIn("No authorising principal recorded.", entry.flag_reasons)
        self.assertEqual(self.engine.generate_audit_report().total_bypass_events, 1)

    def test_short_and_none_justification_flagged(self):
        for justification in ("", "  ", "ab", None):
            with self.subTest(justification=justification):
                engine = RiskControlBypassAuditEngine()
                entry = engine.log_bypass(
                    make_event(justification=justification), recorded_at=RECORDED_AT)
                self.assertTrue(entry.is_suspicious)
                self.assertIn("Missing or insufficient justification.", entry.flag_reasons)

    def test_min_justification_chars_is_configurable(self):
        strict = RiskControlBypassAuditEngine(min_justification_chars=200)
        entry = strict.log_bypass(make_event(), recorded_at=RECORDED_AT)
        self.assertIn("Missing or insufficient justification.", entry.flag_reasons)

    def test_self_authorisation_flagged(self):
        """RTS 6 Art. 1(c) separates the trading desk from risk control."""
        entry = self.engine.log_bypass(
            make_event(requested_by="Risk_Officer"), recorded_at=RECORDED_AT)
        self.assertTrue(entry.is_suspicious)
        self.assertIn("Self-authorised", entry.flag_reason)

    def test_distinct_requester_and_authoriser_not_flagged(self):
        entry = self.engine.log_bypass(
            make_event(requested_by="desk_trader"), recorded_at=RECORDED_AT)
        self.assertFalse(entry.is_suspicious)

    def test_risk_function_verification_is_opt_in(self):
        default_entry = self.engine.log_bypass(make_event(), recorded_at=RECORDED_AT)
        self.assertFalse(default_entry.is_suspicious)

        rts6 = RiskControlBypassAuditEngine(require_risk_function_verification=True)
        missing = rts6.log_bypass(make_event(), recorded_at=RECORDED_AT)
        self.assertIn("No risk management function verification recorded.",
                      missing.flag_reasons)
        supplied = rts6.log_bypass(
            make_event(event_id="BYP_002", risk_function_verifier="risk_control_desk"),
            recorded_at=RECORDED_AT)
        self.assertFalse(supplied.is_suspicious)

    def test_open_ended_critical_bypass_flagged_when_expiry_required(self):
        engine = RiskControlBypassAuditEngine(require_expiry_for_critical=True)
        open_ended = engine.log_bypass(make_event(), recorded_at=RECORDED_AT)
        self.assertIn("Open-ended bypass of a critical control (no expiry recorded).",
                      open_ended.flag_reasons)
        bounded = engine.log_bypass(
            make_event(event_id="BYP_002", expires_at_iso="2024-06-15T11:30:00Z"),
            recorded_at=RECORDED_AT)
        self.assertFalse(bounded.is_suspicious)

    def test_expiry_not_after_event_is_flagged(self):
        entry = self.engine.log_bypass(
            make_event(expires_at_iso="2024-06-15T10:30:00Z"), recorded_at=RECORDED_AT)
        self.assertIn("Override expiry is not after the bypass timestamp.",
                      entry.flag_reasons)

    def test_forward_dated_event_flagged(self):
        entry = self.engine.log_bypass(
            make_event(timestamp_iso="2024-06-15T14:00:00Z"), recorded_at=RECORDED_AT)
        self.assertTrue(entry.is_suspicious)
        self.assertIn("ahead of the recording clock", entry.flag_reason)

    def test_small_clock_skew_within_tolerance_not_flagged(self):
        entry = self.engine.log_bypass(
            make_event(timestamp_iso="2024-06-15T12:00:02Z"), recorded_at=RECORDED_AT)
        self.assertFalse(entry.is_suspicious)

    def test_multiple_flags_accumulate(self):
        entry = self.engine.log_bypass(
            make_event(authorized_by="unknown_user", justification=""),
            recorded_at=RECORDED_AT)
        self.assertEqual(len(entry.flag_reasons), 2)
        self.assertIn("Unauthorized principal", entry.flag_reason)
        self.assertIn("Missing or insufficient justification.", entry.flag_reason)


class TestInputValidation(unittest.TestCase):

    def setUp(self):
        self.engine = RiskControlBypassAuditEngine()

    def test_blank_event_id_raises(self):
        with self.assertRaises(RiskBypassAuditError):
            self.engine.log_bypass(make_event(event_id="   "), recorded_at=RECORDED_AT)

    def test_blank_control_raises(self):
        with self.assertRaises(RiskBypassAuditError):
            self.engine.log_bypass(make_event(bypassed_control=""), recorded_at=RECORDED_AT)

    def test_unparseable_timestamp_raises(self):
        with self.assertRaises(RiskBypassAuditError):
            self.engine.log_bypass(
                make_event(timestamp_iso="15/06/2024 10:30"), recorded_at=RECORDED_AT)

    def test_timezone_naive_timestamp_raises(self):
        """Naive local times cannot be ordered across a DST transition."""
        with self.assertRaises(RiskBypassAuditError):
            self.engine.log_bypass(
                make_event(timestamp_iso="2024-06-15T10:30:00"), recorded_at=RECORDED_AT)

    def test_naive_recorded_at_raises(self):
        with self.assertRaises(RiskBypassAuditError):
            self.engine.log_bypass(
                make_event(), recorded_at=datetime(2024, 6, 15, 12, 0, 0))

    def test_non_event_argument_raises(self):
        with self.assertRaises(RiskBypassAuditError):
            self.engine.log_bypass({"event_id": "BYP_001"}, recorded_at=RECORDED_AT)

    def test_offset_timestamp_accepted(self):
        entry = self.engine.log_bypass(
            make_event(timestamp_iso="2024-06-15T12:30:00+05:30"), recorded_at=RECORDED_AT)
        self.assertFalse(entry.is_suspicious)

    def test_negative_engine_configuration_raises(self):
        with self.assertRaises(RiskBypassAuditError):
            RiskControlBypassAuditEngine(min_justification_chars=-1)
        with self.assertRaises(RiskBypassAuditError):
            RiskControlBypassAuditEngine(clock_skew_tolerance=timedelta(seconds=-1))

    def test_rejected_event_is_not_appended_to_the_chain(self):
        self.engine.log_bypass(make_event(), recorded_at=RECORDED_AT)
        head_before = self.engine.chain_head_hash
        with self.assertRaises(RiskBypassAuditError):
            self.engine.log_bypass(
                make_event(event_id="BYP_002", timestamp_iso="not-a-time"),
                recorded_at=RECORDED_AT)
        self.assertEqual(self.engine.chain_head_hash, head_before)
        self.assertEqual(self.engine.generate_audit_report().total_bypass_events, 1)


class TestIdempotency(unittest.TestCase):

    def setUp(self):
        self.engine = RiskControlBypassAuditEngine()

    def test_identical_resubmission_is_idempotent(self):
        first = self.engine.log_bypass(make_event(), recorded_at=RECORDED_AT)
        # A retried write after a lost acknowledgement must not double-record.
        second = self.engine.log_bypass(
            make_event(), recorded_at=RECORDED_AT + timedelta(seconds=30))
        self.assertEqual(first.record_hash, second.record_hash)
        self.assertEqual(self.engine.generate_audit_report().total_bypass_events, 1)

    def test_conflicting_resubmission_raises(self):
        self.engine.log_bypass(make_event(), recorded_at=RECORDED_AT)
        with self.assertRaises(RiskBypassAuditError):
            self.engine.log_bypass(
                make_event(override_value="9999"), recorded_at=RECORDED_AT)
        self.assertEqual(self.engine.generate_audit_report().total_bypass_events, 1)


class TestChainIntegrity(unittest.TestCase):

    def setUp(self):
        self.engine = RiskControlBypassAuditEngine()
        self.engine.log_bypass(make_event(), recorded_at=RECORDED_AT)
        self.engine.log_bypass(
            make_event(event_id="BYP_002", bypassed_control="KILL_SWITCH",
                       authorized_by="unknown_user", justification=""),
            recorded_at=RECORDED_AT)

    def test_clean_chain_verifies(self):
        ok, reason = self.engine.verify_integrity()
        self.assertTrue(ok)
        self.assertIsNone(reason)

    def test_hashes_are_sha256_hex_and_linked(self):
        entries = self.engine.entries
        self.assertEqual(entries[0].previous_hash, "0" * 64)
        self.assertEqual(entries[1].previous_hash, entries[0].record_hash)
        self.assertEqual(self.engine.chain_head_hash, entries[1].record_hash)
        for entry in entries:
            self.assertRegex(entry.record_hash, re.compile(r"^[0-9a-f]{64}$"))

    def test_hash_is_content_dependent_and_reproducible(self):
        """Same input and clock -> same hash; any changed field -> different hash."""
        twin = RiskControlBypassAuditEngine()
        same = twin.log_bypass(make_event(), recorded_at=RECORDED_AT)
        self.assertEqual(same.record_hash, self.engine.entries[0].record_hash)

        other = RiskControlBypassAuditEngine()
        changed = other.log_bypass(make_event(override_value="2001"),
                                   recorded_at=RECORDED_AT)
        self.assertNotEqual(changed.record_hash, same.record_hash)

    def test_edited_event_is_detected(self):
        # Simulate an in-place edit of a stored record. Reaching into the private
        # list is deliberate: the point is that a tamperer would not go through
        # the public API.
        self.engine._log[0].override_value = "999999"
        ok, reason = self.engine.verify_integrity()
        self.assertFalse(ok)
        self.assertIn("BYP_001", reason)

    def test_edited_verdict_is_detected(self):
        self.engine._entries[1].is_suspicious = False
        self.engine._entries[1].severity = SEVERITY_MEDIUM
        ok, _ = self.engine.verify_integrity()
        self.assertFalse(ok)

    def test_deleted_entry_is_detected(self):
        del self.engine._entries[0]
        del self.engine._log[0]
        ok, _ = self.engine.verify_integrity()
        self.assertFalse(ok)

    def test_report_flags_integrity_failure(self):
        self.engine._log[0].justification = "rewritten after the fact"
        report = self.engine.generate_audit_report()
        self.assertFalse(report.integrity_verified)
        self.assertIn("INTEGRITY FAILURE", report.audit_notes)

    def test_entries_are_defensive_copies(self):
        exported = self.engine.entries
        exported[0].severity = "TAMPERED"
        self.assertEqual(self.engine.entries[0].severity, SEVERITY_CRITICAL)
        self.assertTrue(self.engine.verify_integrity()[0])

    def test_exported_flag_reasons_are_not_shared_with_the_chain(self):
        """A copy that shares its list lets a caller mutate the trail."""
        exported = self.engine.entries[1]
        self.assertTrue(exported.flag_reasons)
        exported.flag_reasons.append("injected by the caller")
        self.assertNotIn("injected by the caller", self.engine.entries[1].flag_reasons)
        self.assertTrue(self.engine.verify_integrity()[0])

    def test_caller_mutating_its_own_event_does_not_alter_the_record(self):
        engine = RiskControlBypassAuditEngine()
        event = make_event()
        engine.log_bypass(event, recorded_at=RECORDED_AT)
        event.override_value = "999999"
        event.justification = "rewritten"
        self.assertTrue(engine.verify_integrity()[0])

    def test_report_entries_are_defensive_copies(self):
        report = self.engine.generate_audit_report()
        report.entries.clear()
        self.assertEqual(self.engine.generate_audit_report().total_bypass_events, 2)


class TestAuditReport(unittest.TestCase):

    def test_empty_report(self):
        report = RiskControlBypassAuditEngine().generate_audit_report()
        self.assertEqual(report.total_bypass_events, 0)
        self.assertEqual(report.status, STATUS_NO_BYPASSES)
        self.assertTrue(report.integrity_verified)
        self.assertEqual(report.chain_head_hash, "0" * 64)

    def test_clean_report_status(self):
        engine = RiskControlBypassAuditEngine()
        engine.log_bypass(make_event(), recorded_at=RECORDED_AT)
        report = engine.generate_audit_report()
        self.assertEqual(report.status, STATUS_BYPASSES_LOGGED)
        self.assertEqual(report.suspicious_count, 0)

    def test_report_severity_matches_log_time_verdict(self):
        """Regression: the report must not re-derive severity.

        A SPREAD_VETO bypass classified MEDIUM at log time was previously
        reported as HIGH, putting two verdicts for one event in one record.
        """
        engine = RiskControlBypassAuditEngine()
        logged = engine.log_bypass(
            make_event(bypassed_control="SPREAD_VETO"), recorded_at=RECORDED_AT)
        reported = engine.generate_audit_report().entries[0]
        self.assertEqual(logged.severity, SEVERITY_MEDIUM)
        self.assertEqual(reported.severity, logged.severity)
        self.assertEqual(reported.record_hash, logged.record_hash)

    def test_report_preserves_flag_reasons(self):
        """Regression: the report previously discarded why an event was flagged."""
        engine = RiskControlBypassAuditEngine()
        engine.log_bypass(
            make_event(authorized_by="unknown_user", justification=""),
            recorded_at=RECORDED_AT)
        reported = engine.generate_audit_report().entries[0]
        self.assertTrue(reported.is_suspicious)
        self.assertIsNotNone(reported.flag_reason)
        self.assertIn("Unauthorized principal", reported.flag_reason)

    def test_report_with_none_justification_does_not_raise(self):
        """Regression: a None justification previously crashed report generation."""
        engine = RiskControlBypassAuditEngine()
        engine.log_bypass(make_event(justification=None), recorded_at=RECORDED_AT)
        report = engine.generate_audit_report()
        self.assertEqual(report.status, STATUS_SUSPICIOUS_BYPASSES)
        self.assertEqual(report.suspicious_count, 1)

    def test_counts_and_status(self):
        engine = RiskControlBypassAuditEngine()
        engine.log_bypass(
            make_event(event_id="BYP_A", bypassed_control="SPREAD_VETO",
                       authorized_by="risk_officer",
                       justification="Approved for illiquid instrument exception."),
            recorded_at=RECORDED_AT)
        engine.log_bypass(
            make_event(event_id="BYP_B", bypassed_control="KILL_SWITCH",
                       original_limit_value="ON", override_value="OFF",
                       authorized_by="unknown_user", justification=""),
            recorded_at=RECORDED_AT)
        engine.log_bypass(
            make_event(event_id="BYP_C", bypassed_control="MAX_ORDER_VALUE"),
            recorded_at=RECORDED_AT)

        report = engine.generate_audit_report()
        self.assertEqual(report.total_bypass_events, 3)
        self.assertEqual(report.status, STATUS_SUSPICIOUS_BYPASSES)
        self.assertEqual(report.suspicious_count, 1)
        self.assertEqual(report.critical_count, 1)
        self.assertEqual(
            report.severity_counts,
            {SEVERITY_CRITICAL: 1, SEVERITY_HIGH: 1, SEVERITY_MEDIUM: 1})
        self.assertEqual([e.sequence_number for e in report.entries], [0, 1, 2])


class TestConcurrency(unittest.TestCase):

    def test_concurrent_logging_keeps_the_chain_consistent(self):
        engine = RiskControlBypassAuditEngine()
        errors = []

        def worker(worker_id: int) -> None:
            try:
                for i in range(50):
                    engine.log_bypass(
                        make_event(event_id=f"BYP_{worker_id}_{i}"),
                        recorded_at=RECORDED_AT)
            except Exception as exc:                      # pragma: no cover
                errors.append(exc)

        threads = [threading.Thread(target=worker, args=(w,)) for w in range(8)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        self.assertEqual(errors, [])
        report = engine.generate_audit_report()
        self.assertEqual(report.total_bypass_events, 400)
        self.assertTrue(report.integrity_verified)
        self.assertEqual([e.sequence_number for e in report.entries], list(range(400)))
        self.assertEqual(len({e.record_hash for e in report.entries}), 400)


if __name__ == "__main__":
    unittest.main()
