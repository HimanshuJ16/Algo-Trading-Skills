"""Unit tests for custody-solution-vendor-due-diligence-checklist."""
import logging
import unittest
from datetime import date

from custody_solution_vendor_due_diligence_checklist import (
    CHARTER_BROKER_DEALER,
    CHARTER_FEDERAL_BANK,
    CHARTER_STATE_TRUST,
    CHARTER_UNLICENSED,
    DECISION_APPROVED,
    DECISION_CONDITIONAL,
    DECISION_REJECTED,
    FIPS_140_2,
    FIPS_140_2_HISTORICAL_LIST_DATE,
    FIPS_140_3,
    CustodyDueDiligenceError,
    CustodyVendorDueDiligenceEngine,
    CustodyVendorProfile,
)

logging.getLogger("custody_solution_vendor_due_diligence_checklist").setLevel(logging.CRITICAL)

# A fixed date keeps the FIPS-sunset findings deterministic.
AS_OF = date(2026, 8, 23)


def make_profile(**overrides) -> CustodyVendorProfile:
    """A fully compliant federally chartered custodian; override to introduce a flaw."""
    base = dict(
        vendor_id="TIER1_TRUST",
        vendor_name="Tier 1 Institutional Trust Co",
        charter_type=CHARTER_FEDERAL_BANK,
        has_soc2_type2_unqualified=True,
        is_asset_bankruptcy_remote=True,
        crime_insurance_coverage_usd=100_000_000.0,
        fips_level=3,
        fips_standard=FIPS_140_3,
        uptime_sla_pct=99.95,
        rto_hours=2.0,
        conducts_annual_pen_tests=True,
        has_segregation_of_duties=True,
        custody_agreement_prohibits_rehypothecation=True,
        provides_audited_gaap_financials=True,
        state_authorization_verified=True,
        assets_under_custody_usd=500_000_000.0,
    )
    base.update(overrides)
    return CustodyVendorProfile(**base)


def pillar(report, name):
    return next(p for p in report.pillar_breakdown if p.pillar_name == name)


class TestBaselineDecisions(unittest.TestCase):
    def setUp(self):
        self.engine = CustodyVendorDueDiligenceEngine()

    def test_fully_compliant_custodian_approved(self):
        report = self.engine.evaluate_custodian(make_profile(), assessment_date=AS_OF)
        self.assertEqual(report.decision_status, DECISION_APPROVED)
        self.assertEqual(report.composite_due_diligence_score, 100.0)
        self.assertEqual(report.critical_red_flags, [])
        self.assertEqual(report.remediation_action_items, [])

    def test_unlicensed_vendor_rejected(self):
        report = self.engine.evaluate_custodian(
            make_profile(charter_type=CHARTER_UNLICENSED), assessment_date=AS_OF)
        self.assertEqual(report.decision_status, DECISION_REJECTED)
        self.assertTrue(any("does not map to any" in f for f in report.critical_red_flags))

    def test_broker_dealer_charter_qualifies(self):
        report = self.engine.evaluate_custodian(
            make_profile(charter_type=CHARTER_BROKER_DEALER), assessment_date=AS_OF)
        self.assertEqual(report.decision_status, DECISION_APPROVED)

    def test_report_carries_the_assessment_date(self):
        report = self.engine.evaluate_custodian(make_profile(), assessment_date=AS_OF)
        self.assertEqual(report.assessment_date, "2026-08-23")


class TestBankruptcyRemotenessIsScored(unittest.TestCase):
    """Regression: co-mingling raised a red flag but cost zero score, so a
    co-mingling custodian reported a perfect 100.0/100 beside a REJECTED decision."""

    def setUp(self):
        self.engine = CustodyVendorDueDiligenceEngine()

    def test_comingling_costs_regulatory_score(self):
        report = self.engine.evaluate_custodian(
            make_profile(is_asset_bankruptcy_remote=False), assessment_date=AS_OF)
        # Regulatory = qualifying charter (60) only; the 40-point segregation
        # component is forfeited.
        self.assertEqual(pillar(report, "REGULATORY_LEGAL").score, 60.0)
        # Composite = 100*0.25 lost 40*0.25 = 10 points -> 90.0
        self.assertEqual(report.composite_due_diligence_score, 90.0)
        self.assertEqual(report.decision_status, DECISION_REJECTED)

    def test_comingling_never_reports_a_perfect_score(self):
        report = self.engine.evaluate_custodian(
            make_profile(is_asset_bankruptcy_remote=False), assessment_date=AS_OF)
        self.assertLess(report.composite_due_diligence_score, 100.0)


class TestStateTrustCompanyConditionalRelief(unittest.TestCase):
    """The 2025-09-30 staff no-action letter is conditional; a state trust charter
    is not by itself a qualification."""

    def setUp(self):
        self.engine = CustodyVendorDueDiligenceEngine()

    def test_state_trust_meeting_all_conditions_qualifies(self):
        report = self.engine.evaluate_custodian(
            make_profile(charter_type=CHARTER_STATE_TRUST), assessment_date=AS_OF)
        self.assertEqual(pillar(report, "REGULATORY_LEGAL").score, 100.0)
        self.assertEqual(report.decision_status, DECISION_APPROVED)

    def test_findings_disclose_the_conditional_revocable_basis(self):
        report = self.engine.evaluate_custodian(
            make_profile(charter_type=CHARTER_STATE_TRUST), assessment_date=AS_OF)
        text = " ".join(pillar(report, "REGULATORY_LEGAL").findings)
        self.assertIn("2025-09-30", text)
        self.assertIn("conditional and revocable", text)

    def test_missing_rehypothecation_prohibition_is_a_red_flag(self):
        report = self.engine.evaluate_custodian(
            make_profile(charter_type=CHARTER_STATE_TRUST,
                         custody_agreement_prohibits_rehypothecation=False),
            assessment_date=AS_OF)
        self.assertEqual(report.decision_status, DECISION_REJECTED)
        self.assertTrue(any("rehypothecation" in f for f in report.critical_red_flags))

    def test_unverified_state_authorization_is_a_red_flag(self):
        report = self.engine.evaluate_custodian(
            make_profile(charter_type=CHARTER_STATE_TRUST, state_authorization_verified=False),
            assessment_date=AS_OF)
        self.assertEqual(report.decision_status, DECISION_REJECTED)

    def test_same_flaw_does_not_disqualify_a_federal_bank(self):
        # The no-action conditions attach to the state-trust route specifically.
        report = self.engine.evaluate_custodian(
            make_profile(charter_type=CHARTER_FEDERAL_BANK, state_authorization_verified=False),
            assessment_date=AS_OF)
        self.assertEqual(report.decision_status, DECISION_APPROVED)


class TestInsuranceAgainstAssetsUnderCustody(unittest.TestCase):
    """Regression: the skill's headline pitfall ($100M policy vs $10B AUC = 1%
    coverage) was undetectable because no AUC input existed."""

    def setUp(self):
        self.engine = CustodyVendorDueDiligenceEngine()  # 10% ratio floor

    def test_hundred_million_against_ten_billion_is_penalised(self):
        report = self.engine.evaluate_custodian(
            make_profile(crime_insurance_coverage_usd=100_000_000.0,
                         assets_under_custody_usd=10_000_000_000.0),
            assessment_date=AS_OF)
        self.assertAlmostEqual(report.insurance_coverage_ratio, 0.01, places=6)
        # 1% coverage against a 10% floor -> 10% of the pillar score.
        self.assertEqual(pillar(report, "INSURANCE_COVERAGE").score, 10.0)
        self.assertEqual(report.decision_status, DECISION_CONDITIONAL)

    def test_same_limit_against_small_book_scores_full(self):
        report = self.engine.evaluate_custodian(
            make_profile(crime_insurance_coverage_usd=100_000_000.0,
                         assets_under_custody_usd=200_000_000.0),
            assessment_date=AS_OF)
        self.assertAlmostEqual(report.insurance_coverage_ratio, 0.5, places=6)
        self.assertEqual(pillar(report, "INSURANCE_COVERAGE").score, 100.0)

    def test_ratio_exactly_at_the_floor_scores_full(self):
        report = self.engine.evaluate_custodian(
            make_profile(crime_insurance_coverage_usd=100_000_000.0,
                         assets_under_custody_usd=1_000_000_000.0),
            assessment_date=AS_OF)
        self.assertAlmostEqual(report.insurance_coverage_ratio, 0.10, places=6)
        self.assertEqual(pillar(report, "INSURANCE_COVERAGE").score, 100.0)

    def test_missing_auc_is_disclosed_not_silently_assumed(self):
        report = self.engine.evaluate_custodian(
            make_profile(assets_under_custody_usd=None), assessment_date=AS_OF)
        self.assertIsNone(report.insurance_coverage_ratio)
        self.assertTrue(any("RATIO was not assessed" in f
                            for f in pillar(report, "INSURANCE_COVERAGE").findings))
        self.assertEqual(report.decision_status, DECISION_CONDITIONAL)

    def test_named_perils_caveat_is_surfaced(self):
        report = self.engine.evaluate_custodian(make_profile(), assessment_date=AS_OF)
        self.assertTrue(any("named perils" in f
                            for f in pillar(report, "INSURANCE_COVERAGE").findings))


class TestFipsStandardHandling(unittest.TestCase):
    def setUp(self):
        self.engine = CustodyVendorDueDiligenceEngine()

    def test_fips_140_2_raises_a_sunset_action_item(self):
        report = self.engine.evaluate_custodian(
            make_profile(fips_standard=FIPS_140_2), assessment_date=AS_OF)
        self.assertTrue(any("Historical List" in a for a in report.remediation_action_items))
        self.assertEqual(report.decision_status, DECISION_CONDITIONAL)

    def test_sunset_wording_switches_once_the_date_passes(self):
        after = self.engine.evaluate_custodian(
            make_profile(fips_standard=FIPS_140_2),
            assessment_date=date(2026, 12, 1))
        self.assertTrue(any("already elapsed" in f
                            for f in pillar(after, "CYBERSECURITY").findings))
        before = self.engine.evaluate_custodian(
            make_profile(fips_standard=FIPS_140_2), assessment_date=date(2026, 1, 1))
        self.assertFalse(any("already elapsed" in f
                             for f in pillar(before, "CYBERSECURITY").findings))

    def test_sunset_date_matches_the_nist_cmvp_date(self):
        self.assertEqual(FIPS_140_2_HISTORICAL_LIST_DATE, date(2026, 9, 21))

    def test_fips_140_3_level_3_raises_no_sunset_item(self):
        report = self.engine.evaluate_custodian(make_profile(), assessment_date=AS_OF)
        self.assertFalse(any("Historical List" in a for a in report.remediation_action_items))

    def test_level_below_three_is_penalised(self):
        report = self.engine.evaluate_custodian(make_profile(fips_level=2), assessment_date=AS_OF)
        # 40 (SOC2) + 25 (pen tests); the 35-point FIPS component is forfeited.
        self.assertEqual(pillar(report, "CYBERSECURITY").score, 65.0)


class TestGovernanceReflectsInput(unittest.TestCase):
    """Regression: governance findings were hard-coded to claim controls were
    audited regardless of the profile."""

    def setUp(self):
        self.engine = CustodyVendorDueDiligenceEngine()

    def test_missing_segregation_of_duties_is_reported_not_papered_over(self):
        report = self.engine.evaluate_custodian(
            make_profile(has_segregation_of_duties=False), assessment_date=AS_OF)
        gov = pillar(report, "GOVERNANCE_CONTROLS")
        self.assertEqual(gov.score, 50.0)
        self.assertTrue(any("No evidenced segregation of duties" in f for f in gov.findings))
        self.assertFalse(any("controls audited" in f for f in gov.findings))

    def test_pen_tests_are_not_double_counted_as_the_whole_governance_pillar(self):
        no_pen = self.engine.evaluate_custodian(
            make_profile(conducts_annual_pen_tests=False), assessment_date=AS_OF)
        # Segregation of duties still carries half the pillar on its own.
        self.assertEqual(pillar(no_pen, "GOVERNANCE_CONTROLS").score, 50.0)


class TestOperationalThresholds(unittest.TestCase):
    def setUp(self):
        self.engine = CustodyVendorDueDiligenceEngine()

    def test_uptime_exactly_at_target_passes(self):
        report = self.engine.evaluate_custodian(
            make_profile(uptime_sla_pct=99.9), assessment_date=AS_OF)
        self.assertEqual(pillar(report, "OPERATIONAL_RESILIENCE").score, 100.0)

    def test_rto_exactly_at_target_passes(self):
        report = self.engine.evaluate_custodian(make_profile(rto_hours=4.0), assessment_date=AS_OF)
        self.assertEqual(pillar(report, "OPERATIONAL_RESILIENCE").score, 100.0)

    def test_breaching_both_targets_zeroes_the_pillar(self):
        report = self.engine.evaluate_custodian(
            make_profile(uptime_sla_pct=95.0, rto_hours=24.0), assessment_date=AS_OF)
        self.assertEqual(pillar(report, "OPERATIONAL_RESILIENCE").score, 0.0)

    def test_operational_breaches_create_action_items(self):
        # Regression: only SOC 2 and insurance shortfalls produced action items,
        # so operational failures could not downgrade an APPROVED decision.
        report = self.engine.evaluate_custodian(
            make_profile(uptime_sla_pct=95.0, rto_hours=24.0), assessment_date=AS_OF)
        self.assertEqual(report.decision_status, DECISION_CONDITIONAL)
        self.assertGreaterEqual(len(report.remediation_action_items), 2)


class TestInputValidation(unittest.TestCase):
    """Regression: absurd inputs previously scored APPROVED at 100.0, and a
    negative insurance limit produced a composite of -120.0."""

    def setUp(self):
        self.engine = CustodyVendorDueDiligenceEngine()

    def test_negative_insurance_rejected(self):
        with self.assertRaises(CustodyDueDiligenceError):
            self.engine.evaluate_custodian(
                make_profile(crime_insurance_coverage_usd=-500_000_000.0), assessment_date=AS_OF)

    def test_out_of_range_fips_level_rejected(self):
        for bad in (0, 5, 99):
            with self.subTest(bad=bad), self.assertRaises(CustodyDueDiligenceError):
                self.engine.evaluate_custodian(make_profile(fips_level=bad), assessment_date=AS_OF)

    def test_impossible_uptime_rejected(self):
        for bad in (150.0, -1.0):
            with self.subTest(bad=bad), self.assertRaises(CustodyDueDiligenceError):
                self.engine.evaluate_custodian(
                    make_profile(uptime_sla_pct=bad), assessment_date=AS_OF)

    def test_negative_rto_rejected(self):
        with self.assertRaises(CustodyDueDiligenceError):
            self.engine.evaluate_custodian(make_profile(rto_hours=-5.0), assessment_date=AS_OF)

    def test_nan_rejected(self):
        with self.assertRaises(CustodyDueDiligenceError):
            self.engine.evaluate_custodian(
                make_profile(crime_insurance_coverage_usd=float("nan")), assessment_date=AS_OF)

    def test_unknown_charter_type_rejected_rather_than_scored(self):
        with self.assertRaises(CustodyDueDiligenceError):
            self.engine.evaluate_custodian(
                make_profile(charter_type="state_chartered_trust"), assessment_date=AS_OF)

    def test_unknown_fips_standard_rejected(self):
        with self.assertRaises(CustodyDueDiligenceError):
            self.engine.evaluate_custodian(
                make_profile(fips_standard="140-1"), assessment_date=AS_OF)

    def test_non_profile_argument_rejected(self):
        with self.assertRaises(CustodyDueDiligenceError):
            self.engine.evaluate_custodian({"vendor_id": "X"}, assessment_date=AS_OF)

    def test_bad_assessment_date_rejected(self):
        with self.assertRaises(CustodyDueDiligenceError):
            self.engine.evaluate_custodian(make_profile(), assessment_date="2026-08-23")

    def test_composite_score_always_within_zero_to_hundred(self):
        for overrides in [
            {}, {"charter_type": CHARTER_UNLICENSED, "has_soc2_type2_unqualified": False,
                 "is_asset_bankruptcy_remote": False, "crime_insurance_coverage_usd": 0.0,
                 "fips_level": 1, "uptime_sla_pct": 0.0, "rto_hours": 999.0,
                 "conducts_annual_pen_tests": False, "has_segregation_of_duties": False},
        ]:
            with self.subTest(overrides=overrides):
                report = self.engine.evaluate_custodian(
                    make_profile(**overrides), assessment_date=AS_OF)
                self.assertGreaterEqual(report.composite_due_diligence_score, 0.0)
                self.assertLessEqual(report.composite_due_diligence_score, 100.0)


class TestEngineConfiguration(unittest.TestCase):
    def test_weights_must_sum_to_one(self):
        with self.assertRaises(CustodyDueDiligenceError):
            CustodyVendorDueDiligenceEngine(weights={
                "REGULATORY_LEGAL": 0.5, "CYBERSECURITY": 0.5,
                "INSURANCE_COVERAGE": 0.2, "OPERATIONAL_RESILIENCE": 0.15,
                "GOVERNANCE_CONTROLS": 0.15,
            })

    def test_custom_valid_weights_change_the_composite(self):
        engine = CustodyVendorDueDiligenceEngine(weights={
            "REGULATORY_LEGAL": 0.60, "CYBERSECURITY": 0.10,
            "INSURANCE_COVERAGE": 0.10, "OPERATIONAL_RESILIENCE": 0.10,
            "GOVERNANCE_CONTROLS": 0.10,
        })
        report = engine.evaluate_custodian(
            make_profile(is_asset_bankruptcy_remote=False), assessment_date=AS_OF)
        # Regulatory 60/100 at weight 0.60 -> 36; the other four pillars are
        # perfect at a combined weight of 0.40 -> 40. Total 76.0.
        self.assertEqual(report.composite_due_diligence_score, 76.0)

    def test_partial_weights_rejected_at_construction(self):
        # Regression: a partial dict summing to 1.0 passed the sum check and then
        # surfaced as a bare KeyError from inside pillar scoring.
        with self.assertRaises(CustodyDueDiligenceError) as ctx:
            CustodyVendorDueDiligenceEngine(weights={"REGULATORY_LEGAL": 1.0})
        self.assertIn("CYBERSECURITY", str(ctx.exception))

    def test_unknown_weight_key_rejected(self):
        with self.assertRaises(CustodyDueDiligenceError):
            CustodyVendorDueDiligenceEngine(weights={
                "REGULATORY_LEGAL": 0.25, "CYBERSECURITY": 0.25,
                "INSURANCE_COVERAGE": 0.20, "OPERATIONAL_RESILIENCE": 0.15,
                "TYPO_PILLAR": 0.15,
            })

    def test_invalid_engine_thresholds_rejected(self):
        with self.assertRaises(CustodyDueDiligenceError):
            CustodyVendorDueDiligenceEngine(min_passing_score=150.0)
        with self.assertRaises(CustodyDueDiligenceError):
            CustodyVendorDueDiligenceEngine(min_insurance_coverage_ratio=2.0)
        with self.assertRaises(CustodyDueDiligenceError):
            CustodyVendorDueDiligenceEngine(min_insurance_usd=-1.0)


if __name__ == "__main__":
    unittest.main()
