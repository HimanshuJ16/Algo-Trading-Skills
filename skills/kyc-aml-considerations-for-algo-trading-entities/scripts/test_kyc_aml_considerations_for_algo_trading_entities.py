import unittest
from datetime import date

from kyc_aml_considerations_for_algo_trading_entities import (
    STATUS_APPROVED,
    STATUS_EDD_REQUIRED,
    STATUS_REJECTED_FATF,
    STATUS_REJECTED_NO_CONTROL_PERSON,
    STATUS_REJECTED_OFAC_50,
    STATUS_REJECTED_OWNERSHIP_OPACITY,
    STATUS_REJECTED_SANCTIONS,
    STATUS_REJECTED_UNVERIFIED_UBO,
    ControlPerson,
    EntityKycAmlPayload,
    JurisdictionRiskLists,
    KycAmlEntityComplianceEngine,
    KycAmlValidationError,
    UboRecord,
    normalize_country,
)

# Fixed assessment date so no test depends on the wall clock. It sits inside the
# 180-day freshness window of the default 2026-06-19 list snapshot.
ASSESSMENT_DATE = date(2026, 8, 25)


def ubo(name, pct, verified=True, pep=False, sanctioned=False, pep_category=""):
    return UboRecord(
        name=name,
        ownership_pct=pct,
        is_pep=pep,
        is_sanctioned=sanctioned,
        is_identity_verified=verified,
        pep_category=pep_category,
    )


def control(name="Dana Reyes", verified=True, pep=False, sanctioned=False, pep_category=""):
    return ControlPerson(
        name=name,
        title="Chief Executive Officer",
        is_pep=pep,
        is_sanctioned=sanctioned,
        is_identity_verified=verified,
        pep_category=pep_category,
    )


def payload(**overrides):
    """A clean, fully compliant baseline file: 60/40 verified, verified CEO, US/US."""
    base = dict(
        entity_name="Alpha Quant LP",
        incorporation_country="USA",
        banking_country="USA",
        ubos=[ubo("Alice Smith", 60.0), ubo("Bob Jones", 40.0)],
        control_person=control(),
    )
    base.update(overrides)
    return EntityKycAmlPayload(**base)


class KycAmlTestBase(unittest.TestCase):
    def setUp(self):
        self.engine = KycAmlEntityComplianceEngine(ubo_ownership_threshold_pct=25.0)

    def audit(self, p):
        return self.engine.audit_entity_compliance(p, assessment_date=ASSESSMENT_DATE)

    def codes(self, report):
        return {f.code for f in report.findings}


class TestBaselineApproval(KycAmlTestBase):

    def test_verified_institutional_fund_approval(self):
        report = self.audit(payload())

        self.assertEqual(report.status, STATUS_APPROVED)
        self.assertTrue(report.is_approved)
        self.assertEqual(report.total_ubo_ownership_accounted_pct, 100.0)
        self.assertEqual(report.unaccounted_ownership_pct, 0.0)
        self.assertFalse(report.has_sanctions_hit)
        self.assertFalse(report.is_fatf_blacklisted)
        self.assertTrue(report.control_person_verified)
        self.assertFalse(report.requires_enhanced_due_diligence)
        self.assertEqual(report.blocking_findings, [])

    def test_report_records_normalized_jurisdictions_and_provenance(self):
        report = self.audit(payload(incorporation_country="Cayman Islands",
                                    banking_country="uk"))

        self.assertEqual(report.incorporation_country, "KY")
        self.assertEqual(report.banking_country, "GB")
        self.assertEqual(report.assessment_date, ASSESSMENT_DATE)
        self.assertEqual(report.screening_lists_as_of, date(2026, 6, 19))
        self.assertEqual(report.status, STATUS_APPROVED)

    def test_exact_threshold_owner_is_in_scope(self):
        # 25.0 is inclusive under 31 CFR 1010.230(d)(1) ("25 percent or more").
        report = self.audit(payload(
            ubos=[ubo("Alice Smith", 75.0), ubo("Mystery Owner", 25.0, verified=False)]))
        self.assertEqual(report.status, STATUS_REJECTED_UNVERIFIED_UBO)
        self.assertEqual(report.unverified_ubos_count, 1)

    def test_just_below_threshold_owner_is_out_of_scope(self):
        report = self.audit(payload(
            ubos=[ubo("Alice Smith", 75.1), ubo("Small Holder", 24.9, verified=False)]))
        self.assertEqual(report.status, STATUS_APPROVED)
        self.assertEqual(report.unverified_ubos_count, 0)


class TestControlProng(KycAmlTestBase):
    """31 CFR 1010.230(d)(2): one control person, always, independent of ownership."""

    def test_no_control_person_blocks_even_with_perfect_ownership(self):
        report = self.audit(payload(control_person=None))

        self.assertEqual(report.status, STATUS_REJECTED_NO_CONTROL_PERSON)
        self.assertFalse(report.control_person_verified)
        self.assertEqual(report.total_ubo_ownership_accounted_pct, 100.0)

    def test_widely_held_entity_with_no_25pct_owner_still_needs_control_person(self):
        # Five 20% owners: the ownership prong returns nobody. This is exactly the
        # structure the pre-fix engine approved with zero verified individuals.
        five_holders = [ubo(f"Holder {i}", 20.0) for i in range(5)]
        report = self.audit(payload(ubos=five_holders, control_person=None))

        self.assertEqual(report.status, STATUS_REJECTED_NO_CONTROL_PERSON)
        self.assertEqual(report.unverified_ubos_count, 0)
        self.assertEqual(report.unaccounted_ownership_pct, 0.0)

    def test_unverified_control_person_blocks(self):
        report = self.audit(payload(control_person=control(verified=False)))

        self.assertEqual(report.status, STATUS_REJECTED_NO_CONTROL_PERSON)
        self.assertFalse(report.control_person_verified)

    def test_control_prong_can_be_disabled_deliberately(self):
        engine = KycAmlEntityComplianceEngine(require_control_person=False)
        report = engine.audit_entity_compliance(
            payload(control_person=None), assessment_date=ASSESSMENT_DATE)
        self.assertEqual(report.status, STATUS_APPROVED)

    def test_sanctioned_control_person_is_screened(self):
        report = self.audit(payload(control_person=control(sanctioned=True)))

        self.assertEqual(report.status, STATUS_REJECTED_SANCTIONS)
        self.assertTrue(report.has_sanctions_hit)
        # A control person holds no declared equity, so the OFAC 50% aggregate
        # must not move.
        self.assertEqual(report.aggregate_sanctioned_ownership_pct, 0.0)


class TestOwnershipProng(KycAmlTestBase):

    def test_unverified_ubo_rejection(self):
        report = self.audit(payload(
            entity_name="Opaque Fund LP",
            incorporation_country="CAYMAN_ISLANDS",
            banking_country="UK",
            ubos=[ubo("Alice Smith", 60.0), ubo("Mystery Owner", 40.0, verified=False)]))

        self.assertEqual(report.status, STATUS_REJECTED_UNVERIFIED_UBO)
        self.assertEqual(report.unverified_ubos_count, 1)

    def test_indirect_holdings_aggregate_across_the_threshold(self):
        # 15% + 15% through two vehicles is 30% >= 25% and must be caught. Each
        # record on its own is below the threshold.
        report = self.audit(payload(ubos=[
            ubo("Jane Doe", 15.0, verified=False),
            ubo("JANE  DOE", 15.0, verified=False),
            ubo("Alice Smith", 70.0),
        ]))

        self.assertEqual(report.status, STATUS_REJECTED_UNVERIFIED_UBO)
        self.assertEqual(report.unverified_ubos_count, 1)
        self.assertEqual(report.total_ubo_ownership_accounted_pct, 100.0)

    def test_aggregation_does_not_double_count_a_verified_person(self):
        report = self.audit(payload(ubos=[
            ubo("Jane Doe", 15.0),
            ubo("Jane Doe", 15.0),
            ubo("Alice Smith", 70.0),
        ]))
        self.assertEqual(report.status, STATUS_APPROVED)
        self.assertEqual(report.total_ubo_ownership_accounted_pct, 100.0)

    def test_unaccounted_ownership_blocks_shell_layer_opacity(self):
        # One declared 26% owner leaves 74% behind holding companies. The
        # pre-fix engine approved this.
        report = self.audit(payload(ubos=[ubo("Alice Smith", 26.0)]))

        self.assertEqual(report.status, STATUS_REJECTED_OWNERSHIP_OPACITY)
        self.assertAlmostEqual(report.unaccounted_ownership_pct, 74.0)
        self.assertEqual(report.unverified_ubos_count, 0)

    def test_residual_just_below_threshold_is_tolerated(self):
        # 75.2% declared leaves 24.8% — below the 25% threshold, so no
        # undisclosed holder can reach it.
        report = self.audit(payload(ubos=[ubo("Alice Smith", 50.2), ubo("Bob Jones", 25.0)]))
        self.assertEqual(report.status, STATUS_APPROVED)
        self.assertAlmostEqual(report.unaccounted_ownership_pct, 24.8, places=6)

    def test_residual_exactly_at_threshold_is_tolerated_but_above_is_not(self):
        at = self.audit(payload(ubos=[ubo("Alice Smith", 75.0)]))
        self.assertEqual(at.status, STATUS_APPROVED)

        above = self.audit(payload(ubos=[ubo("Alice Smith", 74.9)]))
        self.assertEqual(above.status, STATUS_REJECTED_OWNERSHIP_OPACITY)

    def test_empty_ubo_list_is_fully_opaque(self):
        report = self.audit(payload(ubos=[]))
        self.assertEqual(report.status, STATUS_REJECTED_OWNERSHIP_OPACITY)
        self.assertEqual(report.unaccounted_ownership_pct, 100.0)


class TestSanctions(KycAmlTestBase):

    def test_sanctions_match_rejection(self):
        report = self.audit(payload(
            entity_name="Rogue Trading Co",
            incorporation_country="UK",
            banking_country="UK",
            ubos=[ubo("Sanctioned Individual", 40.0, pep=True, sanctioned=True),
                  ubo("Alice Smith", 60.0)]))

        self.assertEqual(report.status, STATUS_REJECTED_SANCTIONS)
        self.assertTrue(report.has_sanctions_hit)
        self.assertTrue(report.has_pep_hit)

    def test_entity_level_sanctions_match_is_screened(self):
        report = self.audit(payload(entity_is_sanctioned=True))

        self.assertEqual(report.status, STATUS_REJECTED_SANCTIONS)
        self.assertTrue(report.has_sanctions_hit)

    def test_ofac_50_percent_rule_aggregates_across_blocked_owners(self):
        # Two blocked persons at 25% each: neither reaches 50% alone, but OFAC
        # aggregates, and the entity is then itself blocked property.
        report = self.audit(payload(ubos=[
            ubo("Blocked One", 25.0, sanctioned=True),
            ubo("Blocked Two", 25.0, sanctioned=True),
            ubo("Alice Smith", 50.0),
        ]))

        self.assertEqual(report.status, STATUS_REJECTED_OFAC_50)
        self.assertEqual(report.aggregate_sanctioned_ownership_pct, 50.0)

    def test_below_50_percent_blocked_ownership_is_a_plain_sanctions_rejection(self):
        report = self.audit(payload(ubos=[
            ubo("Blocked One", 25.0, sanctioned=True),
            ubo("Blocked Two", 24.0, sanctioned=True),
            ubo("Alice Smith", 51.0),
        ]))

        self.assertEqual(report.status, STATUS_REJECTED_SANCTIONS)
        self.assertEqual(report.aggregate_sanctioned_ownership_pct, 49.0)
        self.assertNotIn(STATUS_REJECTED_OFAC_50, self.codes(report))


class TestJurisdictionRisk(KycAmlTestBase):

    def test_counter_measures_jurisdiction_blocks(self):
        report = self.audit(payload(incorporation_country="IRAN"))

        self.assertEqual(report.status, STATUS_REJECTED_FATF)
        self.assertTrue(report.is_fatf_blacklisted)

    def test_banking_jurisdiction_is_screened_not_just_incorporation(self):
        report = self.audit(payload(incorporation_country="USA", banking_country="KP"))

        self.assertEqual(report.status, STATUS_REJECTED_FATF)
        self.assertTrue(report.is_fatf_blacklisted)

    def test_iso_alpha2_and_names_screen_identically(self):
        # The pre-fix engine held names only, so an alpha-2 feed cleared Iran.
        for code in ("IR", "Iran", "islamic republic of iran"):
            with self.subTest(code=code):
                report = self.audit(payload(incorporation_country=code))
                self.assertEqual(report.status, STATUS_REJECTED_FATF)

    def test_myanmar_requires_edd_not_rejection(self):
        # FATF calls for counter-measures against Iran and the DPRK, but only
        # risk-proportionate EDD for Myanmar.
        report = self.audit(payload(incorporation_country="MYANMAR"))

        self.assertEqual(report.status, STATUS_EDD_REQUIRED)
        self.assertTrue(report.is_fatf_blacklisted)
        self.assertTrue(report.requires_enhanced_due_diligence)
        self.assertIn("FATF_CALL_FOR_ACTION_EDD", self.codes(report))
        self.assertNotIn(STATUS_REJECTED_FATF, self.codes(report))

    def test_grey_list_jurisdiction_triggers_edd_not_rejection(self):
        report = self.audit(payload(banking_country="MC"))  # Monaco

        self.assertEqual(report.status, STATUS_EDD_REQUIRED)
        self.assertTrue(report.is_fatf_increased_monitoring)
        self.assertFalse(report.is_fatf_blacklisted)
        self.assertIn("FATF_INCREASED_MONITORING", self.codes(report))

    def test_grey_list_edd_discharged_by_documented_source_of_funds(self):
        report = self.audit(payload(banking_country="MC", source_of_wealth_documented=True))

        self.assertEqual(report.status, STATUS_APPROVED)
        self.assertTrue(report.is_fatf_increased_monitoring)
        self.assertEqual(report.edd_conditions, [])

    def test_stale_lists_raise_an_advisory_but_do_not_block(self):
        report = self.engine.audit_entity_compliance(
            payload(), assessment_date=date(2027, 6, 19))

        self.assertEqual(report.status, STATUS_APPROVED)
        self.assertIn("STALE_JURISDICTION_LISTS", self.codes(report))

    def test_injected_lists_override_the_default_snapshot(self):
        lists = JurisdictionRiskLists(
            as_of=date(2026, 8, 1),
            counter_measures=frozenset({"KY"}),
            enhanced_due_diligence=frozenset(),
            increased_monitoring=frozenset(),
            source="test fixture",
        )
        engine = KycAmlEntityComplianceEngine(risk_lists=lists)
        report = engine.audit_entity_compliance(
            payload(incorporation_country="Cayman Islands"), assessment_date=ASSESSMENT_DATE)

        self.assertEqual(report.status, STATUS_REJECTED_FATF)
        self.assertEqual(report.screening_lists_as_of, date(2026, 8, 1))


class TestPepHandling(KycAmlTestBase):
    """FATF R.12: PEP status triggers EDD, it is not grounds for rejection."""

    def test_foreign_pep_requires_edd_not_rejection(self):
        report = self.audit(payload(ubos=[ubo("Alice Smith", 60.0, pep=True), ubo("Bob Jones", 40.0)]))

        self.assertEqual(report.status, STATUS_EDD_REQUIRED)
        self.assertTrue(report.has_pep_hit)
        self.assertIn("FOREIGN_PEP_EDD", self.codes(report))
        self.assertEqual(len(report.edd_conditions), 2)

    def test_unqualified_pep_flag_is_treated_as_foreign(self):
        report = self.audit(payload(ubos=[ubo("Alice Smith", 60.0, pep=True), ubo("Bob Jones", 40.0)]))
        self.assertIn("PEP_CATEGORY_ASSUMED_FOREIGN", self.codes(report))

    def test_foreign_pep_approved_once_edd_evidence_is_on_file(self):
        report = self.audit(payload(
            ubos=[ubo("Alice Smith", 60.0, pep=True, pep_category="FOREIGN"),
                  ubo("Bob Jones", 40.0)],
            senior_management_approval_obtained=True,
            source_of_wealth_documented=True))

        self.assertEqual(report.status, STATUS_APPROVED)
        self.assertTrue(report.has_pep_hit)
        self.assertEqual(report.edd_conditions, [])
        self.assertNotIn("PEP_CATEGORY_ASSUMED_FOREIGN", self.codes(report))

    def test_domestic_pep_is_advisory_only(self):
        report = self.audit(payload(
            ubos=[ubo("Alice Smith", 60.0, pep=True, pep_category="DOMESTIC"),
                  ubo("Bob Jones", 40.0)]))

        self.assertEqual(report.status, STATUS_APPROVED)
        self.assertTrue(report.has_pep_hit)
        self.assertIn("PEP_DOMESTIC_OR_IO", self.codes(report))
        self.assertEqual(report.edd_conditions, [])


class TestNoShortCircuit(KycAmlTestBase):
    """A rejection must never report an untested check as a clean result."""

    def test_jurisdiction_rejection_still_reports_the_sanctions_screen(self):
        report = self.audit(payload(
            incorporation_country="IRAN",
            ubos=[ubo("Sanctioned Individual", 60.0, sanctioned=True), ubo("Bob Jones", 40.0)]))

        # The pre-fix engine returned on the FATF hit with has_sanctions_hit=False
        # and total_ubo_ownership_accounted_pct=0.0 — both untested.
        self.assertTrue(report.has_sanctions_hit)
        self.assertEqual(report.total_ubo_ownership_accounted_pct, 100.0)
        self.assertEqual(report.aggregate_sanctioned_ownership_pct, 60.0)
        self.assertIn(STATUS_REJECTED_FATF, self.codes(report))
        # OFAC 50% outranks the jurisdiction finding.
        self.assertEqual(report.status, STATUS_REJECTED_OFAC_50)

    def test_every_independent_problem_appears_in_findings(self):
        report = self.audit(payload(
            incorporation_country="IRAN",
            banking_country="MC",
            ubos=[ubo("Mystery Owner", 40.0, verified=False)],
            control_person=None))

        self.assertLessEqual(
            {STATUS_REJECTED_FATF, "FATF_INCREASED_MONITORING",
             STATUS_REJECTED_UNVERIFIED_UBO, STATUS_REJECTED_NO_CONTROL_PERSON,
             STATUS_REJECTED_OWNERSHIP_OPACITY},
            self.codes(report))
        self.assertEqual(report.status, STATUS_REJECTED_FATF)

    def test_every_finding_carries_a_citation(self):
        report = self.audit(payload(incorporation_country="IRAN", control_person=None))
        for finding in report.findings:
            with self.subTest(code=finding.code):
                self.assertTrue(finding.citation.strip())


class TestValidation(KycAmlTestBase):

    def test_unrecognised_country_raises_rather_than_clearing(self):
        with self.assertRaises(KycAmlValidationError):
            self.audit(payload(incorporation_country="ATLANTIS"))

    def test_blank_country_raises(self):
        with self.assertRaises(KycAmlValidationError):
            self.audit(payload(banking_country="   "))

    def test_nan_ownership_raises_instead_of_passing_below_threshold(self):
        with self.assertRaises(KycAmlValidationError):
            self.audit(payload(ubos=[ubo("Ghost Owner", float("nan"), verified=False)]))

    def test_out_of_range_ownership_raises(self):
        for pct in (-1.0, 100.1):
            with self.subTest(pct=pct):
                with self.assertRaises(KycAmlValidationError):
                    self.audit(payload(ubos=[ubo("Alice Smith", pct)]))

    def test_cap_table_over_100_percent_raises(self):
        with self.assertRaises(KycAmlValidationError):
            self.audit(payload(ubos=[ubo("Alice Smith", 60.0), ubo("Bob Jones", 60.0)]))

    def test_blank_entity_name_raises(self):
        with self.assertRaises(KycAmlValidationError):
            self.audit(payload(entity_name=" "))

    def test_contradictory_pep_fields_raise(self):
        with self.assertRaises(KycAmlValidationError):
            self.audit(payload(ubos=[ubo("Alice Smith", 100.0, pep=True, pep_category="NONE")]))
        with self.assertRaises(KycAmlValidationError):
            self.audit(payload(ubos=[ubo("Alice Smith", 100.0, pep=False, pep_category="FOREIGN")]))

    def test_unknown_pep_category_raises(self):
        with self.assertRaises(KycAmlValidationError):
            self.audit(payload(ubos=[ubo("Alice Smith", 100.0, pep=True, pep_category="VIP")]))

    def test_control_person_without_title_raises(self):
        cp = ControlPerson(name="Dana Reyes", title="  ", is_pep=False,
                           is_sanctioned=False, is_identity_verified=True)
        with self.assertRaises(KycAmlValidationError):
            self.audit(payload(control_person=cp))

    def test_invalid_engine_configuration_raises(self):
        for kwargs in ({"ubo_ownership_threshold_pct": 0.0},
                       {"ubo_ownership_threshold_pct": 101.0},
                       {"max_unaccounted_ownership_pct": -1.0},
                       {"max_list_age_days": -1}):
            with self.subTest(kwargs=kwargs):
                with self.assertRaises(KycAmlValidationError):
                    KycAmlEntityComplianceEngine(**kwargs)


class TestCountryNormalization(unittest.TestCase):

    def test_aliases_and_codes_resolve_to_alpha2(self):
        cases = {
            "USA": "US", "united states of america": "US",
            "UK": "GB", "United Kingdom": "GB",
            "Cayman Islands": "KY", "CAYMAN_ISLANDS": "KY",
            "DPRK": "KP", "North Korea": "KP", "kp": "KP",
            "Burma": "MM", "Myanmar": "MM",
            "SG": "SG",
        }
        for raw, expected in cases.items():
            with self.subTest(raw=raw):
                self.assertEqual(normalize_country(raw), expected)

    def test_unresolvable_values_raise(self):
        for raw in ("", "   ", "ATLANTIS", "U", "USAA", None, 42):
            with self.subTest(raw=raw):
                with self.assertRaises(KycAmlValidationError):
                    normalize_country(raw)


if __name__ == "__main__":
    unittest.main()
