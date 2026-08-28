"""Unit tests for the regulatory change monitoring assessment engine.

Fixtures use the real SEC T+1 rulemaking dates (Federal Register document
2023-03566: published 2023-03-06, effective 2023-05-05; compliance date
2024-05-28 per SEC press release 2023-29) so the effective-date/compliance-date
regression is anchored to a verifiable case rather than invented numbers.
"""
import logging
import unittest
from datetime import datetime, timedelta, timezone

from regulatory_change_monitoring_service_integration import (
    BASIS_COMPLIANCE_DATE,
    BASIS_EFFECTIVE_DATE,
    ComplianceResult,
    RegulatoryChangeMonitoringServiceIntegrationEngine,
    RegulatoryChangeReport,
    RegulatoryUpdate,
    STATUS_ACTION_REQUIRED,
    STATUS_COMPLIANT,
    STATUS_MONITORING,
    STATUS_OVERDUE,
)

# Silence the engine's own logging during assertion-heavy runs.
logging.getLogger(
    "regulatory_change_monitoring_service_integration"
).setLevel(logging.CRITICAL)


def make_update(**overrides) -> RegulatoryUpdate:
    """A monitored, action-required update with every field overridable."""
    base = dict(
        update_id="REG_SEC_2024_01",
        regulator="SEC",
        title="T+1 Settlement Rule Transition",
        effective_date="2024-05-28",
        impacted_subdomains=["SETTLEMENT", "CLEARING"],
        severity="CRITICAL",
        action_required=True,
        summary="Transition from T+2 to T+1 settlement cycle for US equities.",
    )
    base.update(overrides)
    return RegulatoryUpdate(**base)


class TestLegacyApi(unittest.TestCase):
    """The pre-existing ComplianceResult surface must keep working."""

    def setUp(self):
        self.engine = RegulatoryChangeMonitoringServiceIntegrationEngine(
            monitored_regulators=["SEC", "FCA", "SEBI"]
        )

    def test_legacy_valid(self):
        res = self.engine.check({"valid": True})
        self.assertIsInstance(res, ComplianceResult)
        self.assertTrue(res.is_compliant)

    def test_legacy_invalid(self):
        self.assertFalse(self.engine.check({"valid": False}).is_compliant)

    def test_legacy_edge(self):
        self.assertFalse(self.engine.check({}).is_compliant)


class TestFiltering(unittest.TestCase):

    def setUp(self):
        self.engine = RegulatoryChangeMonitoringServiceIntegrationEngine(
            monitored_regulators=["SEC", "FCA", "SEBI"]
        )

    def test_unmonitored_regulator_filtered_and_counted(self):
        """Filtering must be visible in the report, not a silent drop."""
        report = self.engine.process_updates(
            [make_update(
                update_id="REG_UNMON_01",
                regulator="UNMONITORED_REG",
                title="Irrelevant Notice",
                effective_date="2024-12-31",
                impacted_subdomains=["MISC"],
                severity="LOW",
                action_required=False,
            )],
            current_date_iso="2024-01-01",
        )
        self.assertEqual(report.overall_status, "NO_UPDATES")
        self.assertEqual(report.total_updates, 0)
        self.assertEqual(report.filtered_regulator_count, 1)
        self.assertEqual(report.filtered_regulators, ["UNMONITORED_REG"])
        self.assertIsNone(report.earliest_deadline_days)

    def test_regulator_match_is_case_and_whitespace_insensitive(self):
        report = self.engine.process_updates(
            [make_update(regulator="  sec  ")], current_date_iso="2024-05-01"
        )
        self.assertEqual(report.total_updates, 1)

    def test_empty_monitored_regulators_rejected(self):
        """`[]` must not silently fall back to the five default authorities."""
        with self.assertRaises(ValueError):
            RegulatoryChangeMonitoringServiceIntegrationEngine(monitored_regulators=[])

    def test_default_regulator_list_used_when_none(self):
        engine = RegulatoryChangeMonitoringServiceIntegrationEngine()
        self.assertEqual(
            engine.monitored_regulators, ["SEC", "FCA", "SEBI", "ESMA", "MAS"]
        )
        report = engine.process_updates(
            [make_update(regulator="MAS")], current_date_iso="2024-05-01"
        )
        self.assertEqual(report.total_updates, 1)

    def test_subdomain_filter_suppresses_off_topic_updates(self):
        engine = RegulatoryChangeMonitoringServiceIntegrationEngine(
            monitored_regulators=["FCA"], monitored_subdomains=["SETTLEMENT"]
        )
        report = engine.process_updates(
            [make_update(
                update_id="FCA_RETAIL_01",
                regulator="FCA",
                impacted_subdomains=["retail_banking"],
            )],
            current_date_iso="2024-05-01",
        )
        self.assertEqual(report.total_updates, 0)
        self.assertEqual(report.filtered_subdomain_count, 1)

    def test_subdomain_filter_matches_case_insensitively(self):
        engine = RegulatoryChangeMonitoringServiceIntegrationEngine(
            monitored_regulators=["SEC"], monitored_subdomains=["settlement"]
        )
        report = engine.process_updates(
            [make_update(impacted_subdomains=["Settlement"])],
            current_date_iso="2024-05-01",
        )
        self.assertEqual(report.total_updates, 1)

    def test_unclassified_update_survives_subdomain_filter(self):
        """Fail-open: an unclassified rule change must not vanish into a filter."""
        engine = RegulatoryChangeMonitoringServiceIntegrationEngine(
            monitored_regulators=["SEC"], monitored_subdomains=["SETTLEMENT"]
        )
        report = engine.process_updates(
            [make_update(impacted_subdomains=[])], current_date_iso="2024-05-01"
        )
        self.assertEqual(report.total_updates, 1)
        self.assertEqual(report.filtered_subdomain_count, 0)

    def test_empty_subdomain_filter_rejected(self):
        with self.assertRaises(ValueError):
            RegulatoryChangeMonitoringServiceIntegrationEngine(monitored_subdomains=[])


class TestDeadlineResolution(unittest.TestCase):

    def setUp(self):
        self.engine = RegulatoryChangeMonitoringServiceIntegrationEngine(
            monitored_regulators=["SEC", "FCA", "SEBI"]
        )

    def test_effective_date_used_when_no_compliance_date(self):
        report = self.engine.process_updates(
            [make_update()], current_date_iso="2024-05-01"
        )
        a = report.assessments[0]
        self.assertEqual(a.days_until_effective, 27)  # 2024-05-01 -> 2024-05-28
        self.assertEqual(a.deadline_basis, BASIS_EFFECTIVE_DATE)
        self.assertEqual(a.deadline_iso, "2024-05-28")
        self.assertEqual(report.overall_status, "ACTION_REQUIRED")
        self.assertEqual(report.critical_count, 1)
        self.assertEqual(report.action_required_count, 1)
        self.assertEqual(report.immediate_action_count, 1)
        self.assertTrue(a.requires_immediate_action)

    def test_compliance_date_overrides_effective_date(self):
        """Regression: the real T+1 dates.

        With only the feed's structured effective date (2023-05-05) the engine
        reported 30 days out on 2023-04-05 and nothing thereafter. The binding
        deadline was the compliance date, 2024-05-28 -- 419 days away.
        """
        update = make_update(effective_date="2023-05-05", compliance_date="2024-05-28")
        report = self.engine.process_updates([update], current_date_iso="2023-04-05")
        a = report.assessments[0]
        self.assertEqual(a.deadline_basis, BASIS_COMPLIANCE_DATE)
        self.assertEqual(a.deadline_iso, "2024-05-28")
        self.assertEqual(a.days_until_effective, 419)
        self.assertFalse(a.requires_immediate_action)
        self.assertEqual(a.status, STATUS_ACTION_REQUIRED)

    def test_compliance_date_drives_urgency_at_the_right_time(self):
        report = self.engine.process_updates(
            [make_update(effective_date="2023-05-05", compliance_date="2024-05-28")],
            current_date_iso="2024-05-01",
        )
        a = report.assessments[0]
        self.assertEqual(a.days_until_effective, 27)
        self.assertTrue(a.requires_immediate_action)

    def test_urgency_window_boundary(self):
        """Exactly at the window escalates; one day beyond it does not."""
        at_window = self.engine.process_updates(
            [make_update(effective_date="2024-05-31")], current_date_iso="2024-05-01"
        ).assessments[0]
        beyond = self.engine.process_updates(
            [make_update(effective_date="2024-06-01")], current_date_iso="2024-05-01"
        ).assessments[0]
        self.assertEqual(at_window.days_until_effective, 30)
        self.assertTrue(at_window.requires_immediate_action)
        self.assertEqual(beyond.days_until_effective, 31)
        self.assertFalse(beyond.requires_immediate_action)

    def test_custom_urgency_window(self):
        engine = RegulatoryChangeMonitoringServiceIntegrationEngine(
            monitored_regulators=["SEC"], urgent_action_window_days=90
        )
        a = engine.process_updates(
            [make_update(effective_date="2024-07-01")], current_date_iso="2024-05-01"
        ).assessments[0]
        self.assertEqual(a.days_until_effective, 61)
        self.assertTrue(a.requires_immediate_action)

    def test_negative_urgency_window_rejected(self):
        with self.assertRaises(ValueError):
            RegulatoryChangeMonitoringServiceIntegrationEngine(urgent_action_window_days=-1)

    def test_leap_day_deadline(self):
        a = self.engine.process_updates(
            [make_update(effective_date="2024-02-29")], current_date_iso="2024-02-01"
        ).assessments[0]
        self.assertEqual(a.days_until_effective, 28)

    def test_defaults_to_today_when_no_assessment_date(self):
        """The old hard-coded 2024-01-01 default silently mis-dated every report."""
        today = datetime.now(timezone.utc).date()
        deadline = (today + timedelta(days=10)).isoformat()
        report = self.engine.process_updates([make_update(effective_date=deadline)])
        self.assertEqual(report.evaluation_date_iso, today.isoformat())
        self.assertEqual(report.assessments[0].days_until_effective, 10)


class TestStatusClassification(unittest.TestCase):

    def setUp(self):
        self.engine = RegulatoryChangeMonitoringServiceIntegrationEngine(
            monitored_regulators=["SEC", "FCA", "SEBI"]
        )

    def test_overdue_open_item_escalates_regardless_of_severity(self):
        a = self.engine.process_updates(
            [make_update(severity="LOW", effective_date="2024-01-15")],
            current_date_iso="2024-05-01",
        ).assessments[0]
        self.assertEqual(a.days_until_effective, -107)
        self.assertTrue(a.is_overdue)
        self.assertTrue(a.requires_immediate_action)
        self.assertEqual(a.status, STATUS_OVERDUE)

    def test_overdue_counted_in_report(self):
        report = self.engine.process_updates(
            [make_update(effective_date="2024-01-15")], current_date_iso="2024-05-01"
        )
        self.assertEqual(report.overdue_count, 1)
        self.assertEqual(report.action_required_count, 1)
        self.assertEqual(report.overall_status, "ACTION_REQUIRED")

    def test_remediated_item_is_compliant_and_not_open(self):
        report = self.engine.process_updates(
            [make_update(effective_date="2024-01-15", remediation_complete=True)],
            current_date_iso="2024-05-01",
        )
        a = report.assessments[0]
        self.assertEqual(a.status, STATUS_COMPLIANT)
        self.assertFalse(a.is_overdue)
        self.assertFalse(a.requires_immediate_action)
        self.assertEqual(report.action_required_count, 0)
        self.assertEqual(report.overdue_count, 0)
        self.assertEqual(report.compliant_count, 1)
        self.assertEqual(report.overall_status, "MONITORING_ONLY")

    def test_informational_update_is_monitoring_only(self):
        report = self.engine.process_updates(
            [make_update(action_required=False, severity="HIGH")],
            current_date_iso="2024-05-01",
        )
        self.assertEqual(report.assessments[0].status, STATUS_MONITORING)
        self.assertFalse(report.assessments[0].requires_immediate_action)
        self.assertEqual(report.overall_status, "MONITORING_ONLY")

    def test_medium_severity_inside_window_is_not_immediate(self):
        a = self.engine.process_updates(
            [make_update(severity="MEDIUM")], current_date_iso="2024-05-01"
        ).assessments[0]
        self.assertEqual(a.status, STATUS_ACTION_REQUIRED)
        self.assertFalse(a.requires_immediate_action)


class TestSeverityHandling(unittest.TestCase):

    def setUp(self):
        self.engine = RegulatoryChangeMonitoringServiceIntegrationEngine(
            monitored_regulators=["SEC"]
        )

    def test_lowercase_severity_is_normalised_not_demoted(self):
        """Regression: case-sensitive matching dropped 'critical' out of the urgent band."""
        report = self.engine.process_updates(
            [make_update(severity="critical")], current_date_iso="2024-05-01"
        )
        self.assertEqual(report.assessments[0].severity, "CRITICAL")
        self.assertEqual(report.critical_count, 1)
        self.assertTrue(report.assessments[0].requires_immediate_action)

    def test_unknown_severity_raises(self):
        with self.assertRaises(ValueError):
            self.engine.process_updates(
                [make_update(severity="URGENT")], current_date_iso="2024-05-01"
            )

    def test_empty_severity_raises(self):
        with self.assertRaises(ValueError):
            self.engine.process_updates(
                [make_update(severity="")], current_date_iso="2024-05-01"
            )


class TestInputValidation(unittest.TestCase):

    def setUp(self):
        self.engine = RegulatoryChangeMonitoringServiceIntegrationEngine(
            monitored_regulators=["SEC"]
        )

    def test_malformed_effective_date_raises(self):
        """Regression: an unparseable date silently became 'effective in 30 days'."""
        with self.assertRaises(ValueError):
            self.engine.process_updates(
                [make_update(effective_date="28-05-2024")], current_date_iso="2024-05-01"
            )

    def test_impossible_date_raises(self):
        with self.assertRaises(ValueError):
            self.engine.process_updates(
                [make_update(effective_date="2024-02-30")], current_date_iso="2024-05-01"
            )

    def test_malformed_compliance_date_raises(self):
        with self.assertRaises(ValueError):
            self.engine.process_updates(
                [make_update(compliance_date="soon")], current_date_iso="2024-05-01"
            )

    def test_blank_compliance_date_raises(self):
        """An adapter emitting '' instead of None is a defect, not a fallback."""
        with self.assertRaises(ValueError):
            self.engine.process_updates(
                [make_update(compliance_date="")], current_date_iso="2024-05-01"
            )

    def test_effective_date_validated_even_when_overridden(self):
        with self.assertRaises(ValueError):
            self.engine.process_updates(
                [make_update(effective_date="not-a-date", compliance_date="2024-05-28")],
                current_date_iso="2024-05-01",
            )

    def test_malformed_assessment_date_raises(self):
        with self.assertRaises(ValueError):
            self.engine.process_updates([make_update()], current_date_iso="01/05/2024")

    def test_blank_update_id_raises(self):
        with self.assertRaises(ValueError):
            self.engine.process_updates([make_update(update_id="   ")])

    def test_blank_regulator_raises(self):
        with self.assertRaises(ValueError):
            self.engine.process_updates([make_update(regulator="")])

    def test_duplicate_update_id_raises(self):
        """Re-delivered feed records must be replaced upstream, not double-counted."""
        with self.assertRaises(ValueError):
            self.engine.process_updates(
                [make_update(), make_update(effective_date="2024-06-30")],
                current_date_iso="2024-05-01",
            )

    def test_non_update_object_raises(self):
        with self.assertRaises(ValueError):
            self.engine.process_updates([{"update_id": "X"}], current_date_iso="2024-05-01")

    def test_string_batch_raises(self):
        with self.assertRaises(ValueError):
            self.engine.process_updates("REG_SEC_2024_01", current_date_iso="2024-05-01")


class TestReportAggregation(unittest.TestCase):

    def setUp(self):
        self.engine = RegulatoryChangeMonitoringServiceIntegrationEngine(
            monitored_regulators=["SEC", "FCA", "SEBI"]
        )
        self.batch = [
            make_update(update_id="SEC_01", effective_date="2024-06-30", severity="MEDIUM"),
            make_update(
                update_id="FCA_01", regulator="FCA", effective_date="2024-05-10",
                severity="HIGH",
            ),
            make_update(
                update_id="SEBI_01", regulator="SEBI", effective_date="2024-04-01",
                severity="LOW",
            ),
            make_update(
                update_id="ESMA_01", regulator="ESMA", effective_date="2024-05-20",
                severity="CRITICAL",
            ),
        ]

    def test_counts_and_ordering(self):
        report = self.engine.process_updates(self.batch, current_date_iso="2024-05-01")
        self.assertIsInstance(report, RegulatoryChangeReport)
        self.assertEqual(report.total_updates, 3)          # ESMA is unmonitored here
        self.assertEqual(report.filtered_regulator_count, 1)
        self.assertEqual(report.critical_count, 0)
        self.assertEqual(report.action_required_count, 3)
        self.assertEqual(report.overdue_count, 1)          # SEBI_01
        self.assertEqual(report.immediate_action_count, 2)  # SEBI overdue + FCA HIGH in 9 days
        self.assertEqual(
            [a.update_id for a in report.assessments], ["SEBI_01", "FCA_01", "SEC_01"]
        )
        self.assertEqual(report.earliest_deadline_days, -30)
        self.assertEqual(report.evaluation_date_iso, "2024-05-01")

    def test_report_is_deterministic(self):
        first = self.engine.process_updates(self.batch, current_date_iso="2024-05-01")
        second = self.engine.process_updates(
            list(reversed(self.batch)), current_date_iso="2024-05-01"
        )
        self.assertEqual(
            [a.update_id for a in first.assessments],
            [a.update_id for a in second.assessments],
        )
        self.assertEqual(first.audit_notes, second.audit_notes)

    def test_empty_batch(self):
        report = self.engine.process_updates([], current_date_iso="2024-05-01")
        self.assertEqual(report.overall_status, "NO_UPDATES")
        self.assertEqual(report.total_updates, 0)
        self.assertIsNone(report.earliest_deadline_days)

    def test_audit_notes_record_the_assessment_date(self):
        report = self.engine.process_updates(self.batch, current_date_iso="2024-05-01")
        self.assertIn("2024-05-01", report.audit_notes)
        self.assertIn("ACTION_REQUIRED", report.audit_notes)


if __name__ == "__main__":
    unittest.main()
