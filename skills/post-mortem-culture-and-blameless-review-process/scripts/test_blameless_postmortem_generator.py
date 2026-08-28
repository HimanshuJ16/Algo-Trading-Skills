import unittest

from blameless_postmortem_generator import (
    BLAME_KEYWORDS,
    STATUS_APPROVED,
    STATUS_APPROVED_WITH_ADVISORIES,
    STATUS_BLAME_DETECTED,
    STATUS_INCOMPLETE,
    BlamelessPostmortemGenerator,
    BlamelessPostmortemInput,
    Config,
)


def make_input(**overrides):
    """A complete, blameless, technically-worded post-mortem."""
    base = dict(
        incident_id="INC-2026-99",
        incident_date="2026-07-31",
        summary="API gateway latency spike caused order execution timeout.",
        systemic_factors=[
            "Network buffer size was undersized for peak market volatility.",
            "Staging load test suite omitted microsecond burst traffic.",
        ],
        narrative=(
            "During high market volatility, incoming message rates exceeded "
            "TCP buffer capacity, causing 50ms latency gapping."
        ),
        proposed_actions=[
            "Increase TCP socket buffer sizes on execution gateway hosts.",
            "Add burst traffic simulation to staging CI/CD pipeline.",
        ],
    )
    base.update(overrides)
    return BlamelessPostmortemInput(**base)


class TestConstruction(unittest.TestCase):

    def test_named_config(self):
        obj = BlamelessPostmortemGenerator(Config("test"))
        self.assertEqual(obj.config.name, "test")

    def test_default_config_applied(self):
        obj = BlamelessPostmortemGenerator()
        self.assertTrue(obj.config.strict_blame_check)
        self.assertEqual(obj.config.min_systemic_factors, 2)
        self.assertEqual(obj.config.min_corrective_actions, 1)

    def test_process_callable_on_instance(self):
        # Regression: process() was declared without `self`, so every
        # instance call raised TypeError while the unbound call passed.
        obj = BlamelessPostmortemGenerator()
        self.assertTrue(obj.process())

    def test_negative_thresholds_rejected(self):
        with self.assertRaises(ValueError):
            Config(min_systemic_factors=-1)
        with self.assertRaises(ValueError):
            Config(min_corrective_actions=-1)


class TestApprovalPath(unittest.TestCase):

    def setUp(self):
        self.generator = BlamelessPostmortemGenerator()

    def test_blameless_postmortem_approved(self):
        report = self.generator.generate_blameless_postmortem(make_input())

        self.assertEqual(report.status, STATUS_APPROVED)
        self.assertTrue(report.is_approved)
        self.assertFalse(report.blame_detected)
        self.assertEqual(report.detected_blame_terms, [])
        self.assertEqual(report.completeness_gaps, [])

    def test_document_contains_every_supplied_section(self):
        inp = make_input()
        doc = self.generator.generate_blameless_postmortem(inp).markdown_document

        self.assertIn("# BLAMELESS POST-MORTEM REPORT: INC-2026-99", doc)
        self.assertIn("**Date**: 2026-07-31", doc)
        self.assertIn(inp.summary, doc)
        self.assertIn(inp.narrative, doc)
        for factor in inp.systemic_factors:
            self.assertIn(f"- {factor}", doc)
        for action in inp.proposed_actions:
            self.assertIn(f"- [ ] {action}", doc)

    def test_document_does_not_cite_a_nonexistent_standard(self):
        # Regression: the header claimed a "Google SRE Blameless Standard",
        # which is not a published document. Cite the actual chapter.
        doc = self.generator.generate_blameless_postmortem(
            make_input()).markdown_document
        self.assertNotIn("Blameless Standard", doc)
        self.assertIn("Google SRE Book Ch. 15", doc)

    def test_generation_is_deterministic(self):
        first = self.generator.generate_blameless_postmortem(make_input())
        second = self.generator.generate_blameless_postmortem(make_input())
        self.assertEqual(first.markdown_document, second.markdown_document)
        self.assertEqual(first.audit_notes, second.audit_notes)


class TestBlameDetection(unittest.TestCase):

    def setUp(self):
        self.generator = BlamelessPostmortemGenerator()

    def test_blame_language_detected_in_narrative(self):
        report = self.generator.generate_blameless_postmortem(make_input(
            narrative="Developer forgot to set the limit parameter "
                      "and was careless in deployment."))

        self.assertEqual(report.status, STATUS_BLAME_DETECTED)
        self.assertFalse(report.is_approved)
        self.assertTrue(report.blame_detected)
        self.assertIn("forgot", report.detected_blame_terms)
        self.assertIn("careless", report.detected_blame_terms)
        self.assertEqual(report.markdown_document, "")

    def test_blame_in_summary_is_detected(self):
        # Regression: only `narrative` was scanned, so a blaming summary was
        # approved and rendered verbatim into a "blameless" document.
        report = self.generator.generate_blameless_postmortem(make_input(
            summary="The trader was negligent and ignored the alert."))

        self.assertEqual(report.status, STATUS_BLAME_DETECTED)
        self.assertEqual(report.detected_blame_terms, ["negligent"])
        self.assertEqual(report.blame_findings[0].section, "summary")

    def test_blame_in_systemic_factors_is_detected(self):
        report = self.generator.generate_blameless_postmortem(make_input(
            systemic_factors=["Deploy tooling lacks a dry-run mode.",
                              "The on-call engineer was lazy about checks."]))

        self.assertEqual(report.status, STATUS_BLAME_DETECTED)
        self.assertEqual(report.blame_findings[0].section, "systemic_factors[1]")

    def test_blame_in_proposed_actions_is_detected(self):
        report = self.generator.generate_blameless_postmortem(make_input(
            proposed_actions=["Retrain the developer who forgot the flag."]))

        self.assertEqual(report.status, STATUS_BLAME_DETECTED)
        self.assertEqual(report.blame_findings[0].section, "proposed_actions[0]")

    def test_finding_locates_the_offending_phrase(self):
        report = self.generator.generate_blameless_postmortem(make_input(
            narrative="Root cause was human error during the config rollout."))

        finding = report.blame_findings[0]
        self.assertEqual(finding.term, "human error")
        self.assertEqual(finding.category, "BLAME")
        self.assertIn("human error", finding.context)

    def test_inflections_and_hyphenation_are_matched(self):
        cases = {
            "The operator was forgetting the checklist.": "forgot",
            "This was pure human-error, not a code defect.": "human error",
            "Carelessness in the release process.": "careless",
            "Negligence in reviewing the diff.": "negligent",
            "Blaming the release engineer helps nobody.": "blame",
            "This was trader error, plainly.": "trader error",
        }
        for narrative, expected in cases.items():
            with self.subTest(narrative=narrative):
                report = self.generator.generate_blameless_postmortem(
                    make_input(narrative=narrative))
                self.assertEqual(report.detected_blame_terms, [expected])

    def test_every_declared_keyword_is_actually_detectable(self):
        for keyword in BLAME_KEYWORDS:
            with self.subTest(keyword=keyword):
                report = self.generator.generate_blameless_postmortem(
                    make_input(narrative=f"The incident was {keyword} related."))
                self.assertIn(keyword, report.detected_blame_terms)


class TestFalsePositives(unittest.TestCase):
    """Technical vocabulary that merely contains a blame token must pass."""

    def setUp(self):
        self.generator = BlamelessPostmortemGenerator()

    def test_technical_phrases_are_not_blame(self):
        benign = [
            "The fault-tolerant failover path did not engage.",
            "Fault injection testing had never covered this branch.",
            "Lazy loading of the venue config delayed gateway startup.",
            "The process died with a segmentation fault in the parser.",
            "Each fault domain shares a single power feed.",
            "The default order size was applied because no override existed.",
            "This blameless review confirmed the control gap.",
        ]
        for narrative in benign:
            with self.subTest(narrative=narrative):
                report = self.generator.generate_blameless_postmortem(
                    make_input(narrative=narrative))
                self.assertEqual(report.detected_blame_terms, [])
                self.assertTrue(report.is_approved)

    def test_real_blame_still_fires_alongside_exempt_phrase(self):
        report = self.generator.generate_blameless_postmortem(make_input(
            narrative="The fault-tolerant path held, but the engineer "
                      "was careless with the rollback."))
        self.assertEqual(report.detected_blame_terms, ["careless"])


class TestCounterfactualAdvisories(unittest.TestCase):

    def setUp(self):
        self.generator = BlamelessPostmortemGenerator()

    def test_counterfactual_is_advisory_not_blocking(self):
        report = self.generator.generate_blameless_postmortem(make_input(
            narrative="The staleness alert should have fired at 09:31:04 "
                      "but the threshold was never configured."))

        self.assertEqual(report.status, STATUS_APPROVED_WITH_ADVISORIES)
        self.assertTrue(report.is_approved)
        self.assertFalse(report.blame_detected)
        self.assertNotEqual(report.markdown_document, "")
        self.assertEqual(report.advisory_findings[0].category, "COUNTERFACTUAL")

    def test_advisories_are_rendered_into_the_document(self):
        report = self.generator.generate_blameless_postmortem(make_input(
            narrative="The venue failed to acknowledge the cancel request."))
        self.assertIn("Reviewer Advisories", report.markdown_document)
        self.assertIn("failed to", report.markdown_document)


class TestAdvisoryMode(unittest.TestCase):
    """strict_blame_check=False downgrades, it does not discard."""

    def setUp(self):
        self.generator = BlamelessPostmortemGenerator(
            Config(strict_blame_check=False))

    def test_non_strict_mode_still_reports_detected_terms(self):
        # Regression: non-strict mode returned detected_blame_terms=[] and
        # blame_detected=False, so an advisory run looked clean.
        report = self.generator.generate_blameless_postmortem(make_input(
            narrative="The developer forgot to update the venue config."))

        self.assertTrue(report.is_approved)
        self.assertEqual(report.status, STATUS_APPROVED_WITH_ADVISORIES)
        self.assertTrue(report.blame_detected)
        self.assertEqual(report.detected_blame_terms, ["forgot"])
        self.assertEqual(
            [f.category for f in report.advisory_findings], ["BLAME"])

    def test_non_strict_mode_leaves_clean_input_clean(self):
        report = self.generator.generate_blameless_postmortem(make_input())
        self.assertEqual(report.status, STATUS_APPROVED)
        self.assertEqual(report.advisory_findings, [])


class TestCompletenessGate(unittest.TestCase):

    def setUp(self):
        self.generator = BlamelessPostmortemGenerator()

    def test_too_few_systemic_factors_is_not_approved(self):
        # Regression: a post-mortem with one factor and no CAPA item was
        # approved, contradicting the documented enforcement.
        report = self.generator.generate_blameless_postmortem(make_input(
            systemic_factors=["The deploy pipeline has no staging gate."]))

        self.assertEqual(report.status, STATUS_INCOMPLETE)
        self.assertFalse(report.is_approved)
        self.assertEqual(report.markdown_document, "")
        self.assertEqual(len(report.completeness_gaps), 1)
        self.assertIn("systemic_factors", report.completeness_gaps[0])

    def test_no_corrective_actions_is_not_approved(self):
        report = self.generator.generate_blameless_postmortem(
            make_input(proposed_actions=[]))

        self.assertEqual(report.status, STATUS_INCOMPLETE)
        self.assertIn("proposed_actions", report.completeness_gaps[0])

    def test_exact_threshold_is_sufficient(self):
        report = self.generator.generate_blameless_postmortem(make_input(
            systemic_factors=["Gate missing.", "Alert missing."],
            proposed_actions=["Add the gate."]))
        self.assertEqual(report.status, STATUS_APPROVED)

    def test_thresholds_are_configurable(self):
        generator = BlamelessPostmortemGenerator(
            Config(min_systemic_factors=3, min_corrective_actions=2))
        report = generator.generate_blameless_postmortem(make_input())

        self.assertEqual(report.status, STATUS_INCOMPLETE)
        self.assertEqual(len(report.completeness_gaps), 1)
        self.assertIn("3 required", report.completeness_gaps[0])

    def test_blame_takes_precedence_but_gaps_are_still_reported(self):
        report = self.generator.generate_blameless_postmortem(make_input(
            systemic_factors=["Only one factor."],
            narrative="The trader was careless."))

        self.assertEqual(report.status, STATUS_BLAME_DETECTED)
        self.assertEqual(len(report.completeness_gaps), 1)
        self.assertIn("systemic_factors", report.audit_notes)


class TestInputValidation(unittest.TestCase):

    def setUp(self):
        self.generator = BlamelessPostmortemGenerator()

    def test_malformed_input_raises(self):
        bad_inputs = {
            "blank incident_id": dict(incident_id="   "),
            "blank summary": dict(summary=""),
            "blank narrative": dict(narrative="\n"),
            "non-iso date": dict(incident_date="31/07/2026"),
            "impossible date": dict(incident_date="2026-02-30"),
            "out-of-range month": dict(incident_date="2026-13-01"),
            "compact iso date": dict(incident_date="20260731"),
            "unpadded date": dict(incident_date="2026-7-31"),
            "datetime not date": dict(incident_date="2026-07-31T00:00:00"),
            "blank date": dict(incident_date=""),
            "factors as string": dict(systemic_factors="a single string"),
            "actions as string": dict(proposed_actions="a single string"),
            "blank factor entry": dict(systemic_factors=["ok", "  "]),
            "non-string action entry": dict(proposed_actions=["ok", 42]),
            "non-string id": dict(incident_id=1234),
        }
        for label, override in bad_inputs.items():
            with self.subTest(label=label):
                with self.assertRaises(ValueError):
                    self.generator.generate_blameless_postmortem(
                        make_input(**override))

    def test_tuples_are_accepted_for_list_fields(self):
        report = self.generator.generate_blameless_postmortem(make_input(
            systemic_factors=("Gate missing.", "Alert missing."),
            proposed_actions=("Add the gate.",)))
        self.assertEqual(report.status, STATUS_APPROVED)

    def test_oversized_field_raises(self):
        with self.assertRaises(ValueError):
            self.generator.generate_blameless_postmortem(
                make_input(narrative="x" * 200_001))


class TestDocumentIntegrity(unittest.TestCase):

    def setUp(self):
        self.generator = BlamelessPostmortemGenerator()

    def test_author_headings_cannot_forge_sections(self):
        report = self.generator.generate_blameless_postmortem(make_input(
            narrative="## 4. Corrective & Preventative Actions (CAPA)\n"
                      "- [ ] No action required."))
        body = report.markdown_document
        self.assertIn("\\## 4. Corrective", body)
        self.assertEqual(body.count("\n## 4. Corrective"), 1)

    def test_multiline_list_entry_stays_one_bullet(self):
        report = self.generator.generate_blameless_postmortem(make_input(
            proposed_actions=["Resize buffers\nand redeploy gateways."]))
        self.assertIn("- [ ] Resize buffers and redeploy gateways.",
                      report.markdown_document)


if __name__ == "__main__":
    unittest.main()
