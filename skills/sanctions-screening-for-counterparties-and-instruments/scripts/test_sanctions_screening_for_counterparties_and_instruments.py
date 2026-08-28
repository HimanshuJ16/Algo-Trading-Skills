"""Tests for the sanctions screening engine.

The regression tests are marked REGRESSION and each names the defect it pins.
Every one of them fails against the previous implementation of this skill.
"""
import doctest
import unittest
import warnings
from datetime import date, datetime, timedelta

import sanctions_screening_for_counterparties_and_instruments as module
from sanctions_screening_for_counterparties_and_instruments import (
    DEFAULT_EMBARGOED_COUNTRIES,
    DEFAULT_EMBARGOED_TERRITORIES,
    DEMO_SANCTIONED_DATABASE,
    OFAC_AGGREGATE_BLOCKING_THRESHOLD_PCT,
    STATUS_BLOCKED_EMBARGO,
    STATUS_BLOCKED_OFAC_50,
    STATUS_BLOCKED_SANCTIONS_HIT,
    STATUS_CLEARED,
    STATUS_RESTRICTED_SECTORAL,
    STATUS_REVIEW_REQUIRED,
    ComplianceResult,
    MatchMethod,
    SanctionedEntry,
    SanctionedOwner,
    SanctionsListSnapshot,
    SanctionsListType,
    SanctionsProgram,
    SanctionsScreeningError,
    SanctionsScreeningForCounterpartiesAndInstrumentsEngine,
    ScreeningEntityKind,
    ScreeningSubject,
    build_engine,
    demo_snapshot,
    normalize_country,
    normalize_name,
    normalize_territory,
)

SCREEN_DATE = date(2026, 8, 28)
LIST_DATE = date(2026, 8, 28)


def make_engine(**kwargs):
    return SanctionsScreeningForCounterpartiesAndInstrumentsEngine(
        demo_snapshot(LIST_DATE), **kwargs)


def subject(**kwargs):
    payload = dict(
        subject_id="LEI_TEST_001",
        name="ACME TRADING LTD",
        country_iso="US",
        entity_kind=ScreeningEntityKind.COUNTERPARTY,
    )
    payload.update(kwargs)
    return ScreeningSubject(**payload)


class TestNameNormalization(unittest.TestCase):
    """Expected values derived by hand from the documented normalisation rules."""

    def test_punctuation_and_case_folded(self):
        self.assertEqual(normalize_name("VTB Bank P.J.S.C."), "VTB BANK PJSC")

    def test_accents_folded_to_ascii(self):
        self.assertEqual(normalize_name("Société Générale"), "SOCIETE GENERALE")

    def test_whitespace_collapsed_and_trimmed(self):
        self.assertEqual(normalize_name("  ACME   TRADING\tLTD  "), "ACME TRADING LTD")

    def test_non_latin_script_preserved_not_mangled(self):
        # The engine does not transliterate; it must not silently destroy the
        # name either, or the audit trail loses what was actually screened.
        self.assertEqual(normalize_name("Сбербанк"), "СБЕРБАНК")


class TestEditSimilarity(unittest.TestCase):
    """Independently derived expected values, computed by hand from the metric."""

    def test_identical_strings_are_100(self):
        self.assertEqual(module._edit_similarity_pct("ABCDE", "ABCDE"), 100.0)

    def test_one_substitution_in_five(self):
        # distance 1, max_len 5 -> (1 - 1/5) * 100 = 80.0
        self.assertEqual(module._edit_similarity_pct("ABCDE", "ABCDF"), 80.0)

    def test_one_insertion_lengthens_denominator(self):
        # "ABCD" -> "ABCDE": distance 1, max_len 5 -> 80.0
        self.assertEqual(module._edit_similarity_pct("ABCD", "ABCDE"), 80.0)

    def test_disjoint_equal_length_strings_are_zero(self):
        # 3 substitutions over max_len 3 -> (1 - 3/3) * 100 = 0.0
        self.assertEqual(module._edit_similarity_pct("ABC", "XYZ"), 0.0)

    def test_empty_against_nonempty_is_zero(self):
        self.assertEqual(module._edit_similarity_pct("", "ABC"), 0.0)

    def test_distance_is_symmetric(self):
        self.assertEqual(
            module._levenshtein_distance("KITTEN", "SITTING"),
            module._levenshtein_distance("SITTING", "KITTEN"),
        )

    def test_known_textbook_distance(self):
        # kitten -> sitting is the canonical Levenshtein example: distance 3.
        self.assertEqual(module._levenshtein_distance("KITTEN", "SITTING"), 3)

    def test_sorted_token_similarity_beats_edit_on_reorder(self):
        a, b = "PJSC SBERBANK", "SBERBANK PJSC"
        self.assertEqual(module._sorted_token_similarity_pct(a, b), 100.0)
        self.assertLess(module._edit_similarity_pct(a, b), 100.0)

    def test_length_bound_never_exceeds_true_similarity(self):
        # The short-circuit must be an upper bound, or it would skip real hits.
        pairs = [("ABCDE", "AB"), ("ACME LTD", "ACME"), ("A", "ABCDEFGHIJ")]
        for a, b in pairs:
            with self.subTest(a=a, b=b):
                self.assertGreaterEqual(
                    module._max_possible_similarity_pct(len(a), len(b)) + 1e-9,
                    module._edit_similarity_pct(a, b),
                )


class TestRegressionFuzzyPunctuationVariant(unittest.TestCase):
    """REGRESSION: the pitfall this skill documents used to CLEAR.

    "VTB BANK PJSC" vs "VTB BANK P.J.S.C." scores 76.47% on the raw strings.
    Under the documented 85% threshold the old engine returned CLEARED for a
    designated Russian bank — the exact example SKILL.md gives as the failure
    this skill prevents.
    """

    def setUp(self):
        self.engine = make_engine()

    def test_punctuation_variant_of_designated_bank_is_blocked(self):
        report = self.engine.screen_subject(
            subject(subject_id="LEI_X", name="VTB Bank P.J.S.C.", country_iso="RU"),
            screened_on=SCREEN_DATE,
        )
        self.assertFalse(report.is_cleared)
        self.assertEqual(report.status, STATUS_BLOCKED_SANCTIONS_HIT)
        self.assertEqual(report.hits[0].match_score_pct, 100.0)
        self.assertEqual(report.hits[0].match_method, MatchMethod.EXACT_NAME)

    def test_raw_strings_would_have_scored_below_threshold(self):
        # Pins *why* the old behaviour failed, so a regression in normalisation
        # cannot be masked by a threshold change.
        self.assertLess(
            module._edit_similarity_pct("VTB BANK PJSC", "VTB BANK P.J.S.C."), 85.0)

    def test_accented_variant_is_blocked(self):
        engine = build_engine(
            (SanctionedEntry("LEI_A", "SOCIETE GENERALE SA", "FR",
                             SanctionsListType.EU_CONSOLIDATED),),
            LIST_DATE,
        )
        report = engine.screen_subject(
            subject(name="Société Générale S.A.", country_iso="FR"),
            screened_on=SCREEN_DATE,
        )
        self.assertEqual(report.status, STATUS_BLOCKED_SANCTIONS_HIT)

    def test_word_order_variant_is_caught_by_token_matching(self):
        engine = build_engine(
            (SanctionedEntry("LEI_B", "SBERBANK OF RUSSIA PJSC", "RU",
                             SanctionsListType.OFAC_SDN),),
            LIST_DATE,
        )
        report = engine.screen_subject(
            subject(name="PJSC Sberbank of Russia", country_iso="RU"),
            screened_on=SCREEN_DATE,
        )
        self.assertEqual(report.status, STATUS_BLOCKED_SANCTIONS_HIT)
        self.assertEqual(report.hits[0].match_method, MatchMethod.FUZZY_TOKEN_ORDER)

    def test_alias_match_is_reported_with_the_primary_name(self):
        # "VNESHTORGBANK" is an a.k.a. of the demo VTB entry.
        report = self.engine.screen_subject(
            subject(name="Vneshtorgbank", country_iso="RU"), screened_on=SCREEN_DATE)
        self.assertEqual(report.status, STATUS_BLOCKED_SANCTIONS_HIT)
        self.assertEqual(report.hits[0].matched_sanctioned_name, "VTB BANK PJSC")
        self.assertIn("via alias", report.hits[0].reason)

    def test_unrelated_name_still_clears(self):
        # Normalisation must not have turned the engine into an everything-blocker.
        report = self.engine.screen_subject(
            subject(name="APPLE INC", country_iso="US"), screened_on=SCREEN_DATE)
        self.assertTrue(report.is_cleared)
        self.assertEqual(report.status, STATUS_CLEARED)
        self.assertEqual(report.hits, ())


class TestRegressionFailOpenInputs(unittest.TestCase):
    """REGRESSION: inputs that used to produce a confident CLEARED."""

    def setUp(self):
        self.engine = make_engine()

    def test_whitespace_padded_country_no_longer_clears(self):
        # "  KP  ".upper() was never stripped, so North Korea cleared on a space.
        report = self.engine.screen_subject(
            subject(country_iso="  KP  "), screened_on=SCREEN_DATE)
        self.assertEqual(report.status, STATUS_BLOCKED_EMBARGO)

    def test_country_name_in_wrong_namespace_no_longer_clears(self):
        # "IRAN" against a list keyed on "IR" used to fail open silently.
        report = self.engine.screen_subject(
            subject(country_iso="Iran"), screened_on=SCREEN_DATE)
        self.assertEqual(report.status, STATUS_BLOCKED_EMBARGO)

    def test_blank_country_raises_rather_than_clearing(self):
        with self.assertRaises(SanctionsScreeningError):
            self.engine.screen_subject(subject(country_iso=""), screened_on=SCREEN_DATE)

    def test_unknown_jurisdiction_placeholder_raises(self):
        for placeholder in ("XX", "ZZ", "N/A"):
            with self.subTest(placeholder=placeholder):
                with self.assertRaises(SanctionsScreeningError):
                    self.engine.screen_subject(
                        subject(country_iso=placeholder), screened_on=SCREEN_DATE)

    def test_unresolvable_country_raises(self):
        with self.assertRaises(SanctionsScreeningError):
            self.engine.screen_subject(
                subject(country_iso="ATLANTIS"), screened_on=SCREEN_DATE)

    def test_nan_ownership_raises_rather_than_clearing(self):
        # NaN >= 50.0 is False, so a NaN ownership used to sail through the gate.
        with self.assertRaises(SanctionsScreeningError):
            self.engine.screen_subject(
                subject(ownership_pct_by_sanctioned=float("nan")),
                screened_on=SCREEN_DATE)

    def test_infinite_ownership_raises(self):
        with self.assertRaises(SanctionsScreeningError):
            self.engine.screen_subject(
                subject(ownership_pct_by_sanctioned=float("inf")),
                screened_on=SCREEN_DATE)

    def test_negative_and_out_of_range_ownership_raise(self):
        for pct in (-10.0, 101.0, 5000.0):
            with self.subTest(pct=pct):
                with self.assertRaises(SanctionsScreeningError):
                    self.engine.screen_subject(
                        subject(ownership_pct_by_sanctioned=pct),
                        screened_on=SCREEN_DATE)

    def test_blank_name_raises(self):
        for name in ("", "   ", "!!!"):
            with self.subTest(name=name):
                with self.assertRaises(SanctionsScreeningError):
                    self.engine.screen_subject(
                        subject(name=name), screened_on=SCREEN_DATE)

    def test_blank_subject_id_raises(self):
        with self.assertRaises(SanctionsScreeningError):
            self.engine.screen_subject(subject(subject_id="  "), screened_on=SCREEN_DATE)


class TestRegressionEmptyDatabaseFallback(unittest.TestCase):
    """REGRESSION: `sanctions_database or DEFAULT` masked a failed feed load."""

    def test_engine_requires_an_explicit_snapshot(self):
        with self.assertRaises(SanctionsScreeningError):
            SanctionsScreeningForCounterpartiesAndInstrumentsEngine()

    def test_empty_entry_list_raises_instead_of_silently_defaulting(self):
        with self.assertRaises(SanctionsScreeningError):
            SanctionsListSnapshot(entries=(), as_of=LIST_DATE)
        with self.assertRaises(SanctionsScreeningError):
            build_engine((), LIST_DATE)

    def test_snapshot_requires_a_real_date(self):
        with self.assertRaises(SanctionsScreeningError):
            SanctionsListSnapshot(entries=DEMO_SANCTIONED_DATABASE, as_of="2026-08-28")


class TestEmbargoes(unittest.TestCase):
    def setUp(self):
        self.engine = make_engine()

    def test_comprehensive_embargo_countries_are_blocked(self):
        for code in ("CU", "IR", "KP"):
            with self.subTest(code=code):
                report = self.engine.screen_subject(
                    subject(country_iso=code), screened_on=SCREEN_DATE)
                self.assertTrue(report.has_embargo_violation)
                self.assertFalse(report.is_cleared)

    def test_syria_is_not_a_comprehensive_embargo(self):
        """REGRESSION: E.O. 14312 (2025-06-30) revoked the Syria programme and
        OFAC removed 31 CFR part 542 on 2025-08-26. Hard-blocking SY over-blocks
        on the authority of a programme that no longer exists."""
        self.assertNotIn("SY", DEFAULT_EMBARGOED_COUNTRIES)
        report = self.engine.screen_subject(
            subject(name="DAMASCUS TRADING CO", country_iso="SY"),
            screened_on=SCREEN_DATE)
        self.assertEqual(report.status, STATUS_CLEARED)

    def test_syrian_designated_party_still_blocked_by_list_screening(self):
        # Removing the country embargo must not remove targeted designations.
        engine = build_engine(
            (SanctionedEntry("SY_SDN_1", "DAMASCUS DESIGNATED CO", "SY",
                             SanctionsListType.OFAC_SDN),),
            LIST_DATE,
        )
        report = engine.screen_subject(
            subject(name="Damascus Designated Co", country_iso="SY"),
            screened_on=SCREEN_DATE)
        self.assertEqual(report.status, STATUS_BLOCKED_SANCTIONS_HIT)

    def test_territorial_embargo_detected_from_iso_3166_2_subdivision(self):
        """REGRESSION: Crimea/DNR/LNR entities report country UA, and the old
        "RU_CRIMEA" pseudo-code could never fire."""
        report = self.engine.screen_subject(
            subject(name="SEVASTOPOL PORT SERVICES", country_iso="UA",
                    region_code="UA-40"),
            screened_on=SCREEN_DATE)
        self.assertEqual(report.status, STATUS_BLOCKED_EMBARGO)
        self.assertEqual(report.hits[0].match_method, MatchMethod.TERRITORY_EMBARGO)

    def test_ukraine_without_region_is_flagged_for_review_not_cleared(self):
        report = self.engine.screen_subject(
            subject(name="KYIV TRADING LLC", country_iso="UA"), screened_on=SCREEN_DATE)
        self.assertEqual(report.status, STATUS_REVIEW_REQUIRED)
        self.assertFalse(report.is_cleared)
        self.assertTrue(any("NO_REGION_SUPPLIED" in a for a in report.advisories))

    def test_non_embargoed_ukrainian_region_clears(self):
        report = self.engine.screen_subject(
            subject(name="LVIV TRADING LLC", country_iso="UA", region_code="UA-46"),
            screened_on=SCREEN_DATE)
        self.assertEqual(report.status, STATUS_CLEARED)

    def test_region_must_belong_to_the_declared_country(self):
        with self.assertRaises(SanctionsScreeningError):
            self.engine.screen_subject(
                subject(country_iso="US", region_code="UA-43"), screened_on=SCREEN_DATE)

    def test_malformed_region_code_raises(self):
        with self.assertRaises(SanctionsScreeningError):
            self.engine.screen_subject(
                subject(country_iso="UA", region_code="CRIMEA"), screened_on=SCREEN_DATE)

    def test_default_territories_are_all_ukrainian_subdivisions(self):
        for code in DEFAULT_EMBARGOED_TERRITORIES:
            with self.subTest(code=code):
                self.assertEqual(normalize_territory(code), code)
                self.assertTrue(code.startswith("UA-"))


class TestOfac50PercentRule(unittest.TestCase):
    def setUp(self):
        self.engine = make_engine()

    def test_aggregation_across_two_blocked_owners_triggers_the_rule(self):
        """REGRESSION: OFAC aggregates. Two blocked persons at 25% each block the
        entity; comparing each holder to 50% individually never fires."""
        report = self.engine.screen_subject(
            subject(
                name="UNLISTED SUBSIDIARY LLC",
                country_iso="DE",
                sanctioned_owners=(
                    SanctionedOwner("OWNER_X", "BLOCKED PERSON X", 25.0),
                    SanctionedOwner("OWNER_Y", "BLOCKED PERSON Y", 25.0),
                ),
            ),
            screened_on=SCREEN_DATE,
        )
        self.assertEqual(report.status, STATUS_BLOCKED_OFAC_50)
        self.assertEqual(report.aggregate_sanctioned_ownership_pct, 50.0)
        self.assertTrue(report.requires_ofac_blocking_report)

    def test_exactly_50_percent_blocks_inclusive_threshold(self):
        report = self.engine.screen_subject(
            subject(country_iso="DE",
                    ownership_pct_by_sanctioned=OFAC_AGGREGATE_BLOCKING_THRESHOLD_PCT),
            screened_on=SCREEN_DATE)
        self.assertEqual(report.status, STATUS_BLOCKED_OFAC_50)

    def test_just_below_50_percent_does_not_trigger_the_rule(self):
        report = self.engine.screen_subject(
            subject(country_iso="DE", ownership_pct_by_sanctioned=49.9),
            screened_on=SCREEN_DATE)
        self.assertNotEqual(report.status, STATUS_BLOCKED_OFAC_50)
        self.assertFalse(report.requires_ofac_blocking_report)
        self.assertEqual(report.aggregate_sanctioned_ownership_pct, 49.9)

    def test_minority_blocked_ownership_is_not_blocked_property(self):
        # A minority blocked holder is a reason to decline, a different legal
        # event from the entity itself being blocked property.
        report = self.engine.screen_subject(
            subject(country_iso="DE",
                    sanctioned_owners=(SanctionedOwner("O1", "BLOCKED X", 30.0),)),
            screened_on=SCREEN_DATE)
        self.assertFalse(report.requires_ofac_blocking_report)

    def test_ownership_rule_outranks_a_list_hit_in_status(self):
        report = self.engine.screen_subject(
            subject(name="VTB BANK PJSC", country_iso="RU",
                    ownership_pct_by_sanctioned=80.0),
            screened_on=SCREEN_DATE)
        self.assertEqual(report.status, STATUS_BLOCKED_OFAC_50)
        # ...but the list hit is not concealed by the precedence.
        self.assertTrue(any(h.list_type is not None for h in report.hits))

    def test_duplicate_owner_id_raises_rather_than_double_counting(self):
        with self.assertRaises(SanctionsScreeningError):
            self.engine.screen_subject(
                subject(sanctioned_owners=(
                    SanctionedOwner("O1", "BLOCKED X", 30.0),
                    SanctionedOwner("O1", "BLOCKED X", 30.0),
                )),
                screened_on=SCREEN_DATE)

    def test_owners_exceeding_100_percent_raise(self):
        with self.assertRaises(SanctionsScreeningError):
            self.engine.screen_subject(
                subject(sanctioned_owners=(
                    SanctionedOwner("O1", "X", 60.0),
                    SanctionedOwner("O2", "Y", 60.0),
                )),
                screened_on=SCREEN_DATE)

    def test_both_channels_take_the_max_not_the_sum(self):
        # Supplying the same 60% stake twice must not become 120% (and must not
        # raise) — it is one stake expressed two ways.
        report = self.engine.screen_subject(
            subject(country_iso="DE",
                    ownership_pct_by_sanctioned=60.0,
                    sanctioned_owners=(SanctionedOwner("O1", "X", 60.0),)),
            screened_on=SCREEN_DATE)
        self.assertEqual(report.aggregate_sanctioned_ownership_pct, 60.0)


class TestSectoralVsBlocking(unittest.TestCase):
    """A sectoral designation restricts certain dealings; it is not an asset freeze."""

    def setUp(self):
        self.engine = make_engine()

    def test_sectoral_match_is_restricted_not_blocked(self):
        report = self.engine.screen_subject(
            subject(name="Demo Sectoral Energy OJSC", country_iso="RU"),
            screened_on=SCREEN_DATE)
        self.assertEqual(report.status, STATUS_RESTRICTED_SECTORAL)
        self.assertTrue(report.has_sectoral_restriction)
        self.assertFalse(report.is_cleared)

    def test_sectoral_match_does_not_set_the_blocking_flag(self):
        report = self.engine.screen_subject(
            subject(name="Demo Sectoral Energy OJSC", country_iso="RU"),
            screened_on=SCREEN_DATE)
        self.assertFalse(report.has_sanctions_hit)
        self.assertFalse(report.requires_ofac_blocking_report)

    def test_blocking_match_outranks_sectoral(self):
        report = self.engine.screen_subject(
            subject(subject_id="LEI_DEMO_004", name="VTB BANK PJSC", country_iso="RU"),
            screened_on=SCREEN_DATE)
        self.assertEqual(report.status, STATUS_BLOCKED_SANCTIONS_HIT)


class TestListStaleness(unittest.TestCase):
    def test_stale_list_downgrades_a_clear_to_review(self):
        engine = SanctionsScreeningForCounterpartiesAndInstrumentsEngine(
            demo_snapshot(date(2026, 1, 1)))
        report = engine.screen_subject(
            subject(name="APPLE INC"), screened_on=SCREEN_DATE)
        self.assertEqual(report.status, STATUS_REVIEW_REQUIRED)
        self.assertFalse(report.is_cleared)
        self.assertTrue(any("STALE_SANCTIONS_LIST" in a for a in report.advisories))

    def test_fresh_list_clears(self):
        engine = SanctionsScreeningForCounterpartiesAndInstrumentsEngine(
            demo_snapshot(SCREEN_DATE - timedelta(days=1)))
        report = engine.screen_subject(
            subject(name="APPLE INC"), screened_on=SCREEN_DATE)
        self.assertEqual(report.status, STATUS_CLEARED)

    def test_staleness_does_not_soften_a_real_block(self):
        engine = SanctionsScreeningForCounterpartiesAndInstrumentsEngine(
            demo_snapshot(date(2026, 1, 1)))
        report = engine.screen_subject(
            subject(name="VTB BANK PJSC", country_iso="RU"), screened_on=SCREEN_DATE)
        self.assertEqual(report.status, STATUS_BLOCKED_SANCTIONS_HIT)

    def test_boundary_exactly_at_the_age_limit_is_not_stale(self):
        engine = SanctionsScreeningForCounterpartiesAndInstrumentsEngine(
            demo_snapshot(SCREEN_DATE - timedelta(days=7)), max_list_age_days=7)
        report = engine.screen_subject(
            subject(name="APPLE INC"), screened_on=SCREEN_DATE)
        self.assertEqual(report.status, STATUS_CLEARED)

    def test_one_day_past_the_limit_is_stale(self):
        engine = SanctionsScreeningForCounterpartiesAndInstrumentsEngine(
            demo_snapshot(SCREEN_DATE - timedelta(days=8)), max_list_age_days=7)
        report = engine.screen_subject(
            subject(name="APPLE INC"), screened_on=SCREEN_DATE)
        self.assertEqual(report.status, STATUS_REVIEW_REQUIRED)

    def test_future_dated_list_raises(self):
        engine = SanctionsScreeningForCounterpartiesAndInstrumentsEngine(
            demo_snapshot(SCREEN_DATE + timedelta(days=1)))
        with self.assertRaises(SanctionsScreeningError):
            engine.screen_subject(subject(), screened_on=SCREEN_DATE)


class TestEngineConfigValidation(unittest.TestCase):
    def test_threshold_bounds_are_enforced(self):
        for bad in (0.0, -1.0, 100.1, float("nan"), "85"):
            with self.subTest(bad=bad):
                with self.assertRaises(SanctionsScreeningError):
                    make_engine(fuzzy_match_threshold_pct=bad)

    def test_threshold_of_100_is_allowed_exact_matching_only(self):
        engine = make_engine(fuzzy_match_threshold_pct=100.0)
        report = engine.screen_subject(
            subject(name="VTB Bank P.J.S.C.", country_iso="RU"), screened_on=SCREEN_DATE)
        # Normalisation makes this an exact match even at a 100% threshold.
        self.assertEqual(report.status, STATUS_BLOCKED_SANCTIONS_HIT)

    def test_negative_list_age_raises(self):
        with self.assertRaises(SanctionsScreeningError):
            make_engine(max_list_age_days=-1)

    def test_embargo_country_overrides_are_normalized(self):
        engine = make_engine(embargoed_countries=["Cuba", "irn"])
        self.assertEqual(engine.embargoed_countries, frozenset({"CU", "IR"}))

    def test_invalid_embargo_override_raises(self):
        with self.assertRaises(SanctionsScreeningError):
            make_engine(embargoed_countries=["ATLANTIS"])


class TestDeterminismAndAuditTrail(unittest.TestCase):
    def setUp(self):
        self.engine = make_engine()

    def test_identical_inputs_produce_identical_reports(self):
        args = (subject(name="VTB Bank P.J.S.C.", country_iso="RU"),)
        first = self.engine.screen_subject(*args, screened_on=SCREEN_DATE)
        second = self.engine.screen_subject(*args, screened_on=SCREEN_DATE)
        self.assertEqual(first, second)

    def test_identifier_and_name_match_produce_one_hit_not_two(self):
        """REGRESSION: an entry matched on both id and name was reported twice,
        inflating every hit count in the audit trail."""
        report = self.engine.screen_subject(
            subject(subject_id="LEI_DEMO_001", name="VTB BANK PJSC", country_iso="RU"),
            screened_on=SCREEN_DATE)
        matched = [h.matched_entity_id for h in report.hits
                   if h.matched_entity_id == "LEI_DEMO_001"]
        self.assertEqual(len(matched), 1)

    def test_exact_identifier_match_blocks_regardless_of_name(self):
        report = self.engine.screen_subject(
            subject(subject_id="ISIN_DEMO_RU0001", name="TOTALLY UNRELATED NAME",
                    country_iso="RU"),
            screened_on=SCREEN_DATE)
        self.assertEqual(report.status, STATUS_BLOCKED_SANCTIONS_HIT)
        self.assertEqual(report.hits[0].match_method, MatchMethod.EXACT_IDENTIFIER)

    def test_report_records_both_dates(self):
        report = self.engine.screen_subject(subject(), screened_on=SCREEN_DATE)
        self.assertEqual(report.screened_on, SCREEN_DATE)
        self.assertEqual(report.list_as_of, LIST_DATE)

    def test_instrument_issuer_kind_is_screened_the_same_way(self):
        report = self.engine.screen_subject(
            subject(subject_id="ISIN_DEMO_RU0001", name="RUSSIAN FEDERAL BOND",
                    country_iso="RU",
                    entity_kind=ScreeningEntityKind.INSTRUMENT_ISSUER),
            screened_on=SCREEN_DATE)
        self.assertEqual(report.status, STATUS_BLOCKED_SANCTIONS_HIT)

    def test_hits_are_sorted_by_descending_score(self):
        report = self.engine.screen_subject(
            subject(name="VTB BANK PJSC", country_iso="RU",
                    ownership_pct_by_sanctioned=90.0),
            screened_on=SCREEN_DATE)
        scores = [h.match_score_pct for h in report.hits]
        self.assertEqual(scores, sorted(scores, reverse=True))

    def test_report_is_immutable(self):
        report = self.engine.screen_subject(subject(), screened_on=SCREEN_DATE)
        with self.assertRaises(Exception):
            report.status = "CLEARED"


class TestAdversarial(unittest.TestCase):
    """Cases found by attacking the implementation after it was written."""

    def setUp(self):
        self.engine = make_engine()

    def test_datetime_screened_on_is_narrowed_not_crashed(self):
        # datetime subclasses date, so an unguarded datetime passed the type
        # check and then raised a bare TypeError on the staleness arithmetic.
        report = self.engine.screen_subject(
            subject(), screened_on=datetime(2026, 8, 28, 14, 30))
        self.assertEqual(report.screened_on, SCREEN_DATE)

    def test_non_date_screened_on_raises_the_domain_error(self):
        with self.assertRaises(SanctionsScreeningError):
            self.engine.screen_subject(subject(), screened_on="2026-08-28")

    def test_unmatchable_list_entry_is_rejected_at_load(self):
        # A designation whose names all normalise away is silent non-coverage:
        # it inflates the list size while screening nothing.
        with self.assertRaises(SanctionsScreeningError):
            build_engine(
                (SanctionedEntry("ID1", "   ", "RU", SanctionsListType.OFAC_SDN),),
                LIST_DATE)
        with self.assertRaises(SanctionsScreeningError):
            build_engine(
                (SanctionedEntry("ID1", "!!!", "RU", SanctionsListType.OFAC_SDN),),
                LIST_DATE)

    def test_blank_entity_id_in_list_is_rejected_at_load(self):
        with self.assertRaises(SanctionsScreeningError):
            build_engine(
                (SanctionedEntry("  ", "ACME", "RU", SanctionsListType.OFAC_SDN),),
                LIST_DATE)

    def test_same_entity_on_two_lists_reports_both_designations(self):
        # Keying dedup on entity_id alone dropped every list after the first,
        # losing the EU designation from the audit trail.
        engine = build_engine((
            SanctionedEntry("LEI_DUP", "ACME TRADING LTD", "RU",
                            SanctionsListType.OFAC_SDN),
            SanctionedEntry("LEI_DUP", "ACME TRADING LTD", "RU",
                            SanctionsListType.EU_CONSOLIDATED),
        ), LIST_DATE)
        report = engine.screen_subject(subject(), screened_on=SCREEN_DATE)
        self.assertEqual(len(report.hits), 2)
        self.assertEqual(
            {h.list_type for h in report.hits},
            {SanctionsListType.OFAC_SDN, SanctionsListType.EU_CONSOLIDATED},
        )

    def test_length_prefilter_matches_unfiltered_results(self):
        """The O(n*m) short-circuit must not change any decision."""
        engine = make_engine()
        names = ["VTB BANK PJSC", "VTB", "V", "SBERBANK OF RUSSIA LIMITED",
                 "IRAN NATIONAL OIL CO", "TOTALLY UNRELATED ENTERPRISE GMBH",
                 "DEMO SECTORAL ENERGY OJSC", "RUSSIAN FEDERAL BOND"]
        for name in names:
            with self.subTest(name=name):
                subject_norm = normalize_name(name)
                for _entry, _raw, entry_norm in engine._normalized_index:
                    filtered, _ = engine._best_name_match(subject_norm, entry_norm)
                    unfiltered = max(
                        module._edit_similarity_pct(subject_norm, entry_norm),
                        module._sorted_token_similarity_pct(subject_norm, entry_norm),
                    )
                    # The filter may only zero out scores already under the bar.
                    if unfiltered >= engine.fuzzy_threshold:
                        self.assertEqual(filtered, unfiltered)
                    else:
                        self.assertLess(filtered, engine.fuzzy_threshold)

    def test_subject_is_not_mutated_by_screening(self):
        subj = subject(country_iso="  kp  ", name="  ACME  ")
        before = (subj.country_iso, subj.name, subj.ownership_pct_by_sanctioned)
        self.engine.screen_subject(subj, screened_on=SCREEN_DATE)
        self.assertEqual(
            (subj.country_iso, subj.name, subj.ownership_pct_by_sanctioned), before)

    def test_engine_holds_no_cross_call_state(self):
        blocked = subject(name="VTB BANK PJSC", country_iso="RU")
        clean = subject(name="APPLE INC", country_iso="US")
        self.engine.screen_subject(blocked, screened_on=SCREEN_DATE)
        after = self.engine.screen_subject(clean, screened_on=SCREEN_DATE)
        self.assertEqual(after.status, STATUS_CLEARED)
        self.assertEqual(after.hits, ())

    def test_wrong_type_subject_raises_domain_error(self):
        with self.assertRaises(SanctionsScreeningError):
            self.engine.screen_subject({"name": "ACME"}, screened_on=SCREEN_DATE)

    def test_precedence_table_covers_every_non_cleared_status(self):
        # The status selector indexes `triggered` by every member of
        # _STATUS_PRECEDENCE; a member added without a trigger would KeyError on
        # the first screen rather than at import.
        self.assertEqual(
            set(module._STATUS_PRECEDENCE),
            {STATUS_BLOCKED_OFAC_50, STATUS_BLOCKED_SANCTIONS_HIT,
             STATUS_BLOCKED_EMBARGO, STATUS_RESTRICTED_SECTORAL,
             STATUS_REVIEW_REQUIRED},
        )
        self.assertNotIn(STATUS_CLEARED, module._STATUS_PRECEDENCE)

    def test_bad_entity_kind_raises(self):
        with self.assertRaises(SanctionsScreeningError):
            self.engine.screen_subject(
                subject(entity_kind="COUNTERPARTY"), screened_on=SCREEN_DATE)


class TestLegacyShim(unittest.TestCase):
    """The legacy `check()` is a hazard, so its warning is part of the contract."""

    def setUp(self):
        self.engine = make_engine()

    def test_check_emits_a_deprecation_warning(self):
        with self.assertWarns(DeprecationWarning):
            self.engine.check({"valid": True})

    def test_check_still_returns_the_legacy_shape(self):
        with warnings.catch_warnings():
            warnings.simplefilter("ignore", DeprecationWarning)
            self.assertTrue(self.engine.check({"valid": True}).is_compliant)
            self.assertFalse(self.engine.check({"valid": False}).is_compliant)
            self.assertFalse(self.engine.check({}).is_compliant)

    def test_compliance_result_still_importable(self):
        self.assertTrue(ComplianceResult(True, "Valid").is_compliant)


class TestDocstrings(unittest.TestCase):
    def test_module_doctests_pass(self):
        results = doctest.testmod(module, verbose=False)
        self.assertEqual(results.failed, 0)


if __name__ == "__main__":
    unittest.main()
