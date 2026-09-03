"""
Unit tests for the multi-jurisdiction tax residency assessment engine.

Expected values are derived from the published rule, not from the
implementation. The Substantial Presence Test figures are worked by hand from
the IRS formula (current year + 1/3 of the first preceding year + 1/6 of the
second, against 183 days, with a 31-day floor in the current year), and the
120-days-a-year example is the IRS's own worked example.
"""
import unittest
from fractions import Fraction

from multi_jurisdiction_tax_residency_implications import (
    BASIS_EFFECTIVE_MANAGEMENT,
    BASIS_INCORPORATION,
    PRESENCE_TEST_SIMPLE,
    PRESENCE_TEST_WEIGHTED_LOOKBACK,
    STATUS_DUAL_RESIDENCE_RESOLVED,
    STATUS_DUAL_RESIDENCE_UNRESOLVED,
    STATUS_NO_RESIDENCE_CLAIMED,
    STATUS_REVIEW_REQUIRED,
    STATUS_SINGLE_RESIDENCE,
    TIEBREAK_COMPETENT_AUTHORITY,
    TIEBREAK_POEM,
    US_SPT_WEIGHTS,
    CorporateResidenceRule,
    EntityProfile,
    IndividualPresence,
    IndividualPresenceRule,
    MultiJurisdictionTaxResidencyEngine,
    TreatyResidenceTieBreaker,
)


def _us_spt_rule():
    """The US Substantial Presence Test, IRC s.7701(b)(3)."""
    return IndividualPresenceRule(
        country="US",
        day_threshold=183,
        test_kind=PRESENCE_TEST_WEIGHTED_LOOKBACK,
        lookback_weights=US_SPT_WEIGHTS,
        min_days_current_year=31,
        source="IRC s.7701(b)(3)",
    )


class TestIndividualPresenceTests(unittest.TestCase):

    def setUp(self):
        self.engine = MultiJurisdictionTaxResidencyEngine()
        self.engine.register_individual_presence_rule(_us_spt_rule())

    def _assess(self, days_by_year, year=2026):
        person = IndividualPresence("CIO_1", {"US": days_by_year})
        (outcome,) = self.engine.assess_individual_presence(person, year)
        return outcome

    def test_irs_worked_example_120_days_each_year_fails(self):
        # IRS example: 120 days in each of three years is 120 + 40 + 20 = 180
        # weighted days, which does not reach 183.
        outcome = self._assess({2026: 120, 2025: 120, 2024: 120})
        self.assertEqual(outcome.weighted_days, 180.0)
        self.assertFalse(outcome.meets_registered_test)

    def test_weighted_test_catches_resident_no_single_year_reaches_183(self):
        # Regression against a flat single-year 183-day threshold. 130 days this
        # year, 180 in each of the two preceding years is 130 + 60 + 30 = 220
        # weighted days: a US resident, though no single year reaches 183.
        outcome = self._assess({2026: 130, 2025: 180, 2024: 180})
        self.assertEqual(outcome.weighted_days, 220.0)
        self.assertTrue(outcome.meets_registered_test)

    def test_thirty_one_day_floor_defeats_a_passing_weighted_total(self):
        # 20 + 366/3 + 366/6 = 20 + 122 + 61 = 203 weighted days, comfortably
        # over 183, but the current year falls short of the separate 31-day
        # minimum, so the test is not met.
        outcome = self._assess({2026: 20, 2025: 366, 2024: 366})
        self.assertEqual(outcome.weighted_days, 203.0)
        self.assertFalse(outcome.meets_registered_test)

    def test_threshold_is_inclusive_at_exactly_183(self):
        self.assertTrue(self._assess({2026: 183}).meets_registered_test)
        self.assertFalse(self._assess({2026: 182}).meets_registered_test)

    def test_exact_arithmetic_at_the_statutory_boundary(self):
        # 31 + 304/3 + 304/6 = 31 + 304/2 = 183 exactly, so the test is met.
        # Evaluated in binary floats the same sum is 182.99999999999997, which
        # would wrongly report a non-resident at the statutory boundary.
        outcome = self._assess({2026: 31, 2025: 304, 2024: 304})
        self.assertEqual(outcome.weighted_days, 183.0)
        self.assertTrue(outcome.meets_registered_test)
        self.assertLess(31 + 304 / 3 + 304 / 6, 183)

    def test_india_182_day_threshold_is_not_183(self):
        # India's basic individual test under Income-tax Act s.6(1) is 182 days.
        # A universal 183-day threshold would report this person non-resident.
        self.engine.register_individual_presence_rule(IndividualPresenceRule(
            country="IN", day_threshold=182, source="Income-tax Act s.6(1)",
        ))
        person = IndividualPresence("CIO_1", {"IN": {2026: 182}})
        (outcome,) = self.engine.assess_individual_presence(person, 2026)
        self.assertEqual(outcome.day_threshold, 182)
        self.assertTrue(outcome.meets_registered_test)

    def test_unregistered_country_is_review_required_not_a_guess(self):
        person = IndividualPresence("CIO_1", {"CH": {2026: 200}})
        (outcome,) = self.engine.assess_individual_presence(person, 2026)
        self.assertEqual(outcome.status, STATUS_REVIEW_REQUIRED)
        self.assertIsNone(outcome.weighted_days)
        self.assertIsNone(outcome.meets_registered_test)

    def test_failing_the_registered_test_is_not_a_clearance(self):
        outcome = self._assess({2026: 10, 2025: 10, 2024: 10})
        self.assertFalse(outcome.meets_registered_test)
        self.assertIn("does NOT establish non-residence", outcome.caveat)

    def test_days_outside_the_lookback_window_are_ignored(self):
        # The US test weights only the current year and the two preceding ones.
        outcome = self._assess({2026: 40, 2023: 366, 2022: 366})
        self.assertEqual(outcome.weighted_days, 40.0)
        self.assertFalse(outcome.meets_registered_test)

    def test_rejects_impossible_and_malformed_day_counts(self):
        for bad in (-1, 367):
            with self.assertRaises(ValueError):
                self.engine.assess_individual_presence(
                    IndividualPresence("P", {"US": {2026: bad}}), 2026)
        with self.assertRaises(TypeError):
            self.engine.assess_individual_presence(
                IndividualPresence("P", {"US": {2026: 182.5}}), 2026)
        with self.assertRaises(TypeError):
            self.engine.assess_individual_presence(
                IndividualPresence("P", {"US": 200}), 2026)
        with self.assertRaises(ValueError):
            self.engine.assess_individual_presence(
                IndividualPresence("  ", {"US": {2026: 10}}), 2026)

    def test_country_keys_that_collide_are_rejected_not_split(self):
        # 100 days under "US" and 100 under "us" is 200 days in the US, but
        # processing them as two countries reports two non-resident findings of
        # 100 days each -- a silent false negative on residence.
        with self.assertRaises(ValueError):
            self.engine.assess_individual_presence(
                IndividualPresence("P", {"US": {2026: 100}, "us": {2026: 100}}), 2026)


class TestPresenceRuleRegistration(unittest.TestCase):

    def setUp(self):
        self.engine = MultiJurisdictionTaxResidencyEngine()

    def test_rejects_float_weights_that_cannot_represent_one_third(self):
        with self.assertRaises(TypeError):
            self.engine.register_individual_presence_rule(IndividualPresenceRule(
                country="US", day_threshold=183,
                test_kind=PRESENCE_TEST_WEIGHTED_LOOKBACK,
                lookback_weights=(1, 1 / 3, 1 / 6),
            ))

    def test_rejects_test_kind_and_weight_count_mismatch(self):
        with self.assertRaises(ValueError):
            self.engine.register_individual_presence_rule(IndividualPresenceRule(
                country="UK", day_threshold=183, test_kind=PRESENCE_TEST_SIMPLE,
                lookback_weights=(Fraction(1), Fraction(1, 3)),
            ))
        with self.assertRaises(ValueError):
            self.engine.register_individual_presence_rule(IndividualPresenceRule(
                country="UK", day_threshold=183,
                test_kind=PRESENCE_TEST_WEIGHTED_LOOKBACK,
                lookback_weights=(Fraction(1),),
            ))

    def test_rejects_unknown_test_kind_and_nonpositive_threshold(self):
        with self.assertRaises(ValueError):
            self.engine.register_individual_presence_rule(IndividualPresenceRule(
                country="UK", day_threshold=183, test_kind="VIBES",
            ))
        with self.assertRaises(ValueError):
            self.engine.register_individual_presence_rule(IndividualPresenceRule(
                country="UK", day_threshold=0,
            ))

    def test_country_codes_are_normalised(self):
        self.engine.register_individual_presence_rule(
            IndividualPresenceRule(country="  uk ", day_threshold=183))
        person = IndividualPresence("P", {"uk": {2026: 183}})
        (outcome,) = self.engine.assess_individual_presence(person, 2026)
        self.assertEqual(outcome.country, "UK")
        self.assertTrue(outcome.meets_registered_test)


class TestCorporateResidence(unittest.TestCase):

    def setUp(self):
        self.engine = MultiJurisdictionTaxResidencyEngine()
        # Cayman asserts corporate residence on neither basis.
        self.engine.register_corporate_residence_rule(CorporateResidenceRule(
            country="KY", taxes_on_incorporation=False,
            taxes_on_effective_management=False,
            source="No Cayman Islands corporate income tax",
        ))
        for cc in ("SG", "UK", "IN"):
            self.engine.register_corporate_residence_rule(CorporateResidenceRule(
                country=cc, taxes_on_incorporation=True,
                taxes_on_effective_management=True,
            ))

    def test_cayman_incorporation_does_not_shelter_singapore_management(self):
        # The headline case: a Cayman-incorporated fund actually managed from
        # Singapore is resident in Singapore, because Cayman asserts nothing and
        # Singapore taxes on place of effective management.
        report = self.engine.assess_entity(
            EntityProfile("CAYMAN_FUND_LTD", "KY", "SG"), 2026)
        self.assertEqual(report.status, STATUS_SINGLE_RESIDENCE)
        self.assertEqual(report.resolved_residence_country, "SG")
        self.assertEqual(report.claiming_countries, ["SG"])
        self.assertFalse(report.treaty_benefits_at_risk)
        (claim,) = report.residency_claims
        self.assertEqual(claim.bases, [BASIS_EFFECTIVE_MANAGEMENT])

    def test_single_country_claiming_on_both_bases(self):
        report = self.engine.assess_entity(EntityProfile("UK_LTD", "UK", "UK"), 2026)
        self.assertEqual(report.status, STATUS_SINGLE_RESIDENCE)
        (claim,) = report.residency_claims
        self.assertEqual(
            claim.bases, [BASIS_INCORPORATION, BASIS_EFFECTIVE_MANAGEMENT])

    def test_no_jurisdiction_claims_residence(self):
        report = self.engine.assess_entity(EntityProfile("KY_LTD", "KY", "KY"), 2026)
        self.assertEqual(report.status, STATUS_NO_RESIDENCE_CLAIMED)
        self.assertIsNone(report.resolved_residence_country)
        self.assertIn("economic substance", report.audit_notes)

    def test_unregistered_jurisdiction_raises_an_action_not_a_claim(self):
        report = self.engine.assess_entity(EntityProfile("X_LTD", "BM", "SG"), 2026)
        self.assertEqual(report.claiming_countries, ["SG"])
        self.assertTrue(any("Register the corporate residence rule for BM" in a
                            for a in report.required_actions))

    def test_rejects_malformed_entity_input(self):
        with self.assertRaises(ValueError):
            self.engine.assess_entity(EntityProfile("", "UK"), 2026)
        with self.assertRaises(ValueError):
            self.engine.assess_entity(EntityProfile("X", "   "), 2026)
        with self.assertRaises(TypeError):
            self.engine.assess_entity(EntityProfile("X", "UK"), "2026")


class TestDualResidenceTieBreaker(unittest.TestCase):

    def setUp(self):
        self.engine = MultiJurisdictionTaxResidencyEngine()
        for cc in ("SG", "UK", "IN"):
            self.engine.register_corporate_residence_rule(CorporateResidenceRule(
                country=cc, taxes_on_incorporation=True,
                taxes_on_effective_management=True,
            ))

    def test_map_tie_breaker_is_unresolved_until_authorities_agree(self):
        # Under the 2017 OECD Model Art. 4(3) and MLI Art. 4 the tie is not
        # broken automatically in favour of the place of effective management:
        # it goes to the competent authorities, and absent agreement the entity
        # gets no relief under the treaty.
        self.engine.register_treaty_tie_breaker(TreatyResidenceTieBreaker(
            "SG", "UK", TIEBREAK_COMPETENT_AUTHORITY, source="MLI Art. 4"))
        report = self.engine.assess_entity(EntityProfile("DUAL_LTD", "SG", "UK"), 2026)
        self.assertEqual(report.status, STATUS_DUAL_RESIDENCE_UNRESOLVED)
        self.assertIsNone(report.resolved_residence_country)
        self.assertTrue(report.treaty_benefits_at_risk)
        self.assertTrue(any("mutual agreement procedure" in a
                            for a in report.required_actions))

    def test_map_tie_breaker_resolves_on_a_concluded_determination(self):
        self.engine.register_treaty_tie_breaker(TreatyResidenceTieBreaker(
            "SG", "UK", TIEBREAK_COMPETENT_AUTHORITY))
        report = self.engine.assess_entity(
            EntityProfile("DUAL_LTD", "SG", "UK",
                          competent_authority_determination="uk"), 2026)
        self.assertEqual(report.status, STATUS_DUAL_RESIDENCE_RESOLVED)
        self.assertEqual(report.resolved_residence_country, "UK")
        self.assertFalse(report.treaty_benefits_at_risk)

    def test_legacy_poem_tie_breaker_resolves_to_management_country(self):
        # Pre-BEPS OECD Model and UN Model treaties do break the tie on place of
        # effective management, and many remain in force.
        self.engine.register_treaty_tie_breaker(TreatyResidenceTieBreaker(
            "SG", "UK", TIEBREAK_POEM, source="older treaty text"))
        report = self.engine.assess_entity(EntityProfile("LEGACY_LTD", "SG", "UK"), 2026)
        self.assertEqual(report.status, STATUS_DUAL_RESIDENCE_RESOLVED)
        self.assertEqual(report.resolved_residence_country, "UK")
        self.assertEqual(report.tie_breaker_method, TIEBREAK_POEM)

    def test_no_registered_tie_breaker_is_review_required(self):
        report = self.engine.assess_entity(EntityProfile("DUAL_LTD", "SG", "UK"), 2026)
        self.assertEqual(report.status, STATUS_REVIEW_REQUIRED)
        self.assertTrue(report.treaty_benefits_at_risk)
        self.assertIsNone(report.resolved_residence_country)

    def test_poem_tie_breaker_unresolved_when_management_is_unknown(self):
        # A tie-breaker that turns on place of effective management cannot be
        # applied when that place has not been established.
        self.engine.register_treaty_tie_breaker(TreatyResidenceTieBreaker(
            "SG", "IN", TIEBREAK_POEM))
        self.engine.register_corporate_residence_rule(CorporateResidenceRule(
            country="SG", taxes_on_incorporation=True,
            taxes_on_effective_management=False))
        report = self.engine.assess_entity(EntityProfile("L", "SG", "IN"), 2026)
        self.assertEqual(report.claiming_countries, ["IN", "SG"])
        self.assertEqual(report.resolved_residence_country, "IN")

    def test_determination_must_name_one_of_the_claiming_jurisdictions(self):
        self.engine.register_treaty_tie_breaker(TreatyResidenceTieBreaker(
            "SG", "UK", TIEBREAK_COMPETENT_AUTHORITY))
        with self.assertRaises(ValueError):
            self.engine.assess_entity(
                EntityProfile("DUAL_LTD", "SG", "UK",
                              competent_authority_determination="DE"), 2026)

    def test_tie_breaker_registration_rejects_a_single_country_pair(self):
        with self.assertRaises(ValueError):
            self.engine.register_treaty_tie_breaker(TreatyResidenceTieBreaker(
                "UK", "uk", TIEBREAK_POEM))
        with self.assertRaises(ValueError):
            self.engine.register_treaty_tie_breaker(TreatyResidenceTieBreaker(
                "SG", "UK", "COIN_FLIP"))

    def test_tie_breaker_lookup_is_order_independent(self):
        self.engine.register_treaty_tie_breaker(TreatyResidenceTieBreaker(
            "UK", "SG", TIEBREAK_POEM))
        report = self.engine.assess_entity(EntityProfile("L", "SG", "UK"), 2026)
        self.assertEqual(report.resolved_residence_country, "UK")


class TestPermanentEstablishmentFlags(unittest.TestCase):

    def setUp(self):
        self.engine = MultiJurisdictionTaxResidencyEngine()
        for cc in ("SG", "UK", "DE"):
            self.engine.register_corporate_residence_rule(CorporateResidenceRule(
                country=cc, taxes_on_incorporation=True,
                taxes_on_effective_management=True))

    def test_colocated_server_outside_residence_is_flagged(self):
        report = self.engine.assess_entity(EntityProfile(
            "SG_FUND", "SG", "SG",
            fixed_places_of_business={
                "UK": "owned co-located execution server in LD4",
                "SG": "head office",
            }), 2026)
        self.assertEqual([f.country for f in report.permanent_establishment_flags], ["UK"])
        self.assertIn("preparatory or auxiliary",
                      report.permanent_establishment_flags[0].assessment_required)

    def test_unattended_equipment_is_not_excused_by_absence_of_staff(self):
        report = self.engine.assess_entity(EntityProfile(
            "SG_FUND", "SG", "SG",
            fixed_places_of_business={"DE": "unmanned FPGA rack, Eurex co-location"}),
            2026)
        (flag,) = report.permanent_establishment_flags
        self.assertIn("human intervention is not required", flag.assessment_required)

    def test_places_of_business_require_a_description(self):
        with self.assertRaises(ValueError):
            self.engine.assess_entity(EntityProfile(
                "SG_FUND", "SG", "SG", fixed_places_of_business={"UK": "  "}), 2026)

    def test_colliding_place_of_business_keys_are_rejected(self):
        with self.assertRaises(ValueError):
            self.engine.assess_entity(EntityProfile(
                "SG_FUND", "SG", "SG",
                fixed_places_of_business={"UK": "LD4 rack", "uk": "LD4 cage"}), 2026)

    def test_unresolved_residence_flags_every_place_of_business(self):
        # With residence unresolved there is no "home" country to exclude, so
        # every fixed place of business stays in scope for analysis.
        self.engine.register_treaty_tie_breaker(TreatyResidenceTieBreaker(
            "SG", "UK", TIEBREAK_COMPETENT_AUTHORITY))
        report = self.engine.assess_entity(EntityProfile(
            "DUAL", "SG", "UK",
            fixed_places_of_business={"SG": "office", "DE": "rack"}), 2026)
        self.assertEqual(report.status, STATUS_DUAL_RESIDENCE_UNRESOLVED)
        self.assertEqual(
            [f.country for f in report.permanent_establishment_flags], ["DE", "SG"])


class TestEntityAndIndividualInteraction(unittest.TestCase):

    def test_decision_maker_resident_elsewhere_raises_a_management_action(self):
        engine = MultiJurisdictionTaxResidencyEngine()
        engine.register_corporate_residence_rule(CorporateResidenceRule(
            country="SG", taxes_on_incorporation=True,
            taxes_on_effective_management=True))
        engine.register_individual_presence_rule(_us_spt_rule())
        report = engine.assess_entity(
            EntityProfile("SG_FUND", "SG", "SG"), 2026,
            [IndividualPresence("CIO_1", {"US": {2026: 200}}, role="CIO")],
        )
        self.assertTrue(report.individual_findings[0].meets_registered_test)
        self.assertTrue(any("place of effective management" in a
                            for a in report.required_actions))

    def test_unassessed_jurisdiction_reaches_the_action_list(self):
        # A decision-maker spending 300 days somewhere with no registered rule is
        # the effective-management risk this engine exists to surface, so it must
        # appear in required_actions and not only in the per-person findings.
        engine = MultiJurisdictionTaxResidencyEngine()
        engine.register_corporate_residence_rule(CorporateResidenceRule(
            country="SG", taxes_on_incorporation=True,
            taxes_on_effective_management=True))
        report = engine.assess_entity(
            EntityProfile("SG_FUND", "SG", "SG"), 2026,
            [IndividualPresence("CIO_1", {"CH": {2026: 300}})],
        )
        self.assertEqual(report.individual_findings[0].status, STATUS_REVIEW_REQUIRED)
        self.assertTrue(any("Register presence rules for CH" in a
                            for a in report.required_actions))

    def test_no_management_action_when_decision_maker_sits_in_residence_country(self):
        engine = MultiJurisdictionTaxResidencyEngine()
        engine.register_corporate_residence_rule(CorporateResidenceRule(
            country="SG", taxes_on_incorporation=True,
            taxes_on_effective_management=True))
        engine.register_individual_presence_rule(IndividualPresenceRule(
            country="SG", day_threshold=183))
        report = engine.assess_entity(
            EntityProfile("SG_FUND", "SG", "SG"), 2026,
            [IndividualPresence("CIO_1", {"SG": {2026: 300}})],
        )
        self.assertEqual(report.required_actions, [])


if __name__ == "__main__":
    unittest.main()
