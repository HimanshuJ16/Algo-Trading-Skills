"""
Unit tests for post-breach-root-cause-analysis-template.

Expected values are derived independently of the implementation: timeline
ordering is asserted against a hand-written expected sequence, the JSON payload
is re-parsed with ``json.loads`` rather than compared to the module's own
string, and containment duration is computed from the literal datetimes.

Several tests are explicit regressions against the previous implementation;
each is annotated with the behavior it would have caught.
"""
import json
import logging
import unittest
from datetime import datetime, timedelta, timezone

from breach_rca_generator import (
    BreachIncidentSpec,
    BreachRcaGenerator,
    CapaItem,
    CapaType,
    RCAReport,
    Severity,
    TimelineEvent,
)

# Keep test output clean without globally disabling logging, which would break
# the assertLogs assertions below.
logging.getLogger("breach_rca_generator").addHandler(logging.NullHandler())
logging.getLogger("breach_rca_generator").propagate = False

UTC = timezone.utc
DETECTED = datetime(2026, 7, 31, 14, 5, 0, tzinfo=UTC)
CONTAINED = datetime(2026, 7, 31, 14, 5, 1, tzinfo=UTC)
GENERATED = datetime(2026, 8, 3, 9, 0, 0, tzinfo=UTC)

FIVE_WHYS = (
    "Order size exceeded the position limit.",
    "The pre-trade risk limit check was bypassed.",
    "The risk gateway configuration flag was toggled off.",
    "The staging deployment script lacked automated config validation.",
    "The CI/CD pipeline omitted pre-deployment risk config assertions.",
)


def capa(description, owner="risk-eng-lead", due=datetime(2026, 8, 14, tzinfo=UTC),
         capa_type=CapaType.CORRECTIVE):
    return CapaItem(description=description, owner=owner, due_date=due, capa_type=capa_type)


def build_spec(**overrides):
    """A complete, valid incident record. Override any field per test."""
    defaults = dict(
        incident_id="INC-2026-001",
        strategy_id="STAT_ARB_PROD",
        breach_type="POSITION_LIMIT_EXCEEDED",
        severity=Severity.CRITICAL,
        detected_at=DETECTED,
        contained_at=CONTAINED,
        financial_loss_usd=25000.0,
        unauthorized_turnover_usd=4_100_000.0,
        five_whys=list(FIVE_WHYS),
        timeline_events=[
            TimelineEvent(datetime(2026, 7, 31, 14, 0, 0, tzinfo=UTC),
                          "Deployment script ran.", "deploy-host-01"),
            TimelineEvent(DETECTED, "Limit breached by order #9921.", "oms-primary"),
            TimelineEvent(CONTAINED, "Kill switch engaged automatically.", "risk-gateway"),
        ],
        action_items=[
            capa("Add CI/CD pre-deployment assertion for risk gateway flags.",
                 capa_type=CapaType.PREVENTIVE),
            capa("Restore the risk gateway flag in production."),
        ],
        possible_rule_violation=False,
    )
    defaults.update(overrides)
    return BreachIncidentSpec(**defaults)


class TestHappyPath(unittest.TestCase):
    def setUp(self):
        self.engine = BreachRcaGenerator()
        self.report = self.engine.generate_rca_report(build_spec(), GENERATED)

    def test_status_and_validity(self):
        self.assertEqual(self.report.status, "RCA_GENERATED_SUCCESS")
        self.assertTrue(self.report.is_valid_rca)
        self.assertEqual(self.report.validation_findings, [])

    def test_report_counters(self):
        self.assertEqual(self.report.five_whys_depth, 5)
        self.assertEqual(self.report.action_item_count, 2)
        self.assertEqual(self.report.unassigned_action_items, 0)
        self.assertTrue(self.report.has_preventive_action)

    def test_containment_duration_computed_from_timestamps(self):
        # 14:05:01 - 14:05:00 = 1 second, derived from the literals above.
        self.assertEqual(self.report.containment_seconds, 1.0)

    def test_clock_sources_are_recorded_and_deduplicated(self):
        self.assertEqual(
            self.report.timeline_clock_sources,
            ["deploy-host-01", "oms-primary", "risk-gateway"],
        )

    def test_markdown_contains_every_required_section(self):
        doc = self.report.markdown_document
        for heading in (
            "# ROOT CAUSE ANALYSIS (RCA) REPORT: INC-2026-001",
            "## 1. Financial Impact",
            "## 2. Chronological Timeline (UTC)",
            "## 3. 5-Whys Analysis",
            "## 4. Corrective and Preventive Actions (CAPA)",
            "## 5. Rule-Violation Assessment",
            "## 6. Audit Findings",
        ):
            self.assertIn(heading, doc)

    def test_markdown_renders_capa_owner_and_due_date(self):
        # Regression: v1 rendered action items as bare "- [ ] <text>" with no
        # owner or due date, while the skill's own standard required both.
        self.assertIn(
            "- [ ] (PREVENTIVE) Add CI/CD pre-deployment assertion for risk "
            "gateway flags. -- owner: risk-eng-lead, due: 2026-08-14",
            self.report.markdown_document,
        )

    def test_markdown_reports_amounts_as_positive_magnitudes(self):
        self.assertIn("Realised loss: `$25,000.00`", self.report.markdown_document)
        self.assertIn("Unauthorised turnover: `$4,100,000.00`", self.report.markdown_document)


class TestTimelineOrdering(unittest.TestCase):
    """Regression: v1 documented "order events by timestamp" but emitted the
    caller's list order verbatim, so an out-of-order input produced a
    post-mortem whose chronology was wrong."""

    def setUp(self):
        self.engine = BreachRcaGenerator()

    def test_events_are_sorted_chronologically(self):
        shuffled = [
            TimelineEvent(datetime(2026, 7, 31, 14, 5, 1, tzinfo=UTC), "third", "c"),
            TimelineEvent(datetime(2026, 7, 31, 14, 0, 0, tzinfo=UTC), "first", "a"),
            TimelineEvent(datetime(2026, 7, 31, 14, 5, 0, tzinfo=UTC), "second", "b"),
        ]
        report = self.engine.generate_rca_report(
            build_spec(timeline_events=shuffled), GENERATED
        )
        descriptions = [e["description"] for e in json.loads(report.json_payload)["timeline"]]
        self.assertEqual(descriptions, ["first", "second", "third"])

    def test_non_utc_offsets_are_normalised_before_sorting(self):
        # 09:00 at UTC-5 is 14:00 UTC, which precedes 14:05 UTC. Sorting on the
        # naive wall-clock text would have put it last.
        events = [
            TimelineEvent(DETECTED, "later", "oms"),
            TimelineEvent(
                datetime(2026, 7, 31, 9, 0, 0, tzinfo=timezone(timedelta(hours=-5))),
                "earlier",
                "ny-host",
            ),
        ]
        report = self.engine.generate_rca_report(
            build_spec(timeline_events=events), GENERATED
        )
        timeline = json.loads(report.json_payload)["timeline"]
        self.assertEqual([e["description"] for e in timeline], ["earlier", "later"])
        self.assertEqual(timeline[0]["timestamp_utc"], "2026-07-31T14:00:00+00:00")

    def test_equal_timestamps_preserve_caller_order(self):
        same = datetime(2026, 7, 31, 14, 5, 0, tzinfo=UTC)
        events = [
            TimelineEvent(same, "alpha", "host-a"),
            TimelineEvent(same, "beta", "host-b"),
        ]
        report = self.engine.generate_rca_report(
            build_spec(timeline_events=events), GENERATED
        )
        timeline = json.loads(report.json_payload)["timeline"]
        self.assertEqual([e["description"] for e in timeline], ["alpha", "beta"])


class TestCompletenessGates(unittest.TestCase):
    def setUp(self):
        self.engine = BreachRcaGenerator()

    def test_insufficient_five_whys_depth(self):
        report = self.engine.generate_rca_report(
            build_spec(five_whys=["Order exceeded the limit.", "The check was off."]),
            GENERATED,
        )
        self.assertEqual(report.status, "INSUFFICIENT_5_WHYS_DEPTH")
        self.assertFalse(report.is_valid_rca)
        self.assertIn("INSUFFICIENT_5_WHYS_DEPTH", report.validation_findings)

    def test_exactly_min_depth_passes(self):
        report = self.engine.generate_rca_report(
            build_spec(five_whys=list(FIVE_WHYS[:3])), GENERATED
        )
        self.assertEqual(report.status, "RCA_GENERATED_SUCCESS")

    def test_configurable_min_depth(self):
        strict = BreachRcaGenerator(min_five_whys_depth=5)
        report = strict.generate_rca_report(
            build_spec(five_whys=list(FIVE_WHYS[:4])), GENERATED
        )
        self.assertEqual(report.status, "INSUFFICIENT_5_WHYS_DEPTH")

    def test_missing_action_items(self):
        report = self.engine.generate_rca_report(build_spec(action_items=[]), GENERATED)
        self.assertEqual(report.status, "MISSING_ACTION_ITEMS")
        self.assertFalse(report.is_valid_rca)

    def test_capa_without_owner_is_a_finding(self):
        # Regression: v1 accepted bare strings as action items, so the skill's
        # stated requirement for named owners and due dates was unenforceable.
        report = self.engine.generate_rca_report(
            build_spec(action_items=[CapaItem(description="Fix the gateway flag.")]),
            GENERATED,
        )
        self.assertEqual(report.status, "CAPA_MISSING_OWNER_OR_DUE_DATE")
        self.assertFalse(report.is_valid_rca)
        self.assertEqual(report.unassigned_action_items, 1)
        self.assertIn("**UNASSIGNED**", report.markdown_document)
        self.assertIn("**NO DUE DATE**", report.markdown_document)

    def test_capa_with_owner_but_no_due_date_is_a_finding(self):
        report = self.engine.generate_rca_report(
            build_spec(action_items=[CapaItem("Fix it.", owner="risk-eng-lead")]),
            GENERATED,
        )
        self.assertEqual(report.status, "CAPA_MISSING_OWNER_OR_DUE_DATE")

    def test_missing_timeline(self):
        report = self.engine.generate_rca_report(build_spec(timeline_events=[]), GENERATED)
        self.assertIn("MISSING_TIMELINE", report.validation_findings)
        self.assertFalse(report.is_valid_rca)
        self.assertIn("_No timeline events recorded._", report.markdown_document)

    def test_rule_violation_assessment_must_be_explicit(self):
        report = self.engine.generate_rca_report(
            build_spec(possible_rule_violation=None), GENERATED
        )
        self.assertIn("RULE_VIOLATION_ASSESSMENT_MISSING", report.validation_findings)
        self.assertFalse(report.is_valid_rca)
        self.assertIn("**NOT ASSESSED.**", report.markdown_document)

    def test_rule_violation_true_renders_escalation(self):
        report = self.engine.generate_rca_report(
            build_spec(possible_rule_violation=True), GENERATED
        )
        self.assertTrue(report.is_valid_rca)
        self.assertIn("Possible rule violation identified", report.markdown_document)

    def test_past_due_rca(self):
        due = GENERATED - timedelta(seconds=1)
        report = self.engine.generate_rca_report(build_spec(rca_due_by=due), GENERATED)
        self.assertIn("RCA_PAST_DUE", report.validation_findings)
        self.assertFalse(report.is_valid_rca)

    def test_exactly_on_deadline_is_not_past_due(self):
        report = self.engine.generate_rca_report(
            build_spec(rca_due_by=GENERATED), GENERATED
        )
        self.assertNotIn("RCA_PAST_DUE", report.validation_findings)
        self.assertTrue(report.is_valid_rca)

    def test_all_findings_reported_not_just_the_first(self):
        # Regression: v1 returned on the first failure, so a caller who fixed
        # the 5-Whys depth then discovered the missing CAPA items, one round
        # trip at a time.
        report = self.engine.generate_rca_report(
            build_spec(
                five_whys=["Only one why."],
                action_items=[],
                timeline_events=[],
                possible_rule_violation=None,
            ),
            GENERATED,
        )
        self.assertEqual(
            sorted(report.validation_findings),
            sorted([
                "INSUFFICIENT_5_WHYS_DEPTH",
                "MISSING_ACTION_ITEMS",
                "MISSING_TIMELINE",
                "RULE_VIOLATION_ASSESSMENT_MISSING",
            ]),
        )
        self.assertEqual(report.status, "INSUFFICIENT_5_WHYS_DEPTH")

    def test_incomplete_rca_still_renders_a_document(self):
        # Regression: v1 returned an empty markdown_document on any failure,
        # discarding the incident record the author had already written.
        report = self.engine.generate_rca_report(
            build_spec(five_whys=["Only one why."]), GENERATED
        )
        self.assertFalse(report.is_valid_rca)
        self.assertIn("INC-2026-001", report.markdown_document)
        self.assertIn("`INSUFFICIENT_5_WHYS_DEPTH` (BLOCKING)", report.markdown_document)


class TestBlameHeuristic(unittest.TestCase):
    def setUp(self):
        self.engine = BreachRcaGenerator()

    def test_terminal_blame_is_flagged_but_advisory(self):
        whys = list(FIVE_WHYS[:2]) + ["The deployment failed because of human error."]
        report = self.engine.generate_rca_report(build_spec(five_whys=whys), GENERATED)
        self.assertIn("TERMINAL_BLAME_ATTRIBUTION", report.validation_findings)
        self.assertTrue(report.is_valid_rca)
        self.assertEqual(report.status, "RCA_GENERATED_SUCCESS")
        self.assertIn("`TERMINAL_BLAME_ATTRIBUTION` (ADVISORY)", report.markdown_document)

    def test_blame_phrase_earlier_in_the_chain_is_not_flagged(self):
        whys = ["Human error triggered the deploy."] + list(FIVE_WHYS[3:5])
        report = self.engine.generate_rca_report(build_spec(five_whys=whys), GENERATED)
        self.assertNotIn("TERMINAL_BLAME_ATTRIBUTION", report.validation_findings)

    def test_match_is_case_insensitive(self):
        whys = list(FIVE_WHYS[:2]) + ["OPERATOR ERROR."]
        report = self.engine.generate_rca_report(build_spec(five_whys=whys), GENERATED)
        self.assertIn("TERMINAL_BLAME_ATTRIBUTION", report.validation_findings)

    def test_control_focused_root_cause_is_not_flagged(self):
        report = self.engine.generate_rca_report(build_spec(), GENERATED)
        self.assertNotIn("TERMINAL_BLAME_ATTRIBUTION", report.validation_findings)


class TestJsonPayload(unittest.TestCase):
    def setUp(self):
        self.engine = BreachRcaGenerator()
        self.payload = json.loads(
            self.engine.generate_rca_report(build_spec(), GENERATED).json_payload
        )

    def test_payload_is_valid_json_with_the_documented_fields(self):
        # Regression: v1's SKILL.md promised a "machine-readable JSON report
        # payload" that the implementation never produced.
        for key in (
            "incident_id", "strategy_id", "breach_type", "severity",
            "detected_at_utc", "contained_at_utc", "generated_at_utc",
            "containment_seconds", "financial_loss_usd",
            "unauthorized_turnover_usd", "five_whys", "timeline",
            "action_items", "possible_rule_violation", "rca_due_by_utc",
            "status", "validation_findings",
        ):
            self.assertIn(key, self.payload)

    def test_payload_timestamps_are_utc_iso8601(self):
        self.assertEqual(self.payload["detected_at_utc"], "2026-07-31T14:05:00+00:00")
        self.assertEqual(self.payload["contained_at_utc"], "2026-07-31T14:05:01+00:00")

    def test_payload_capa_items_carry_owner_and_due_date(self):
        item = self.payload["action_items"][0]
        self.assertEqual(item["owner"], "risk-eng-lead")
        self.assertEqual(item["due_date"], "2026-08-14")
        self.assertEqual(item["capa_type"], "PREVENTIVE")

    def test_generation_is_deterministic(self):
        # The same input must render byte-identical output: the module reads no
        # wall clock.
        a = self.engine.generate_rca_report(build_spec(), GENERATED)
        b = self.engine.generate_rca_report(build_spec(), GENERATED)
        self.assertEqual(a.json_payload, b.json_payload)
        self.assertEqual(a.markdown_document, b.markdown_document)


class TestStructuralValidation(unittest.TestCase):
    def setUp(self):
        self.engine = BreachRcaGenerator()

    def test_blank_incident_id_raises(self):
        with self.assertRaises(ValueError):
            build_spec(incident_id="   ")

    def test_blank_why_raises(self):
        # Regression: v1 counted len(five_whys), so ["", "", ""] satisfied the
        # depth gate with no analysis at all.
        with self.assertRaises(ValueError):
            build_spec(five_whys=["", "", ""])

    def test_bare_string_sequences_raise(self):
        # A str satisfies Sequence and iterates by character: five_whys="Human
        # error" would otherwise become an 11-level analysis of single letters
        # that clears the depth gate with no analysis in it at all.
        with self.assertRaises(ValueError):
            build_spec(five_whys="Human error")
        with self.assertRaises(ValueError):
            build_spec(timeline_events="event")
        with self.assertRaises(ValueError):
            build_spec(action_items="Fix the bug.")

    def test_non_iterable_sequences_raise(self):
        with self.assertRaises(ValueError):
            build_spec(five_whys=3)

    def test_negative_loss_raises(self):
        with self.assertRaises(ValueError):
            build_spec(financial_loss_usd=-25000.0)

    def test_non_finite_loss_raises(self):
        with self.assertRaises(ValueError):
            build_spec(financial_loss_usd=float("nan"))
        with self.assertRaises(ValueError):
            build_spec(unauthorized_turnover_usd=float("inf"))

    def test_zero_loss_is_allowed(self):
        report = self.engine.generate_rca_report(
            build_spec(financial_loss_usd=0.0, unauthorized_turnover_usd=0.0), GENERATED
        )
        self.assertTrue(report.is_valid_rca)
        self.assertIn("Realised loss: `$0.00`", report.markdown_document)

    def test_naive_datetime_raises(self):
        with self.assertRaises(ValueError):
            build_spec(detected_at=datetime(2026, 7, 31, 14, 5, 0))
        with self.assertRaises(ValueError):
            TimelineEvent(datetime(2026, 7, 31, 14, 0, 0), "no tz", "host")

    def test_naive_generated_at_raises(self):
        with self.assertRaises(ValueError):
            self.engine.generate_rca_report(build_spec(), datetime(2026, 8, 3, 9, 0, 0))

    def test_containment_before_detection_raises(self):
        with self.assertRaises(ValueError):
            build_spec(contained_at=DETECTED - timedelta(seconds=1))

    def test_containment_equal_to_detection_is_allowed(self):
        spec = build_spec(contained_at=DETECTED)
        self.assertEqual(spec.containment_seconds, 0.0)

    def test_severity_must_be_enum(self):
        with self.assertRaises(ValueError):
            build_spec(severity="CRITICAL")

    def test_timeline_events_must_be_typed(self):
        with self.assertRaises(ValueError):
            build_spec(timeline_events=[("14:00:00 UTC", "Deployment script ran.")])

    def test_action_items_must_be_typed(self):
        with self.assertRaises(ValueError):
            build_spec(action_items=["Fix the bug."])

    def test_possible_rule_violation_must_be_bool_or_none(self):
        with self.assertRaises(ValueError):
            build_spec(possible_rule_violation="yes")

    def test_spec_type_is_checked(self):
        with self.assertRaises(ValueError):
            self.engine.generate_rca_report({"incident_id": "INC-1"}, GENERATED)

    def test_invalid_min_depth_raises(self):
        with self.assertRaises(ValueError):
            BreachRcaGenerator(min_five_whys_depth=0)
        with self.assertRaises(ValueError):
            BreachRcaGenerator(min_five_whys_depth=True)

    def test_embedded_newlines_cannot_break_markdown_structure(self):
        # A description containing a newline would otherwise split one bullet
        # into two, silently inventing a timeline entry.
        event = TimelineEvent(DETECTED, "Limit breached\n- Kill switch engaged", "oms")
        self.assertEqual(event.description, "Limit breached - Kill switch engaged")
        report = self.engine.generate_rca_report(
            build_spec(timeline_events=[event]), GENERATED
        )
        timeline_block = report.markdown_document.split("## 2. Chronological Timeline (UTC)")[1]
        timeline_block = timeline_block.split("## 3.")[0]
        bullets = [ln for ln in timeline_block.splitlines() if ln.startswith("- ")]
        self.assertEqual(len(bullets), 1)


class TestLogging(unittest.TestCase):
    def test_incomplete_rca_logs_a_warning(self):
        engine = BreachRcaGenerator()
        with self.assertLogs("breach_rca_generator", level="WARNING") as ctx:
            engine.generate_rca_report(build_spec(action_items=[]), GENERATED)
        self.assertIn("MISSING_ACTION_ITEMS", "".join(ctx.output))

    def test_valid_rca_logs_info_not_warning(self):
        engine = BreachRcaGenerator()
        with self.assertLogs("breach_rca_generator", level="INFO") as ctx:
            engine.generate_rca_report(build_spec(), GENERATED)
        self.assertTrue(any("RCA generated for INC-2026-001" in m for m in ctx.output))
        self.assertFalse(any(r.levelname == "WARNING" for r in ctx.records))


class TestReportShape(unittest.TestCase):
    def test_report_is_the_documented_dataclass(self):
        report = BreachRcaGenerator().generate_rca_report(build_spec(), GENERATED)
        self.assertIsInstance(report, RCAReport)
        self.assertEqual(report.severity, "CRITICAL")
        self.assertEqual(report.incident_id, "INC-2026-001")


if __name__ == "__main__":
    unittest.main()
