"""Behavioural tests for the jurisdictional custody compliance engine.

Every audit passes an explicit ``as_of`` so results are reproducible and do not
drift as the UK cryptoasset regime's commencement date approaches.
"""
import unittest
from datetime import date

from regulatory_custody_requirements_by_jurisdiction import (
    ASSET_SCOPE_CRYPTO,
    ASSET_SCOPE_SECURITIES,
    CUSTODY_AFFILIATED_CUSTODIAN,
    CUSTODY_EXCHANGE_CUSTODY,
    CUSTODY_QUALIFIED_CUSTODIAN,
    CUSTODY_SELF_CUSTODY,
    CUSTODY_STATE_CHARTERED_TRUST,
    MAS_COLD_STORAGE_EXPECTATION_PCT,
    MICA_ANNEX_IV_CLASS_2_EUR,
    SEVERITY_ADVISORY,
    SEVERITY_MANDATORY,
    SEVERITY_UNEVIDENCED,
    STATUS_COMPLIANT,
    STATUS_PRE_COMMENCEMENT,
    STATUS_UNKNOWN_JURISDICTION,
    STATUS_UNSUPPORTED_REGIME,
    STATUS_VIOLATION,
    UK_CRYPTOASSET_REGIME_COMMENCEMENT,
    CustodyRegimeError,
    CustodyRequirement,
    CustodyRuleSpec,
    CustodySetup,
    RegulatoryCustodyRequirementsByJurisdictionEngine,
)

AS_OF = date(2026, 8, 27)


def violation_types(report):
    return {v.violation_type for v in report.violations}


def advisory_types(report):
    return {a.violation_type for a in report.advisories}


def us_securities_setup(**overrides):
    """A fully evidenced, compliant Advisers Act securities custody setup."""
    kwargs = dict(
        jurisdiction="US",
        custodian_name="Example National Trust Bank",
        custody_type=CUSTODY_QUALIFIED_CUSTODIAN,
        is_asset_segregated=True,
        has_annual_audit=True,
        asset_scope=ASSET_SCOPE_SECURITIES,
    )
    kwargs.update(overrides)
    return CustodySetup(**kwargs)


def eu_crypto_setup(**overrides):
    """A fully evidenced, compliant MiCA custody setup."""
    kwargs = dict(
        jurisdiction="EU",
        custodian_name="Example CASP BV",
        custody_type=CUSTODY_QUALIFIED_CUSTODIAN,
        is_asset_segregated=True,
        has_annual_audit=True,
        asset_scope=ASSET_SCOPE_CRYPTO,
        custodian_is_authorised_in_jurisdiction=True,
        has_documented_custody_policy=True,
        maintains_client_position_register=True,
        prudential_safeguard_eur=300_000.0,
        fixed_overheads_prior_year_eur=800_000.0,
    )
    kwargs.update(overrides)
    return CustodySetup(**kwargs)


def sg_crypto_setup(**overrides):
    """A fully evidenced, compliant MAS DPT custody setup."""
    kwargs = dict(
        jurisdiction="SG",
        custodian_name="Example DPT Pte Ltd",
        custody_type=CUSTODY_SELF_CUSTODY,
        is_asset_segregated=True,
        has_annual_audit=True,
        asset_scope=ASSET_SCOPE_CRYPTO,
        custodian_is_authorised_in_jurisdiction=True,
        holds_client_assets_on_statutory_trust=True,
        cold_storage_pct=95.0,
    )
    kwargs.update(overrides)
    return CustodySetup(**kwargs)


class TestRegimeResolution(unittest.TestCase):

    def setUp(self):
        self.engine = RegulatoryCustodyRequirementsByJurisdictionEngine()

    def test_jurisdiction_and_asset_scope_select_the_regime(self):
        report = self.engine.audit_custody_setup(us_securities_setup(), as_of=AS_OF)
        self.assertEqual(report.regime_id, "US:SECURITIES")
        self.assertIn("206(4)-2", report.instrument)

    def test_us_crypto_and_us_securities_are_different_regimes(self):
        """A crypto question must not be answered with the securities ruleset."""
        crypto = self.engine.audit_custody_setup(
            us_securities_setup(asset_scope=ASSET_SCOPE_CRYPTO), as_of=AS_OF)
        self.assertEqual(crypto.regime_id, "US:CRYPTO")
        self.assertNotEqual(crypto.instrument,
                            self.engine.rules["US:SECURITIES"].instrument)

    def test_jurisdiction_is_case_insensitive(self):
        report = self.engine.audit_custody_setup(
            us_securities_setup(jurisdiction="us"), as_of=AS_OF)
        self.assertEqual(report.jurisdiction, "US")
        self.assertEqual(report.regime_id, "US:SECURITIES")

    def test_unknown_jurisdiction_is_reported_not_guessed(self):
        report = self.engine.audit_custody_setup(
            us_securities_setup(jurisdiction="JP"), as_of=AS_OF)
        self.assertEqual(report.status, STATUS_UNKNOWN_JURISDICTION)
        self.assertFalse(report.is_compliant)

    def test_known_jurisdiction_unmodelled_asset_scope_is_distinguished(self):
        """EU securities custody is MiFID II/AIFMD, not MiCA -- say so rather
        than silently auditing it against the crypto ruleset."""
        report = self.engine.audit_custody_setup(
            eu_crypto_setup(asset_scope=ASSET_SCOPE_SECURITIES), as_of=AS_OF)
        self.assertEqual(report.status, STATUS_UNSUPPORTED_REGIME)
        self.assertFalse(report.is_compliant)

    def test_explicit_regime_id_overrides_resolution(self):
        report = self.engine.audit_custody_setup(
            us_securities_setup(), regime_id="US:CRYPTO", as_of=AS_OF)
        self.assertEqual(report.regime_id, "US:CRYPTO")

    def test_custom_regime_can_be_registered(self):
        spec = CustodyRuleSpec(
            jurisdiction="CH",
            regime_id="CH:CRYPTO",
            regulator="FINMA",
            instrument="Example",
            asset_scope=ASSET_SCOPE_CRYPTO,
            requirements=(
                CustodyRequirement(
                    requirement_id="SEGREGATION",
                    description="Segregate client assets.",
                    citation="Example",
                    check=lambda s: s.is_asset_segregated,
                ),
            ),
        )
        engine = RegulatoryCustodyRequirementsByJurisdictionEngine(
            custom_rules={"CH:CRYPTO": spec})
        report = engine.audit_custody_setup(
            CustodySetup("CH", "Example AG", CUSTODY_QUALIFIED_CUSTODIAN, True, True),
            as_of=AS_OF)
        self.assertEqual(report.status, STATUS_COMPLIANT)

    def test_custom_regime_key_must_match_regime_id(self):
        spec = CustodyRuleSpec(
            jurisdiction="CH", regime_id="CH:CRYPTO", regulator="FINMA",
            instrument="Example", asset_scope=ASSET_SCOPE_CRYPTO, requirements=())
        with self.assertRaises(CustodyRegimeError):
            RegulatoryCustodyRequirementsByJurisdictionEngine(
                custom_rules={"CH:SECURITIES": spec})


class TestEvidenceDiscipline(unittest.TestCase):
    """Missing evidence must never be reported as compliance."""

    def setUp(self):
        self.engine = RegulatoryCustodyRequirementsByJurisdictionEngine()

    def test_unevidenced_mandatory_requirement_is_a_violation(self):
        report = self.engine.audit_custody_setup(
            us_securities_setup(is_asset_segregated=None), as_of=AS_OF)
        self.assertFalse(report.is_compliant)
        self.assertEqual(report.status, STATUS_VIOLATION)
        self.assertIn("CLIENT_ASSET_SEGREGATION_NOT_EVIDENCED", violation_types(report))

    def test_unevidenced_is_severity_tagged_apart_from_a_breach(self):
        unevidenced = self.engine.audit_custody_setup(
            us_securities_setup(is_asset_segregated=None), as_of=AS_OF)
        breached = self.engine.audit_custody_setup(
            us_securities_setup(is_asset_segregated=False), as_of=AS_OF)
        self.assertEqual(unevidenced.violations[0].severity, SEVERITY_UNEVIDENCED)
        self.assertEqual(breached.violations[0].severity, SEVERITY_MANDATORY)
        self.assertIn("CLIENT_ASSET_SEGREGATION", violation_types(breached))

    def test_every_finding_carries_its_citation(self):
        report = self.engine.audit_custody_setup(
            us_securities_setup(is_asset_segregated=False, has_annual_audit=False),
            as_of=AS_OF)
        self.assertTrue(all(v.citation for v in report.violations))
        self.assertTrue(any("206(4)-2" in v.citation for v in report.violations))


class TestUnitedStatesAdvisersActRegime(unittest.TestCase):

    def setUp(self):
        self.engine = RegulatoryCustodyRequirementsByJurisdictionEngine()

    def test_fully_evidenced_setup_is_compliant(self):
        report = self.engine.audit_custody_setup(us_securities_setup(), as_of=AS_OF)
        self.assertEqual(report.status, STATUS_COMPLIANT)
        self.assertTrue(report.is_compliant)
        self.assertEqual(report.violations, [])

    def test_self_custody_fails_the_qualified_custodian_test(self):
        report = self.engine.audit_custody_setup(
            us_securities_setup(custody_type=CUSTODY_SELF_CUSTODY), as_of=AS_OF)
        self.assertIn("QUALIFIED_CUSTODIAN", violation_types(report))

    def test_exchange_custody_fails_the_qualified_custodian_test(self):
        report = self.engine.audit_custody_setup(
            us_securities_setup(custody_type=CUSTODY_EXCHANGE_CUSTODY), as_of=AS_OF)
        self.assertIn("QUALIFIED_CUSTODIAN", violation_types(report))

    def test_fee_deduction_exception_relieves_the_surprise_examination(self):
        """Rule 206(4)-2(b)(3). Reporting a violation here is a false positive."""
        report = self.engine.audit_custody_setup(
            us_securities_setup(has_annual_audit=False,
                                custody_solely_for_fee_deduction=True),
            as_of=AS_OF)
        self.assertNotIn("ANNUAL_SURPRISE_EXAMINATION", violation_types(report))
        self.assertEqual(report.status, STATUS_COMPLIANT)
        self.assertTrue(any("(b)(3)" in e for e in report.exemptions_applied))

    def test_audited_pooled_vehicle_exception_relieves_the_surprise_examination(self):
        """Rule 206(4)-2(b)(4): audited pool distributing statements in 120 days."""
        report = self.engine.audit_custody_setup(
            us_securities_setup(has_annual_audit=None,
                                pooled_vehicle_audited_within_120_days=True),
            as_of=AS_OF)
        self.assertNotIn("ANNUAL_SURPRISE_EXAMINATION_NOT_EVIDENCED",
                         violation_types(report))
        self.assertTrue(any("(b)(4)" in e for e in report.exemptions_applied))

    def test_missing_surprise_examination_without_an_exception_is_a_violation(self):
        report = self.engine.audit_custody_setup(
            us_securities_setup(has_annual_audit=False), as_of=AS_OF)
        self.assertIn("ANNUAL_SURPRISE_EXAMINATION", violation_types(report))
        self.assertEqual(report.exemptions_applied, [])

    def test_internal_control_report_only_required_for_a_related_custodian(self):
        """Rule 206(4)-2(a)(6) is conditional; it must not fire on an
        unaffiliated custodian."""
        unaffiliated = self.engine.audit_custody_setup(
            us_securities_setup(has_internal_control_report=None), as_of=AS_OF)
        self.assertNotIn("INTERNAL_CONTROL_REPORT_NOT_EVIDENCED",
                         violation_types(unaffiliated))

        affiliated = self.engine.audit_custody_setup(
            us_securities_setup(custody_type=CUSTODY_AFFILIATED_CUSTODIAN,
                                has_internal_control_report=None),
            as_of=AS_OF)
        self.assertIn("INTERNAL_CONTROL_REPORT_NOT_EVIDENCED",
                      violation_types(affiliated))

    def test_related_person_flag_also_engages_the_internal_control_report(self):
        report = self.engine.audit_custody_setup(
            us_securities_setup(custodian_is_related_person=True,
                                has_internal_control_report=False),
            as_of=AS_OF)
        self.assertIn("INTERNAL_CONTROL_REPORT", violation_types(report))

    def test_state_trust_route_is_crypto_only(self):
        """The 2025-09-30 relief covers crypto assets, not securities custody."""
        securities = self.engine.audit_custody_setup(
            us_securities_setup(custody_type=CUSTODY_STATE_CHARTERED_TRUST),
            as_of=AS_OF)
        self.assertIn("QUALIFIED_CUSTODIAN", violation_types(securities))

        crypto = self.engine.audit_custody_setup(
            us_securities_setup(asset_scope=ASSET_SCOPE_CRYPTO,
                                custody_type=CUSTODY_STATE_CHARTERED_TRUST,
                                state_trust_no_action_conditions_verified=True),
            as_of=AS_OF)
        self.assertNotIn("QUALIFIED_CUSTODIAN", violation_types(crypto))
        self.assertEqual(crypto.status, STATUS_COMPLIANT)

    def test_state_trust_without_verified_no_action_conditions_fails(self):
        report = self.engine.audit_custody_setup(
            us_securities_setup(asset_scope=ASSET_SCOPE_CRYPTO,
                                custody_type=CUSTODY_STATE_CHARTERED_TRUST),
            as_of=AS_OF)
        self.assertIn("STATE_TRUST_NO_ACTION_CONDITIONS_NOT_EVIDENCED",
                      violation_types(report))
        self.assertTrue(any("2025-09-30" in v.citation for v in report.violations))

    def test_no_action_conditions_not_demanded_of_a_bank_custodian(self):
        report = self.engine.audit_custody_setup(
            us_securities_setup(asset_scope=ASSET_SCOPE_CRYPTO), as_of=AS_OF)
        self.assertNotIn("STATE_TRUST_NO_ACTION_CONDITIONS_NOT_EVIDENCED",
                         violation_types(report))


class TestEuropeanUnionMiCARegime(unittest.TestCase):

    def setUp(self):
        self.engine = RegulatoryCustodyRequirementsByJurisdictionEngine()

    def test_fully_evidenced_casp_is_compliant(self):
        report = self.engine.audit_custody_setup(eu_crypto_setup(), as_of=AS_OF)
        self.assertEqual(report.status, STATUS_COMPLIANT)

    def test_absence_of_insurance_is_not_itself_a_mica_violation(self):
        """Regression: earlier revisions raised MISSING_INSURANCE for the EU.
        MiCA Art. 75 does not mention insurance; Art. 67(4) makes it one
        permitted form of the prudential safeguard, satisfiable by own funds."""
        report = self.engine.audit_custody_setup(
            eu_crypto_setup(has_insurance_coverage=False), as_of=AS_OF)
        self.assertEqual(report.status, STATUS_COMPLIANT)
        self.assertNotIn("MISSING_INSURANCE", violation_types(report))

    def test_unauthorised_casp_is_a_violation(self):
        report = self.engine.audit_custody_setup(
            eu_crypto_setup(custodian_is_authorised_in_jurisdiction=False),
            as_of=AS_OF)
        self.assertIn("AUTHORISED_CASP", violation_types(report))

    def test_missing_custody_policy_and_register_are_separate_findings(self):
        report = self.engine.audit_custody_setup(
            eu_crypto_setup(has_documented_custody_policy=False,
                            maintains_client_position_register=False),
            as_of=AS_OF)
        self.assertIn("CUSTODY_POLICY", violation_types(report))
        self.assertIn("REGISTER_OF_POSITIONS", violation_types(report))
        self.assertTrue(any("75(3)" in v.citation for v in report.violations))
        self.assertTrue(any("75(2)" in v.citation for v in report.violations))

    def test_prudential_safeguard_below_annex_iv_floor_fails(self):
        report = self.engine.audit_custody_setup(
            eu_crypto_setup(prudential_safeguard_eur=MICA_ANNEX_IV_CLASS_2_EUR - 1),
            as_of=AS_OF)
        self.assertIn("PRUDENTIAL_SAFEGUARDS", violation_types(report))

    def test_prudential_safeguard_exactly_at_the_floor_passes_when_overheads_allow(self):
        report = self.engine.audit_custody_setup(
            eu_crypto_setup(prudential_safeguard_eur=MICA_ANNEX_IV_CLASS_2_EUR,
                            fixed_overheads_prior_year_eur=400_000.0),
            as_of=AS_OF)
        self.assertEqual(report.status, STATUS_COMPLIANT)

    def test_higher_of_test_binds_on_the_fixed_overheads_limb(self):
        """EUR 200,000 clears the Annex IV floor but is short of a quarter of
        EUR 1,000,000 of fixed overheads (EUR 250,000)."""
        report = self.engine.audit_custody_setup(
            eu_crypto_setup(prudential_safeguard_eur=200_000.0,
                            fixed_overheads_prior_year_eur=1_000_000.0),
            as_of=AS_OF)
        self.assertIn("PRUDENTIAL_SAFEGUARDS", violation_types(report))

    def test_unknown_overheads_makes_the_higher_of_test_unevidenced(self):
        """Clearing the floor alone is not enough to conclude compliance."""
        report = self.engine.audit_custody_setup(
            eu_crypto_setup(prudential_safeguard_eur=500_000.0,
                            fixed_overheads_prior_year_eur=None),
            as_of=AS_OF)
        self.assertIn("PRUDENTIAL_SAFEGUARDS_NOT_EVIDENCED", violation_types(report))

    def test_insurance_backed_safeguard_satisfies_article_67(self):
        report = self.engine.audit_custody_setup(
            eu_crypto_setup(has_insurance_coverage=True,
                            prudential_safeguard_eur=150_000.0,
                            fixed_overheads_prior_year_eur=200_000.0),
            as_of=AS_OF)
        self.assertEqual(report.status, STATUS_COMPLIANT)


class TestUnitedKingdomRegimes(unittest.TestCase):

    def setUp(self):
        self.engine = RegulatoryCustodyRequirementsByJurisdictionEngine()

    def test_cass_regime_has_no_qualified_custodian_requirement(self):
        """Regression: 'qualified custodian' is an Advisers Act term with no CASS
        analogue. Self-custody by an authorised firm is not a UK breach by
        reason of the arrangement type alone."""
        report = self.engine.audit_custody_setup(
            CustodySetup(jurisdiction="UK",
                         custodian_name="Example Investments Ltd",
                         custody_type=CUSTODY_SELF_CUSTODY,
                         is_asset_segregated=True,
                         has_annual_audit=True,
                         asset_scope=ASSET_SCOPE_SECURITIES,
                         custodian_is_authorised_in_jurisdiction=True),
            as_of=AS_OF)
        self.assertEqual(report.status, STATUS_COMPLIANT)
        self.assertNotIn("QUALIFIED_CUSTODIAN", violation_types(report))
        self.assertNotIn("UNQUALIFIED_CUSTODIAN", violation_types(report))

    def test_unauthorised_uk_firm_is_a_violation(self):
        report = self.engine.audit_custody_setup(
            CustodySetup("UK", "Example Ltd", CUSTODY_SELF_CUSTODY, True, True,
                         asset_scope=ASSET_SCOPE_SECURITIES,
                         custodian_is_authorised_in_jurisdiction=False),
            as_of=AS_OF)
        self.assertIn("FCA_AUTHORISED_FIRM", violation_types(report))

    def test_client_assets_report_is_cited_to_sup_3_10_not_a_surprise_exam(self):
        report = self.engine.audit_custody_setup(
            CustodySetup("UK", "Example Ltd", CUSTODY_SELF_CUSTODY, True, False,
                         asset_scope=ASSET_SCOPE_SECURITIES,
                         custodian_is_authorised_in_jurisdiction=True),
            as_of=AS_OF)
        self.assertIn("ANNUAL_CLIENT_ASSETS_REPORT", violation_types(report))
        self.assertTrue(any("SUP 3.10" in v.citation for v in report.violations))

    def test_uk_crypto_regime_before_commencement_is_a_readiness_assessment(self):
        before = UK_CRYPTOASSET_REGIME_COMMENCEMENT.replace(
            year=UK_CRYPTOASSET_REGIME_COMMENCEMENT.year - 1)
        report = self.engine.audit_custody_setup(
            CustodySetup("UK", "Example Crypto Ltd", CUSTODY_SELF_CUSTODY,
                         False, False, asset_scope=ASSET_SCOPE_CRYPTO,
                         custodian_is_authorised_in_jurisdiction=False),
            as_of=before)
        self.assertEqual(report.status, STATUS_PRE_COMMENCEMENT)
        self.assertEqual(report.violations, [])
        self.assertIn("CLIENT_ASSET_SEGREGATION", advisory_types(report))
        self.assertTrue(all(a.severity == SEVERITY_ADVISORY
                            for a in report.advisories))

    def test_pre_commencement_readiness_gaps_are_not_reported_as_compliant(self):
        before = UK_CRYPTOASSET_REGIME_COMMENCEMENT.replace(
            year=UK_CRYPTOASSET_REGIME_COMMENCEMENT.year - 1)
        report = self.engine.audit_custody_setup(
            CustodySetup("UK", "Example Crypto Ltd", CUSTODY_SELF_CUSTODY,
                         False, False, asset_scope=ASSET_SCOPE_CRYPTO,
                         custodian_is_authorised_in_jurisdiction=False),
            as_of=before)
        self.assertFalse(report.is_compliant)

    def test_uk_crypto_regime_bites_on_the_commencement_date_itself(self):
        report = self.engine.audit_custody_setup(
            CustodySetup("UK", "Example Crypto Ltd", CUSTODY_SELF_CUSTODY,
                         False, True, asset_scope=ASSET_SCOPE_CRYPTO,
                         custodian_is_authorised_in_jurisdiction=True),
            as_of=UK_CRYPTOASSET_REGIME_COMMENCEMENT)
        self.assertEqual(report.status, STATUS_VIOLATION)
        self.assertIn("CLIENT_ASSET_SEGREGATION", violation_types(report))


class TestSingaporeDPTRegime(unittest.TestCase):

    def setUp(self):
        self.engine = RegulatoryCustodyRequirementsByJurisdictionEngine()

    def test_self_custody_under_a_statutory_trust_is_permitted(self):
        """Regression: MAS does not mandate an independent third-party
        custodian; a licensed provider may maintain the trust account itself."""
        report = self.engine.audit_custody_setup(sg_crypto_setup(), as_of=AS_OF)
        self.assertEqual(report.status, STATUS_COMPLIANT)

    def test_absence_of_insurance_is_not_a_mas_violation(self):
        """Regression: earlier revisions raised MISSING_INSURANCE for SG. No MAS
        instrument mandates insurance over custodied digital payment tokens."""
        report = self.engine.audit_custody_setup(
            sg_crypto_setup(has_insurance_coverage=False), as_of=AS_OF)
        self.assertEqual(report.status, STATUS_COMPLIANT)
        self.assertNotIn("MISSING_INSURANCE", violation_types(report))

    def test_missing_statutory_trust_is_a_violation(self):
        report = self.engine.audit_custody_setup(
            sg_crypto_setup(holds_client_assets_on_statutory_trust=False),
            as_of=AS_OF)
        self.assertIn("STATUTORY_TRUST", violation_types(report))

    def test_cold_storage_shortfall_is_advisory_not_a_breach(self):
        """The 90% expectation is supervisory guidance, not a statutory rule."""
        report = self.engine.audit_custody_setup(
            sg_crypto_setup(cold_storage_pct=50.0), as_of=AS_OF)
        self.assertEqual(report.status, STATUS_COMPLIANT)
        self.assertTrue(report.is_compliant)
        self.assertIn("COLD_STORAGE_MAJORITY", advisory_types(report))
        self.assertEqual(report.advisories[0].severity, SEVERITY_ADVISORY)

    def test_cold_storage_exactly_at_the_expectation_is_satisfied(self):
        report = self.engine.audit_custody_setup(
            sg_crypto_setup(cold_storage_pct=MAS_COLD_STORAGE_EXPECTATION_PCT),
            as_of=AS_OF)
        self.assertEqual(report.advisories, [])
        self.assertIn("COLD_STORAGE_MAJORITY", report.satisfied_requirements)

    def test_unreported_cold_storage_is_advisory_not_silently_passed(self):
        report = self.engine.audit_custody_setup(
            sg_crypto_setup(cold_storage_pct=None), as_of=AS_OF)
        self.assertIn("COLD_STORAGE_MAJORITY_NOT_EVIDENCED", advisory_types(report))


class TestInputValidation(unittest.TestCase):

    def setUp(self):
        self.engine = RegulatoryCustodyRequirementsByJurisdictionEngine()

    def test_unrecognised_custody_type_raises_rather_than_flagging_a_breach(self):
        """A typo must not be laundered into an authoritative-looking finding."""
        with self.assertRaises(CustodyRegimeError):
            self.engine.audit_custody_setup(
                us_securities_setup(custody_type="QUALIFED_CUSTODIAN"), as_of=AS_OF)

    def test_blank_jurisdiction_raises(self):
        with self.assertRaises(CustodyRegimeError):
            self.engine.audit_custody_setup(
                us_securities_setup(jurisdiction="   "), as_of=AS_OF)

    def test_blank_custodian_name_raises(self):
        with self.assertRaises(CustodyRegimeError):
            self.engine.audit_custody_setup(
                us_securities_setup(custodian_name=""), as_of=AS_OF)

    def test_unrecognised_asset_scope_raises(self):
        with self.assertRaises(CustodyRegimeError):
            self.engine.audit_custody_setup(
                us_securities_setup(asset_scope="DERIVATIVES"), as_of=AS_OF)

    def test_out_of_range_cold_storage_pct_raises(self):
        with self.assertRaises(CustodyRegimeError):
            self.engine.audit_custody_setup(
                sg_crypto_setup(cold_storage_pct=150.0), as_of=AS_OF)

    def test_negative_prudential_safeguard_raises(self):
        with self.assertRaises(CustodyRegimeError):
            self.engine.audit_custody_setup(
                eu_crypto_setup(prudential_safeguard_eur=-1.0), as_of=AS_OF)

    def test_non_finite_prudential_safeguard_raises(self):
        with self.assertRaises(CustodyRegimeError):
            self.engine.audit_custody_setup(
                eu_crypto_setup(prudential_safeguard_eur=float("nan")), as_of=AS_OF)

    def test_non_date_as_of_raises(self):
        with self.assertRaises(CustodyRegimeError):
            self.engine.audit_custody_setup(us_securities_setup(), as_of="2026-08-27")


class TestReportContract(unittest.TestCase):

    def setUp(self):
        self.engine = RegulatoryCustodyRequirementsByJurisdictionEngine()

    def test_report_records_the_regime_and_the_as_of_date(self):
        report = self.engine.audit_custody_setup(eu_crypto_setup(), as_of=AS_OF)
        self.assertEqual(report.as_of, AS_OF)
        self.assertEqual(report.regime_id, "EU:CRYPTO")
        self.assertEqual(report.regulator,
                         "National competent authority (ESMA and EBA at Union level)")
        self.assertTrue(report.scope_note)

    def test_audit_is_deterministic_for_a_fixed_as_of(self):
        first = self.engine.audit_custody_setup(eu_crypto_setup(), as_of=AS_OF)
        second = self.engine.audit_custody_setup(eu_crypto_setup(), as_of=AS_OF)
        self.assertEqual(first.audit_notes, second.audit_notes)
        self.assertEqual(first.satisfied_requirements, second.satisfied_requirements)

    def test_audit_does_not_mutate_the_setup(self):
        setup = eu_crypto_setup()
        before = vars(setup).copy()
        self.engine.audit_custody_setup(setup, as_of=AS_OF)
        self.assertEqual(vars(setup), before)

    def test_default_rules_are_not_mutated_by_custom_rules(self):
        spec = CustodyRuleSpec(
            jurisdiction="US", regime_id="US:SECURITIES", regulator="SEC",
            instrument="Override", asset_scope=ASSET_SCOPE_SECURITIES,
            requirements=())
        RegulatoryCustodyRequirementsByJurisdictionEngine(
            custom_rules={"US:SECURITIES": spec})
        fresh = RegulatoryCustodyRequirementsByJurisdictionEngine()
        self.assertNotEqual(fresh.rules["US:SECURITIES"].instrument, "Override")


if __name__ == "__main__":
    unittest.main()
