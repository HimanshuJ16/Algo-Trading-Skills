import datetime
import unittest

from third_party_custody_audit_report_review_cadence import (
    AuditOpinion,
    AuditReport,
    CUECCheck,
    ComplianceStatus,
    CustodyAuditError,
    CustodyAuditReviewEngine,
    CustodyVendor,
    GapLetter,
    ReportType,
    RiskRating,
)


def _cuec(cuec_id="CUEC-1", implemented=True, evidence="Configured in Portal"):
    return CUECCheck(cuec_id, f"Control {cuec_id}", implemented, evidence)


class BaseCustodyTest(unittest.TestCase):
    """Shared fixture: one registered vendor with default annual cadence."""

    def setUp(self):
        self.engine = CustodyAuditReviewEngine()
        self.vendor = CustodyVendor(
            vendor_id="VEND-001",
            name="Coinbase Custody Trust Co",
            asset_classes_held=["BTC", "ETH", "SOL"],
            total_aum_usd=25_000_000.0,
            review_cadence_days=365,
        )
        self.engine.register_vendor(self.vendor)

    def soc_report(self, **overrides):
        """A clean SOC 1 Type II covering calendar 2025 with one required CUEC."""
        kwargs = dict(
            report_id="SOC1-2025",
            vendor_id="VEND-001",
            report_type=ReportType.SOC1_TYPE2,
            opinion=AuditOpinion.UNQUALIFIED,
            coverage_start=datetime.date(2025, 1, 1),
            coverage_end=datetime.date(2025, 12, 31),
            report_date=datetime.date(2026, 2, 15),
            cuecs_required=["CUEC-1"],
        )
        kwargs.update(overrides)
        return AuditReport(**kwargs)


class TestCoreEvaluation(BaseCustodyTest):
    def test_missing_audit_reports_triggers_critical_risk(self):
        res = self.engine.evaluate_vendor_compliance("VEND-001", datetime.date(2026, 8, 1))
        self.assertEqual(res.status, ComplianceStatus.NON_COMPLIANT)
        self.assertEqual(res.risk_rating, RiskRating.CRITICAL)
        self.assertIn("No audit reports on file.", res.findings)
        self.assertEqual(res.implemented_cuec_pct, 0.0)

    def test_clean_unqualified_soc_report_with_implemented_cuec(self):
        self.engine.submit_audit_report(self.soc_report())
        self.engine.update_cuec_checks("VEND-001", [_cuec("CUEC-1")])

        # 60 days after coverage end: inside the 90-day unbridged-gap allowance.
        res = self.engine.evaluate_vendor_compliance("VEND-001", datetime.date(2026, 3, 1))
        self.assertEqual(res.status, ComplianceStatus.COMPLIANT)
        self.assertEqual(res.risk_rating, RiskRating.LOW)
        self.assertEqual(res.findings, [])
        self.assertEqual(res.implemented_cuec_pct, 100.0)
        self.assertEqual(res.next_due_date, datetime.date(2026, 12, 31))

    def test_qualified_opinion_triggers_escalation(self):
        self.engine.submit_audit_report(
            self.soc_report(opinion=AuditOpinion.QUALIFIED, deficiencies_found=2)
        )
        self.engine.update_cuec_checks("VEND-001", [_cuec("CUEC-1")])

        res = self.engine.evaluate_vendor_compliance("VEND-001", datetime.date(2026, 6, 1))
        self.assertEqual(res.status, ComplianceStatus.ESCALATED)
        self.assertEqual(res.risk_rating, RiskRating.CRITICAL)

    def test_disclaimer_opinion_is_escalated_like_adverse(self):
        self.engine.submit_audit_report(self.soc_report(opinion=AuditOpinion.DISCLAIMER))
        self.engine.update_cuec_checks("VEND-001", [_cuec("CUEC-1")])

        res = self.engine.evaluate_vendor_compliance("VEND-001", datetime.date(2026, 6, 1))
        self.assertEqual(res.status, ComplianceStatus.ESCALATED)
        self.assertEqual(res.risk_rating, RiskRating.CRITICAL)

    def test_deficiencies_under_clean_opinion_raise_risk_to_high(self):
        # An unqualified opinion can still carry Section IV test exceptions.
        self.engine.submit_audit_report(self.soc_report(deficiencies_found=1))
        self.engine.update_cuec_checks("VEND-001", [_cuec("CUEC-1")])

        res = self.engine.evaluate_vendor_compliance("VEND-001", datetime.date(2026, 6, 1))
        self.assertEqual(res.status, ComplianceStatus.COMPLIANT)
        self.assertEqual(res.risk_rating, RiskRating.HIGH)

    def test_risk_rating_never_downgraded_by_a_later_check(self):
        # Qualified opinion (CRITICAL) evaluated together with a CUEC gap (MEDIUM
        # floor): the CRITICAL rating must survive.
        self.engine.submit_audit_report(self.soc_report(opinion=AuditOpinion.QUALIFIED))
        self.engine.update_cuec_checks("VEND-001", [_cuec("CUEC-1", implemented=False)])

        res = self.engine.evaluate_vendor_compliance("VEND-001", datetime.date(2026, 6, 1))
        self.assertEqual(res.risk_rating, RiskRating.CRITICAL)


class TestSocReportIsMandatory(BaseCustodyTest):
    def test_proof_of_reserves_alone_is_not_a_substitute_for_a_soc_report(self):
        # Regression: a PoR attestation used to be picked up as the "latest SOC
        # report" and could rate a custodian COMPLIANT/LOW. A PoR engagement is not
        # an audit (PCAOB Investor Advisory, 2023-03-08).
        self.engine.submit_audit_report(
            self.soc_report(
                report_id="POR-2026-Q2",
                report_type=ReportType.PROOF_OF_RESERVES,
                coverage_start=datetime.date(2026, 6, 30),
                coverage_end=datetime.date(2026, 6, 30),
                report_date=datetime.date(2026, 7, 5),
                cuecs_required=[],
            )
        )
        res = self.engine.evaluate_vendor_compliance("VEND-001", datetime.date(2026, 8, 1))
        self.assertEqual(res.status, ComplianceStatus.NON_COMPLIANT)
        self.assertEqual(res.risk_rating, RiskRating.CRITICAL)
        self.assertTrue(
            any("No SOC 1/SOC 2 Type II report on file" in f for f in res.findings),
            res.findings,
        )

    def test_iso27001_certificate_alone_is_not_a_substitute(self):
        self.engine.submit_audit_report(
            self.soc_report(report_id="ISO-2026", report_type=ReportType.ISO27001)
        )
        res = self.engine.evaluate_vendor_compliance("VEND-001", datetime.date(2026, 6, 1))
        self.assertEqual(res.status, ComplianceStatus.NON_COMPLIANT)
        self.assertEqual(res.risk_rating, RiskRating.CRITICAL)

    def test_soc2_is_preferred_over_a_newer_non_soc_artefact(self):
        self.engine.submit_audit_report(self.soc_report(report_type=ReportType.SOC2_TYPE2))
        self.engine.submit_audit_report(
            self.soc_report(
                report_id="POR-2026-Q2",
                report_type=ReportType.PROOF_OF_RESERVES,
                coverage_start=datetime.date(2026, 5, 15),
                coverage_end=datetime.date(2026, 5, 15),
                report_date=datetime.date(2026, 5, 20),
                cuecs_required=[],
            )
        )
        self.engine.update_cuec_checks("VEND-001", [_cuec("CUEC-1")])

        res = self.engine.evaluate_vendor_compliance("VEND-001", datetime.date(2026, 6, 1))
        self.assertEqual(res.status, ComplianceStatus.COMPLIANT)
        self.assertIn("SOC1-2025 (SOC2_TYPE2)", " ".join(res.audit_trail))


class TestMultipleSocTypes(BaseCustodyTest):
    def test_clean_soc2_does_not_mask_a_qualified_soc1(self):
        # SOC 1 and SOC 2 cover different control objectives; neither supersedes the
        # other. Picking only the report with the latest coverage end would hide a
        # qualified opinion behind a newer clean one.
        self.engine.submit_audit_report(
            self.soc_report(
                report_id="SOC1-2025",
                report_type=ReportType.SOC1_TYPE2,
                opinion=AuditOpinion.QUALIFIED,
                coverage_end=datetime.date(2025, 12, 30),
                report_date=datetime.date(2026, 2, 10),
            )
        )
        self.engine.submit_audit_report(
            self.soc_report(
                report_id="SOC2-2025",
                report_type=ReportType.SOC2_TYPE2,
                opinion=AuditOpinion.UNQUALIFIED,
                coverage_end=datetime.date(2025, 12, 31),
            )
        )
        self.engine.update_cuec_checks("VEND-001", [_cuec("CUEC-1")])

        res = self.engine.evaluate_vendor_compliance("VEND-001", datetime.date(2026, 6, 1))
        self.assertEqual(res.status, ComplianceStatus.ESCALATED)
        self.assertEqual(res.risk_rating, RiskRating.CRITICAL)
        self.assertTrue(
            any("SOC1_TYPE2 audit opinion is QUALIFIED" in f for f in res.findings),
            res.findings,
        )

    def test_deficiencies_are_counted_from_every_soc_type_held(self):
        self.engine.submit_audit_report(
            self.soc_report(
                report_id="SOC1-2025",
                report_type=ReportType.SOC1_TYPE2,
                deficiencies_found=3,
                coverage_end=datetime.date(2025, 12, 30),
                report_date=datetime.date(2026, 2, 10),
            )
        )
        self.engine.submit_audit_report(
            self.soc_report(report_id="SOC2-2025", report_type=ReportType.SOC2_TYPE2)
        )
        self.engine.update_cuec_checks("VEND-001", [_cuec("CUEC-1")])

        res = self.engine.evaluate_vendor_compliance("VEND-001", datetime.date(2026, 6, 1))
        self.assertEqual(res.risk_rating, RiskRating.HIGH)
        self.assertTrue(
            any("3 control deficiencies in SOC1-2025" in f for f in res.findings),
            res.findings,
        )

    def test_cuecs_from_both_soc_reports_must_be_implemented(self):
        self.engine.submit_audit_report(
            self.soc_report(
                report_id="SOC1-2025",
                report_type=ReportType.SOC1_TYPE2,
                cuecs_required=["CUEC-FIN"],
                coverage_end=datetime.date(2025, 12, 30),
                report_date=datetime.date(2026, 2, 10),
            )
        )
        self.engine.submit_audit_report(
            self.soc_report(
                report_id="SOC2-2025",
                report_type=ReportType.SOC2_TYPE2,
                cuecs_required=["CUEC-SEC"],
            )
        )
        self.engine.update_cuec_checks("VEND-001", [_cuec("CUEC-SEC")])

        res = self.engine.evaluate_vendor_compliance("VEND-001", datetime.date(2026, 6, 1))
        self.assertEqual(res.implemented_cuec_pct, 50.0)
        self.assertTrue(
            any("never assessed internally: CUEC-FIN" in f for f in res.findings),
            res.findings,
        )

    def test_superseded_report_of_the_same_type_is_not_re_evaluated(self):
        self.engine.submit_audit_report(
            self.soc_report(
                report_id="SOC1-2024",
                opinion=AuditOpinion.QUALIFIED,
                coverage_start=datetime.date(2024, 1, 1),
                coverage_end=datetime.date(2024, 12, 31),
                report_date=datetime.date(2025, 2, 15),
            )
        )
        self.engine.submit_audit_report(self.soc_report())
        self.engine.update_cuec_checks("VEND-001", [_cuec("CUEC-1")])

        res = self.engine.evaluate_vendor_compliance("VEND-001", datetime.date(2026, 3, 1))
        self.assertEqual(res.status, ComplianceStatus.COMPLIANT)
        self.assertEqual(res.risk_rating, RiskRating.LOW)


class TestCadenceAndGapLetters(BaseCustodyTest):
    EXPIRED_SOC = dict(
        report_id="SOC1-2024",
        coverage_start=datetime.date(2024, 1, 1),
        coverage_end=datetime.date(2024, 12, 31),
        report_date=datetime.date(2025, 2, 15),
    )

    def _submit_expired_soc(self):
        self.engine.submit_audit_report(self.soc_report(**self.EXPIRED_SOC))
        self.engine.update_cuec_checks("VEND-001", [_cuec("CUEC-1")])

    def test_expired_soc_report_without_gap_letter_is_overdue(self):
        self._submit_expired_soc()
        # 2026-08-01 is 578 days after the 2024-12-31 coverage end.
        res = self.engine.evaluate_vendor_compliance("VEND-001", datetime.date(2026, 8, 1))
        self.assertEqual(res.status, ComplianceStatus.OVERDUE)
        self.assertEqual(res.risk_rating, RiskRating.HIGH)

    def test_within_cadence_but_unbridged_gap_is_flagged_without_a_letter(self):
        # 213 days after coverage end: the report has not expired against the
        # 365-day cadence, but 213 unbridged days is well beyond the 90-day
        # bridging allowance and must not read as LOW.
        self._submit_expired_soc()
        res = self.engine.evaluate_vendor_compliance("VEND-001", datetime.date(2025, 8, 1))
        self.assertEqual(res.status, ComplianceStatus.COMPLIANT)
        self.assertEqual(res.risk_rating, RiskRating.MEDIUM)
        self.assertTrue(
            any("213 days since 2024-12-31" in f for f in res.findings), res.findings
        )

    def test_valid_gap_letter_closes_the_window_but_holds_risk_at_medium(self):
        # A bridge letter is management's unaudited assertion: it can close the
        # unbridged window, but never restores a LOW rating.
        self._submit_expired_soc()
        self.engine.submit_gap_letter(
            GapLetter(
                letter_id="GAP-2025-Q2",
                vendor_id="VEND-001",
                report_id="SOC1-2024",
                period_start=datetime.date(2025, 1, 1),
                period_end=datetime.date(2025, 6, 30),
                signed_date=datetime.date(2025, 7, 10),
            )
        )
        # 213 days past coverage end, but only 32 days past the bridged period.
        res = self.engine.evaluate_vendor_compliance("VEND-001", datetime.date(2025, 8, 1))
        self.assertEqual(res.status, ComplianceStatus.COMPLIANT)
        self.assertEqual(res.risk_rating, RiskRating.MEDIUM)
        self.assertTrue(
            any("no audit assurance" in f for f in res.findings), res.findings
        )
        self.assertFalse(
            any("covered by neither" in f for f in res.findings), res.findings
        )

    def test_a_bridge_letter_cannot_rescue_an_expired_report(self):
        # Regression: an 18-month "bridge" over a year-stale report used to clear
        # OVERDUE entirely. Bridging is a ~3-month device, not a substitute for a
        # report that has blown the review cadence.
        self._submit_expired_soc()
        self.engine.submit_gap_letter(
            GapLetter(
                letter_id="GAP-18-MONTH",
                vendor_id="VEND-001",
                report_id="SOC1-2024",
                period_start=datetime.date(2025, 1, 1),
                period_end=datetime.date(2026, 5, 31),
                signed_date=datetime.date(2026, 6, 10),
            )
        )
        res = self.engine.evaluate_vendor_compliance("VEND-001", datetime.date(2026, 6, 15))
        self.assertEqual(res.status, ComplianceStatus.OVERDUE)
        self.assertEqual(res.risk_rating, RiskRating.HIGH)
        self.assertTrue(
            any("cannot substitute for a report this stale" in f for f in res.findings),
            res.findings,
        )

    def _assert_letter_was_rejected(self, letter_id, reason_fragment):
        res = self.engine.evaluate_vendor_compliance("VEND-001", datetime.date(2025, 8, 1))
        self.assertEqual(res.risk_rating, RiskRating.MEDIUM)
        self.assertTrue(
            any("213 days since 2024-12-31" in f for f in res.findings),
            f"letter appears to have been accepted: {res.findings}",
        )
        self.assertTrue(
            any(
                f"Gap Letter {letter_id} rejected" in line and reason_fragment in line
                for line in res.audit_trail
            ),
            res.audit_trail,
        )

    def test_unsigned_gap_letter_is_rejected(self):
        self._submit_expired_soc()
        self.engine.submit_gap_letter(
            GapLetter(
                letter_id="GAP-UNSIGNED",
                vendor_id="VEND-001",
                report_id="SOC1-2024",
                period_start=datetime.date(2025, 1, 1),
                period_end=datetime.date(2025, 6, 30),
                signed_date=None,
            )
        )
        self._assert_letter_was_rejected("GAP-UNSIGNED", "is unsigned")

    def test_gap_letter_that_ran_out_leaves_an_unbridged_window(self):
        self._submit_expired_soc()
        self.engine.submit_gap_letter(
            GapLetter(
                letter_id="GAP-RAN-OUT",
                vendor_id="VEND-001",
                report_id="SOC1-2024",
                period_start=datetime.date(2025, 1, 1),
                period_end=datetime.date(2025, 3, 31),
                signed_date=datetime.date(2025, 4, 10),
            )
        )
        # The letter is valid but stopped bridging 123 days ago.
        res = self.engine.evaluate_vendor_compliance("VEND-001", datetime.date(2025, 8, 1))
        self.assertEqual(res.risk_rating, RiskRating.MEDIUM)
        self.assertTrue(
            any("123 days since 2025-03-31" in f for f in res.findings), res.findings
        )

    def test_unbridged_window_boundary_is_inclusive(self):
        self.engine.submit_audit_report(self.soc_report())
        self.engine.update_cuec_checks("VEND-001", [_cuec("CUEC-1")])

        exactly_90 = datetime.date(2025, 12, 31) + datetime.timedelta(days=90)
        at_limit = self.engine.evaluate_vendor_compliance("VEND-001", exactly_90)
        self.assertEqual(at_limit.risk_rating, RiskRating.LOW)
        self.assertEqual(at_limit.findings, [])

        past_limit = self.engine.evaluate_vendor_compliance(
            "VEND-001", exactly_90 + datetime.timedelta(days=1)
        )
        self.assertEqual(past_limit.risk_rating, RiskRating.MEDIUM)
        self.assertTrue(
            any("covered by neither" in f for f in past_limit.findings),
            past_limit.findings,
        )

    def test_gap_letter_for_a_different_report_is_rejected(self):
        self._submit_expired_soc()
        self.engine.submit_gap_letter(
            GapLetter(
                letter_id="GAP-OTHER",
                vendor_id="VEND-001",
                report_id="SOC1-2022",
                period_start=datetime.date(2025, 1, 1),
                period_end=datetime.date(2025, 6, 30),
                signed_date=datetime.date(2025, 7, 10),
            )
        )
        self._assert_letter_was_rejected("GAP-OTHER", "bridges report SOC1-2022")

    def test_gap_letter_leaving_an_uncovered_window_is_rejected(self):
        # Starts three months after coverage ended: Jan-Mar 2025 is unbridged, so
        # the letter cannot be treated as continuing the audited period.
        self._submit_expired_soc()
        self.engine.submit_gap_letter(
            GapLetter(
                letter_id="GAP-HOLE",
                vendor_id="VEND-001",
                report_id="SOC1-2024",
                period_start=datetime.date(2025, 4, 1),
                period_end=datetime.date(2025, 6, 30),
                signed_date=datetime.date(2025, 7, 10),
            )
        )
        self._assert_letter_was_rejected("GAP-HOLE", "uncovered window")

    def test_gap_letter_attesting_to_the_future_is_rejected(self):
        self._submit_expired_soc()
        self.engine.submit_gap_letter(
            GapLetter(
                letter_id="GAP-FUTURE",
                vendor_id="VEND-001",
                report_id="SOC1-2024",
                period_start=datetime.date(2025, 1, 1),
                period_end=datetime.date(2026, 6, 30),
                signed_date=datetime.date(2026, 7, 1),
            )
        )
        self._assert_letter_was_rejected("GAP-FUTURE", "future signature date")

    def test_gap_letter_asserting_material_changes_is_rejected(self):
        self._submit_expired_soc()
        self.engine.submit_gap_letter(
            GapLetter(
                letter_id="GAP-CHANGED",
                vendor_id="VEND-001",
                report_id="SOC1-2024",
                period_start=datetime.date(2025, 1, 1),
                period_end=datetime.date(2025, 6, 30),
                no_material_changes_asserted=False,
                signed_date=datetime.date(2025, 7, 10),
            )
        )
        self._assert_letter_was_rejected(
            "GAP-CHANGED", "does not assert absence of material control changes"
        )

    def test_gap_letter_signed_before_its_period_ended_is_rejected(self):
        self._submit_expired_soc()
        self.engine.submit_gap_letter(
            GapLetter(
                letter_id="GAP-PRESIGNED",
                vendor_id="VEND-001",
                report_id="SOC1-2024",
                period_start=datetime.date(2025, 1, 1),
                period_end=datetime.date(2025, 6, 30),
                signed_date=datetime.date(2025, 2, 1),
            )
        )
        self._assert_letter_was_rejected("GAP-PRESIGNED", "before the end of the period")

    def test_configured_review_cadence_is_honoured(self):
        # Regression: the staleness threshold was hard-coded to 365 days, so a
        # semi-annual cadence was silently never enforced.
        engine = CustodyAuditReviewEngine()
        engine.register_vendor(
            CustodyVendor(
                vendor_id="VEND-SEMI",
                name="Semi-annual Review Custodian",
                asset_classes_held=["USD"],
                total_aum_usd=1_000_000.0,
                review_cadence_days=180,
            )
        )
        engine.submit_audit_report(
            AuditReport(
                report_id="SOC1-2025",
                vendor_id="VEND-SEMI",
                report_type=ReportType.SOC1_TYPE2,
                opinion=AuditOpinion.UNQUALIFIED,
                coverage_start=datetime.date(2025, 1, 1),
                coverage_end=datetime.date(2025, 12, 31),
                report_date=datetime.date(2026, 2, 15),
                cuecs_required=["CUEC-1"],
            )
        )
        engine.update_cuec_checks("VEND-SEMI", [_cuec("CUEC-1")])

        # 152 days elapsed: inside the 180-day cadence.
        inside = engine.evaluate_vendor_compliance("VEND-SEMI", datetime.date(2026, 6, 1))
        self.assertEqual(inside.status, ComplianceStatus.COMPLIANT)
        # 213 days elapsed: outside it, and under a 365-day rule this was COMPLIANT.
        outside = engine.evaluate_vendor_compliance("VEND-SEMI", datetime.date(2026, 8, 1))
        self.assertEqual(outside.status, ComplianceStatus.OVERDUE)
        self.assertEqual(outside.next_due_date, datetime.date(2026, 6, 29))

    def test_cadence_boundary_is_exclusive(self):
        self._submit_expired_soc()
        exactly_365 = datetime.date(2024, 12, 31) + datetime.timedelta(days=365)
        self.assertEqual(
            self.engine.evaluate_vendor_compliance("VEND-001", exactly_365).status,
            ComplianceStatus.COMPLIANT,
        )
        self.assertEqual(
            self.engine.evaluate_vendor_compliance(
                "VEND-001", exactly_365 + datetime.timedelta(days=1)
            ).status,
            ComplianceStatus.OVERDUE,
        )

    def test_future_dated_coverage_end_is_flagged_not_silently_accepted(self):
        self.engine.submit_audit_report(self.soc_report())
        self.engine.update_cuec_checks("VEND-001", [_cuec("CUEC-1")])
        res = self.engine.evaluate_vendor_compliance("VEND-001", datetime.date(2025, 6, 1))
        self.assertEqual(res.risk_rating, RiskRating.HIGH)
        self.assertTrue(
            any("after the evaluation date" in f for f in res.findings), res.findings
        )


class TestObservationPeriod(BaseCustodyTest):
    def test_short_type2_observation_period_is_surfaced_as_a_finding(self):
        # Regression: a sub-policy observation period was logged at ingestion and
        # then never appeared in the ReviewResult the reviewer actually reads.
        self.engine.submit_audit_report(
            self.soc_report(
                coverage_start=datetime.date(2025, 11, 1),
                coverage_end=datetime.date(2025, 12, 31),
            )
        )
        self.engine.update_cuec_checks("VEND-001", [_cuec("CUEC-1")])

        res = self.engine.evaluate_vendor_compliance("VEND-001", datetime.date(2026, 6, 1))
        self.assertEqual(res.risk_rating, RiskRating.HIGH)
        self.assertTrue(
            any("observation period is 60 days" in f for f in res.findings), res.findings
        )

    def test_observation_period_threshold_is_configurable_firm_policy(self):
        engine = CustodyAuditReviewEngine()
        engine.register_vendor(
            CustodyVendor(
                vendor_id="V",
                name="Accepts 90-day Type II",
                asset_classes_held=["USD"],
                total_aum_usd=1.0,
                min_type2_coverage_days=90,
            )
        )
        engine.submit_audit_report(
            AuditReport(
                report_id="SOC2-Q4",
                vendor_id="V",
                report_type=ReportType.SOC2_TYPE2,
                opinion=AuditOpinion.UNQUALIFIED,
                coverage_start=datetime.date(2025, 10, 1),
                coverage_end=datetime.date(2025, 12, 31),
                report_date=datetime.date(2026, 1, 20),
                cuecs_required=["CUEC-1"],
            )
        )
        engine.update_cuec_checks("V", [_cuec("CUEC-1")])
        res = engine.evaluate_vendor_compliance("V", datetime.date(2026, 3, 1))
        self.assertEqual(res.risk_rating, RiskRating.LOW)
        self.assertEqual(res.findings, [])


class TestCUECEvaluation(BaseCustodyTest):
    def test_unimplemented_cuec_increases_risk_rating(self):
        self.engine.submit_audit_report(self.soc_report(cuecs_required=["CUEC-1", "CUEC-2"]))
        self.engine.update_cuec_checks(
            "VEND-001",
            [
                CUECCheck("CUEC-1", "Multi-user withdrawal signoff", True, "Configured in Portal"),
                CUECCheck("CUEC-2", "IP Whitelisting enforced", False, "Missing Evidence"),
            ],
        )

        res = self.engine.evaluate_vendor_compliance("VEND-001", datetime.date(2026, 6, 1))
        self.assertEqual(res.implemented_cuec_pct, 50.0)
        self.assertEqual(res.risk_rating, RiskRating.MEDIUM)
        self.assertIn("Unimplemented internal CUEC controls: CUEC-2", res.findings)

    def test_unassessed_cuecs_are_not_treated_as_implemented(self):
        # Regression: no CUEC records used to score 100% and rate the vendor LOW.
        self.engine.submit_audit_report(self.soc_report(cuecs_required=["CUEC-1", "CUEC-2"]))
        self.engine.update_cuec_checks("VEND-001", [_cuec("CUEC-1")])

        res = self.engine.evaluate_vendor_compliance("VEND-001", datetime.date(2026, 6, 1))
        self.assertEqual(res.implemented_cuec_pct, 50.0)
        self.assertEqual(res.risk_rating, RiskRating.MEDIUM)
        self.assertTrue(
            any("never assessed internally: CUEC-2" in f for f in res.findings), res.findings
        )

    def test_no_cuec_evidence_at_all_is_reported_as_unassessed(self):
        self.engine.submit_audit_report(self.soc_report(cuecs_required=[]))
        res = self.engine.evaluate_vendor_compliance("VEND-001", datetime.date(2026, 6, 1))
        self.assertEqual(res.implemented_cuec_pct, 0.0)
        self.assertEqual(res.risk_rating, RiskRating.MEDIUM)
        self.assertTrue(
            any("has not been assessed" in f for f in res.findings), res.findings
        )

    def test_cuec_marked_implemented_without_evidence_does_not_count(self):
        self.engine.submit_audit_report(self.soc_report(cuecs_required=["CUEC-1"]))
        self.engine.update_cuec_checks("VEND-001", [_cuec("CUEC-1", evidence="   ")])

        res = self.engine.evaluate_vendor_compliance("VEND-001", datetime.date(2026, 6, 1))
        self.assertEqual(res.implemented_cuec_pct, 0.0)
        self.assertEqual(res.risk_rating, RiskRating.MEDIUM)
        self.assertTrue(
            any("without verification evidence" in f for f in res.findings), res.findings
        )

    def test_checks_recorded_beyond_the_reports_cuec_list_still_count(self):
        self.engine.submit_audit_report(self.soc_report(cuecs_required=["CUEC-1"]))
        self.engine.update_cuec_checks("VEND-001", [_cuec("CUEC-1"), _cuec("CUEC-EXTRA")])

        res = self.engine.evaluate_vendor_compliance("VEND-001", datetime.date(2026, 3, 1))
        self.assertEqual(res.implemented_cuec_pct, 100.0)
        self.assertEqual(res.risk_rating, RiskRating.LOW)

    def test_duplicate_cuec_ids_are_rejected(self):
        with self.assertRaises(CustodyAuditError):
            self.engine.update_cuec_checks(
                "VEND-001", [_cuec("CUEC-1"), _cuec("CUEC-1", implemented=False)]
            )


class TestProofOfReserves(BaseCustodyTest):
    def _submit_soc_and_cuec(self):
        self.engine.submit_audit_report(self.soc_report())
        self.engine.update_cuec_checks("VEND-001", [_cuec("CUEC-1")])

    def _submit_por(self, as_of):
        self.engine.submit_audit_report(
            AuditReport(
                report_id=f"POR-{as_of.isoformat()}",
                vendor_id="VEND-001",
                report_type=ReportType.PROOF_OF_RESERVES,
                opinion=AuditOpinion.UNQUALIFIED,
                coverage_start=as_of,
                coverage_end=as_of,
                report_date=as_of,
            )
        )

    def test_stale_proof_of_reserves_raises_risk_to_medium(self):
        # Regression: por_cadence_days was stored and never evaluated.
        self._submit_soc_and_cuec()
        self._submit_por(datetime.date(2026, 1, 31))
        res = self.engine.evaluate_vendor_compliance("VEND-001", datetime.date(2026, 6, 1))
        self.assertEqual(res.risk_rating, RiskRating.MEDIUM)
        self.assertTrue(
            any("Proof of Reserves attestation is 121 days old" in f for f in res.findings),
            res.findings,
        )

    def test_fresh_proof_of_reserves_produces_no_finding(self):
        self._submit_soc_and_cuec()
        self._submit_por(datetime.date(2026, 2, 15))
        res = self.engine.evaluate_vendor_compliance("VEND-001", datetime.date(2026, 3, 1))
        self.assertEqual(res.risk_rating, RiskRating.LOW)
        self.assertEqual(res.findings, [])

    def test_required_but_absent_proof_of_reserves_is_a_finding(self):
        engine = CustodyAuditReviewEngine()
        engine.register_vendor(
            CustodyVendor(
                vendor_id="V",
                name="Crypto Custodian",
                asset_classes_held=["BTC"],
                total_aum_usd=1.0,
                requires_proof_of_reserves=True,
            )
        )
        engine.submit_audit_report(
            AuditReport(
                report_id="SOC2-2025",
                vendor_id="V",
                report_type=ReportType.SOC2_TYPE2,
                opinion=AuditOpinion.UNQUALIFIED,
                coverage_start=datetime.date(2025, 1, 1),
                coverage_end=datetime.date(2025, 12, 31),
                report_date=datetime.date(2026, 2, 15),
                cuecs_required=["CUEC-1"],
            )
        )
        engine.update_cuec_checks("V", [_cuec("CUEC-1")])
        res = engine.evaluate_vendor_compliance("V", datetime.date(2026, 6, 1))
        self.assertEqual(res.risk_rating, RiskRating.MEDIUM)
        self.assertTrue(
            any("No Proof of Reserves attestation on file" in f for f in res.findings),
            res.findings,
        )


class TestIngestionValidation(BaseCustodyTest):
    def test_reregistering_a_vendor_does_not_silently_discard_evidence(self):
        self.engine.submit_audit_report(self.soc_report())
        with self.assertRaises(CustodyAuditError):
            self.engine.register_vendor(self.vendor)
        self.assertEqual(len(self.engine.audit_reports["VEND-001"]), 1)

        self.engine.register_vendor(self.vendor, replace=True)
        self.assertEqual(self.engine.audit_reports["VEND-001"], [])

    def test_duplicate_report_id_is_rejected(self):
        self.engine.submit_audit_report(self.soc_report())
        with self.assertRaises(CustodyAuditError):
            self.engine.submit_audit_report(self.soc_report())

    def test_duplicate_gap_letter_id_is_rejected(self):
        letter = GapLetter(
            letter_id="GAP-1",
            vendor_id="VEND-001",
            report_id="SOC1-2025",
            period_start=datetime.date(2026, 1, 1),
            period_end=datetime.date(2026, 3, 31),
            signed_date=datetime.date(2026, 4, 1),
        )
        self.engine.submit_gap_letter(letter)
        with self.assertRaises(CustodyAuditError):
            self.engine.submit_gap_letter(letter)

    def test_unregistered_vendor_is_rejected_everywhere(self):
        with self.assertRaises(CustodyAuditError):
            self.engine.evaluate_vendor_compliance("NOPE", datetime.date(2026, 6, 1))
        with self.assertRaises(CustodyAuditError):
            self.engine.update_cuec_checks("NOPE", [])
        with self.assertRaises(CustodyAuditError):
            self.engine.record_review("NOPE", datetime.date(2026, 6, 1))
        with self.assertRaises(CustodyAuditError):
            self.engine.submit_audit_report(self.soc_report(vendor_id="NOPE"))

    def test_inverted_report_coverage_dates_are_rejected(self):
        with self.assertRaises(CustodyAuditError):
            self.soc_report(
                coverage_start=datetime.date(2025, 12, 31),
                coverage_end=datetime.date(2025, 1, 1),
            )

    def test_report_dated_before_its_coverage_ends_is_rejected(self):
        with self.assertRaises(CustodyAuditError):
            self.soc_report(report_date=datetime.date(2025, 6, 1))

    def test_negative_deficiency_count_is_rejected(self):
        with self.assertRaises(CustodyAuditError):
            self.soc_report(deficiencies_found=-1)

    def test_invalid_vendor_configuration_is_rejected(self):
        for kwargs in (
            {"review_cadence_days": 0},
            {"por_cadence_days": -30},
            {"total_aum_usd": -1.0},
            {"total_aum_usd": float("nan")},
            {"max_unbridged_gap_days": -1},
            {"vendor_id": "  "},
        ):
            with self.subTest(**kwargs):
                base = dict(
                    vendor_id="V",
                    name="X",
                    asset_classes_held=["USD"],
                    total_aum_usd=1.0,
                )
                base.update(kwargs)
                with self.assertRaises(CustodyAuditError):
                    CustodyVendor(**base)

    def test_datetime_is_rejected_where_a_date_is_required(self):
        # datetime is a subclass of date but will not subtract against one; without
        # this guard an accidental datetime.now() fails later with an opaque
        # TypeError deep inside the cadence arithmetic.
        self.engine.submit_audit_report(self.soc_report())
        with self.assertRaises(CustodyAuditError):
            self.engine.evaluate_vendor_compliance(
                "VEND-001", datetime.datetime(2026, 6, 1, 9, 30)
            )
        with self.assertRaises(CustodyAuditError):
            self.soc_report(
                report_id="SOC1-DT", report_date=datetime.datetime(2026, 2, 15, 12, 0)
            )

    def test_inverted_gap_letter_period_is_rejected(self):
        with self.assertRaises(CustodyAuditError):
            GapLetter(
                letter_id="G",
                vendor_id="VEND-001",
                report_id="SOC1-2025",
                period_start=datetime.date(2026, 3, 31),
                period_end=datetime.date(2026, 1, 1),
            )


class TestPortfolioViews(BaseCustodyTest):
    def test_evaluation_is_side_effect_free(self):
        # Regression: get_overdue_vendors() used to stamp last_reviews as a side
        # effect, so merely listing overdue vendors rewrote the review history.
        self.engine.submit_audit_report(self.soc_report())
        self.engine.get_overdue_vendors(datetime.date(2026, 6, 1))
        self.engine.evaluate_vendor_compliance("VEND-001", datetime.date(2026, 6, 1))
        self.assertEqual(self.engine.last_reviews, {})

        self.engine.record_review("VEND-001", datetime.date(2026, 6, 1))
        self.assertEqual(
            self.engine.last_reviews, {"VEND-001": datetime.date(2026, 6, 1)}
        )

    def test_overdue_and_escalated_vendors_are_reported_separately(self):
        self.engine.submit_audit_report(
            self.soc_report(
                report_id="SOC1-2024",
                coverage_start=datetime.date(2024, 1, 1),
                coverage_end=datetime.date(2024, 12, 31),
                report_date=datetime.date(2025, 2, 15),
            )
        )
        self.engine.register_vendor(
            CustodyVendor(
                vendor_id="VEND-002",
                name="Qualified Opinion Custodian",
                asset_classes_held=["USD"],
                total_aum_usd=5_000_000.0,
            )
        )
        self.engine.submit_audit_report(
            AuditReport(
                report_id="SOC2-2025",
                vendor_id="VEND-002",
                report_type=ReportType.SOC2_TYPE2,
                opinion=AuditOpinion.QUALIFIED,
                coverage_start=datetime.date(2025, 1, 1),
                coverage_end=datetime.date(2025, 12, 31),
                report_date=datetime.date(2026, 2, 15),
                cuecs_required=["CUEC-1"],
            )
        )
        self.engine.update_cuec_checks("VEND-002", [_cuec("CUEC-1")])

        as_of = datetime.date(2026, 8, 1)
        overdue_ids = [v.vendor_id for v in self.engine.get_overdue_vendors(as_of)]
        escalated_ids = [
            r.vendor_id for r in self.engine.get_vendors_requiring_escalation(as_of)
        ]
        self.assertEqual(overdue_ids, ["VEND-001"])
        self.assertEqual(escalated_ids, ["VEND-002"])
        self.assertEqual(len(self.engine.evaluate_all_vendors(as_of)), 2)

    def test_vendor_with_no_reports_appears_in_both_views(self):
        as_of = datetime.date(2026, 8, 1)
        self.assertEqual(
            [v.vendor_id for v in self.engine.get_overdue_vendors(as_of)], ["VEND-001"]
        )
        self.assertEqual(
            [r.vendor_id for r in self.engine.get_vendors_requiring_escalation(as_of)],
            ["VEND-001"],
        )


if __name__ == "__main__":
    unittest.main()
