"""Behavioural tests for the multi-jurisdiction retention engine.

Expected dates and years are derived from the cited rules and from calendar
arithmetic done by hand, never by re-running the engine's own expression.
"""

import unittest
from datetime import date

from record_retention_periods_by_jurisdiction import (
    ClockStart,
    DEFAULT_RETENTION_RULES,
    RecordClass,
    RecordRetentionPeriodsByJurisdictionEngine,
    RetentionError,
    RetentionRecord,
    RetentionRule,
    RetentionStatus,
)


def make_record(**overrides):
    kwargs = dict(
        record_id="REC_001",
        record_class=RecordClass.TRADE_AND_LEDGER,
        jurisdictions=["US"],
        creation_date="2019-03-15",
    )
    kwargs.update(overrides)
    return RetentionRecord(**kwargs)


class TestBuiltInRuleTable(unittest.TestCase):
    """The figures themselves are the highest-risk part of this skill."""

    def setUp(self):
        self.engine = RecordRetentionPeriodsByJurisdictionEngine()

    def test_us_ledger_floor_is_six_years_not_seven(self):
        # Regression: v1.0.0 asserted a flat 7-year US floor citing Rule 17a-4.
        # 17 CFR 240.17a-4(a) is six years, first two readily accessible.
        rule = self.engine.rule_for("US", RecordClass.TRADE_AND_LEDGER)
        self.assertEqual(rule.min_years, 6)
        self.assertEqual(rule.accessible_years, 2)
        self.assertIn("17a-4(a)", rule.citation)

    def test_us_communications_floor_is_three_years(self):
        # 17 CFR 240.17a-4(b)(4). A single per-country number cannot express
        # this alongside the six-year ledger floor.
        rule = self.engine.rule_for("US", RecordClass.COMMUNICATION)
        self.assertEqual(rule.min_years, 3)
        self.assertIn("17a-4(b)(4)", rule.citation)

    def test_us_client_account_measures_from_account_closure(self):
        rule = self.engine.rule_for("US", RecordClass.CLIENT_ACCOUNT)
        self.assertEqual(rule.min_years, 6)
        self.assertIs(rule.clock_start, ClockStart.ACCOUNT_CLOSURE)

    def test_india_ledger_floor_is_eight_years_and_others_five(self):
        # Regression: v1.0.0 asserted a flat 8-year SEBI floor. SEBI (Stock
        # Brokers) Regulations 1992 reg. 18 is five years; the eight-year
        # figure comes from Companies Act 2013 s.128(5) and applies to books
        # of account only.
        ledger = self.engine.rule_for("IN", RecordClass.TRADE_AND_LEDGER)
        comms = self.engine.rule_for("IN", RecordClass.COMMUNICATION)
        self.assertEqual(ledger.min_years, 8)
        self.assertIn("128(5)", ledger.citation)
        self.assertEqual(comms.min_years, 5)
        self.assertIn("reg. 18", comms.citation)

    def test_uk_and_eu_carry_a_seven_year_extension(self):
        # SYSC 9.1.2R / MiFID II Art. 16(6): five years, up to seven where the
        # competent authority requests it.
        for jurisdiction in ("UK", "EU"):
            rule = self.engine.rule_for(jurisdiction, RecordClass.TRADE_AND_LEDGER)
            self.assertEqual(rule.min_years, 5, jurisdiction)
            self.assertEqual(rule.extension_years, 7, jurisdiction)

    def test_australia_floor_is_seven_years(self):
        rule = self.engine.rule_for("AU", RecordClass.TRADE_AND_LEDGER)
        self.assertEqual(rule.min_years, 7)

    def test_every_default_rule_carries_a_citation_and_authority(self):
        for rule in DEFAULT_RETENTION_RULES:
            with self.subTest(rule=f"{rule.jurisdiction}/{rule.record_class.value}"):
                self.assertTrue(rule.citation.strip())
                self.assertTrue(rule.authority.strip())

    def test_every_jurisdiction_covers_every_record_class(self):
        # A gap would silently fall through to the OTHER rule; confirm the
        # fallback is never load-bearing by accident.
        for jurisdiction in self.engine.jurisdictions:
            for record_class in RecordClass:
                with self.subTest(jurisdiction=jurisdiction, record_class=record_class):
                    self.assertIsNotNone(self.engine.rule_for(jurisdiction, record_class))


class TestPurgeDateArithmetic(unittest.TestCase):
    def setUp(self):
        self.engine = RecordRetentionPeriodsByJurisdictionEngine()

    def test_purge_date_is_creation_plus_six_calendar_years(self):
        record = make_record(creation_date="2019-03-15")
        result = self.engine.assess(record, as_of=date(2024, 1, 1))
        self.assertEqual(result.earliest_permissible_purge_date, date(2025, 3, 15))
        self.assertEqual(result.required_years, 6)
        self.assertIs(result.status, RetentionStatus.RETAIN)

    def test_readily_accessible_sub_period_is_reported(self):
        # 17a-4(a): first two years in an easily accessible place.
        result = self.engine.assess(make_record(creation_date="2019-03-15"),
                                    as_of=date(2024, 1, 1))
        self.assertEqual(result.readily_accessible_until, date(2021, 3, 15))

    def test_exact_boundary_day_is_eligible_and_day_before_is_not(self):
        record = make_record(creation_date="2019-03-15")
        day_before = self.engine.assess(record, as_of=date(2025, 3, 14))
        boundary = self.engine.assess(record, as_of=date(2025, 3, 15))
        self.assertIs(day_before.status, RetentionStatus.RETAIN)
        self.assertEqual(day_before.days_until_eligible, 1)
        self.assertIs(boundary.status, RetentionStatus.ELIGIBLE_FOR_REVIEW)
        self.assertEqual(boundary.days_until_eligible, 0)

    def test_leap_day_creation_maps_onto_28_february(self):
        # 2020-02-29 + 6 years lands in 2026, not a leap year.
        record = make_record(creation_date="2020-02-29")
        result = self.engine.assess(record, as_of=date(2021, 1, 1))
        self.assertEqual(result.earliest_permissible_purge_date, date(2026, 2, 28))

    def test_calendar_years_not_365_day_years(self):
        # 2016-01-01 + 6 calendar years = 2022-01-01 (2192 days: 2016 and 2020
        # are leap years). A 365*6 = 2190-day approximation would land on
        # 2021-12-30 and authorise a purge two days early.
        record = make_record(creation_date="2016-01-01")
        result = self.engine.assess(record, as_of=date(2021, 12, 31))
        self.assertEqual(result.earliest_permissible_purge_date, date(2022, 1, 1))
        self.assertIs(result.status, RetentionStatus.RETAIN)

    def test_days_until_eligible_never_goes_negative(self):
        result = self.engine.assess(make_record(creation_date="2000-01-01"),
                                    as_of=date(2026, 1, 1))
        self.assertEqual(result.days_until_eligible, 0)


class TestMultiJurisdictionResolution(unittest.TestCase):
    def setUp(self):
        self.engine = RecordRetentionPeriodsByJurisdictionEngine()

    def test_strictest_jurisdiction_governs(self):
        # UK five years vs AU seven years on the same record: AU governs.
        record = make_record(jurisdictions=["UK", "AU"], creation_date="2019-03-15")
        result = self.engine.assess(record, as_of=date(2024, 1, 1))
        self.assertEqual(result.binding_jurisdiction, "AU")
        self.assertEqual(result.required_years, 7)
        self.assertEqual(result.earliest_permissible_purge_date, date(2026, 3, 15))

    def test_jurisdiction_order_does_not_change_the_outcome(self):
        earlier = self.engine.assess(
            make_record(jurisdictions=["AU", "UK"]), as_of=date(2024, 1, 1))
        later = self.engine.assess(
            make_record(record_id="REC_002", jurisdictions=["UK", "AU"]),
            as_of=date(2024, 1, 1))
        self.assertEqual(earlier.earliest_permissible_purge_date,
                         later.earliest_permissible_purge_date)

    def test_all_applicable_citations_are_reported_not_only_the_binding_one(self):
        result = self.engine.assess(
            make_record(jurisdictions=["UK", "AU"]), as_of=date(2024, 1, 1))
        self.assertEqual(len(result.applied_citations), 2)
        self.assertTrue(any(c.startswith("UK:") for c in result.applied_citations))
        self.assertTrue(any(c.startswith("AU:") for c in result.applied_citations))

    def test_accessible_sub_period_survives_a_stricter_jurisdiction_binding(self):
        # UK (5 years, no accessible sub-period) sets the longer floor with the
        # extension; the US 17a-4(a) two-year accessible obligation still binds.
        engine = RecordRetentionPeriodsByJurisdictionEngine(extension_requested=["UK"])
        record = make_record(jurisdictions=["US", "UK"], creation_date="2019-03-15")
        result = engine.assess(record, as_of=date(2024, 1, 1))
        self.assertEqual(result.binding_jurisdiction, "UK")
        self.assertEqual(result.readily_accessible_until, date(2021, 3, 15))

    def test_one_unknown_jurisdiction_makes_the_whole_record_indeterminate(self):
        # The dangerous alternative is reporting the known jurisdiction's date
        # and letting a purge run against an unmodelled obligation.
        record = make_record(jurisdictions=["US", "ZZ"], creation_date="2010-01-01")
        result = self.engine.assess(record, as_of=date(2026, 1, 1))
        self.assertIs(result.status, RetentionStatus.INDETERMINATE)
        self.assertIsNone(result.earliest_permissible_purge_date)
        self.assertIsNone(result.required_years)
        self.assertTrue(any("ZZ" in issue for issue in result.issues))

    def test_jurisdiction_codes_are_case_and_whitespace_insensitive(self):
        result = self.engine.assess(make_record(jurisdictions=[" us "]),
                                    as_of=date(2024, 1, 1))
        self.assertEqual(result.jurisdictions, ("US",))
        self.assertIs(result.status, RetentionStatus.RETAIN)

    def test_duplicate_jurisdiction_codes_are_collapsed(self):
        record = make_record(jurisdictions=["US", "us", "US"])
        self.assertEqual(record.jurisdictions, ("US",))


class TestClockStart(unittest.TestCase):
    def setUp(self):
        self.engine = RecordRetentionPeriodsByJurisdictionEngine()

    def test_account_closure_rule_uses_the_supplied_clock_start_date(self):
        # 17a-4(e)(5): six years from account closure. Created 2015, closed
        # 2025 -> retained to 2031, not 2021.
        record = make_record(
            record_class=RecordClass.CLIENT_ACCOUNT,
            creation_date="2015-06-01",
            clock_start_date="2025-06-01")
        result = self.engine.assess(record, as_of=date(2026, 1, 1))
        self.assertEqual(result.retention_start, date(2025, 6, 1))
        self.assertEqual(result.earliest_permissible_purge_date, date(2031, 6, 1))
        self.assertIs(result.status, RetentionStatus.RETAIN)

    def test_missing_clock_start_date_is_indeterminate_not_measured_from_creation(self):
        record = make_record(
            record_class=RecordClass.CLIENT_ACCOUNT, creation_date="2015-06-01")
        result = self.engine.assess(record, as_of=date(2026, 1, 1))
        self.assertIs(result.status, RetentionStatus.INDETERMINATE)
        self.assertIsNone(result.earliest_permissible_purge_date)
        self.assertTrue(any("clock_start_date" in issue for issue in result.issues))

    def test_clock_start_date_is_ignored_by_creation_based_rules(self):
        record = make_record(creation_date="2019-03-15", clock_start_date="2024-01-01")
        result = self.engine.assess(record, as_of=date(2024, 6, 1))
        self.assertEqual(result.retention_start, date(2019, 3, 15))


class TestExtensionRequested(unittest.TestCase):
    def test_uk_extension_lifts_the_floor_from_five_to_seven(self):
        base = RecordRetentionPeriodsByJurisdictionEngine()
        extended = RecordRetentionPeriodsByJurisdictionEngine(extension_requested=["uk"])
        record = make_record(jurisdictions=["UK"], creation_date="2019-03-15")
        self.assertEqual(base.assess(record, date(2024, 1, 1)).required_years, 5)
        result = extended.assess(record, date(2024, 1, 1))
        self.assertEqual(result.required_years, 7)
        self.assertEqual(result.earliest_permissible_purge_date, date(2026, 3, 15))

    def test_malformed_extension_codes_are_rejected(self):
        for bad in ([""], [None], [7]):
            with self.subTest(value=bad):
                with self.assertRaises(RetentionError):
                    RecordRetentionPeriodsByJurisdictionEngine(extension_requested=bad)

    def test_extension_does_not_apply_to_jurisdictions_without_one(self):
        engine = RecordRetentionPeriodsByJurisdictionEngine(extension_requested=["US"])
        result = engine.assess(make_record(jurisdictions=["US"]), date(2024, 1, 1))
        self.assertEqual(result.required_years, 6)


class TestLegalHold(unittest.TestCase):
    def setUp(self):
        self.engine = RecordRetentionPeriodsByJurisdictionEngine()

    def test_legal_hold_overrides_an_elapsed_retention_period(self):
        record = make_record(creation_date="2000-01-01", legal_hold=True)
        result = self.engine.assess(record, as_of=date(2026, 1, 1))
        self.assertIs(result.status, RetentionStatus.LEGAL_HOLD)
        # The computed floor is still reported, so the hold's effect is visible.
        self.assertEqual(result.earliest_permissible_purge_date, date(2006, 1, 1))

    def test_legal_hold_on_an_indeterminate_record_stays_indeterminate(self):
        record = make_record(jurisdictions=["ZZ"], legal_hold=True)
        result = self.engine.assess(record, as_of=date(2026, 1, 1))
        self.assertIs(result.status, RetentionStatus.INDETERMINATE)
        self.assertTrue(any("Legal hold" in issue for issue in result.issues))


class TestPolicyShortfall(unittest.TestCase):
    def setUp(self):
        self.engine = RecordRetentionPeriodsByJurisdictionEngine()

    def test_policy_below_the_floor_is_flagged_with_the_deficit(self):
        record = make_record(policy_retention_years=5.0)  # US ledger floor is 6
        result = self.engine.assess(record, as_of=date(2024, 1, 1))
        self.assertEqual(result.policy_shortfall_years, 1.0)
        self.assertTrue(any("short of" in issue for issue in result.issues))

    def test_policy_exactly_at_the_floor_is_not_flagged(self):
        result = self.engine.assess(make_record(policy_retention_years=6.0),
                                    as_of=date(2024, 1, 1))
        self.assertIsNone(result.policy_shortfall_years)
        self.assertEqual(result.issues, ())

    def test_policy_above_the_floor_is_not_flagged(self):
        result = self.engine.assess(make_record(policy_retention_years=10.0),
                                    as_of=date(2024, 1, 1))
        self.assertIsNone(result.policy_shortfall_years)

    def test_shortfall_is_measured_against_the_strictest_jurisdiction(self):
        record = make_record(jurisdictions=["UK", "AU"], policy_retention_years=5.0)
        result = self.engine.assess(record, as_of=date(2024, 1, 1))
        self.assertEqual(result.policy_shortfall_years, 2.0)  # AU 7 - 5

    def test_shortfall_is_not_reported_against_a_partially_resolved_floor(self):
        # US(6) + unknown ZZ: reporting a 1-year gap against the US floor alone
        # would understate an obligation that has not been resolved at all.
        record = make_record(jurisdictions=["US", "ZZ"], policy_retention_years=5.0)
        result = self.engine.assess(record, as_of=date(2024, 1, 1))
        self.assertIs(result.status, RetentionStatus.INDETERMINATE)
        self.assertIsNone(result.policy_shortfall_years)
        self.assertTrue(any("not compared" in issue for issue in result.issues))

    def test_fractional_shortfall_is_not_rounded_away(self):
        # v1.0.0 rounded the surplus/deficit to one decimal place.
        record = make_record(policy_retention_years=5.99)
        result = self.engine.assess(record, as_of=date(2024, 1, 1))
        self.assertAlmostEqual(result.policy_shortfall_years, 0.01, places=6)


class TestInputValidation(unittest.TestCase):
    def setUp(self):
        self.engine = RecordRetentionPeriodsByJurisdictionEngine()

    def test_bare_string_jurisdiction_is_rejected(self):
        # "US" as a Sequence iterates to ('U', 'S'), silently losing the rule.
        with self.assertRaises(RetentionError):
            make_record(jurisdictions="US")

    def test_empty_jurisdiction_list_is_rejected(self):
        with self.assertRaises(RetentionError):
            make_record(jurisdictions=[])

    def test_blank_record_id_is_rejected(self):
        with self.assertRaises(RetentionError):
            make_record(record_id="   ")

    def test_string_record_class_is_rejected(self):
        with self.assertRaises(RetentionError):
            make_record(record_class="TRADE")

    def test_unparseable_creation_date_is_rejected(self):
        for bad in ("15/03/2019", "not-a-date", "", None, 20190315):
            with self.subTest(value=bad):
                with self.assertRaises(RetentionError):
                    make_record(creation_date=bad)

    def test_naive_datetime_is_rejected_but_offset_aware_is_accepted(self):
        with self.assertRaises(RetentionError):
            make_record(creation_date="2019-03-15T23:30:00")
        record = make_record(creation_date="2019-03-15T23:30:00+00:00")
        result = self.engine.assess(record, as_of=date(2024, 1, 1))
        self.assertEqual(result.retention_start, date(2019, 3, 15))

    def test_offset_is_normalised_to_utc_before_taking_the_date(self):
        # 2019-03-15T23:30-05:00 is 2019-03-16T04:30Z.
        record = make_record(creation_date="2019-03-15T23:30:00-05:00")
        result = self.engine.assess(record, as_of=date(2024, 1, 1))
        self.assertEqual(result.retention_start, date(2019, 3, 16))

    def test_negative_or_non_finite_policy_years_are_rejected(self):
        for bad in (-1.0, float("nan"), float("inf"), "6", True):
            with self.subTest(value=bad):
                with self.assertRaises(RetentionError):
                    make_record(policy_retention_years=bad)

    def test_datetime_as_of_is_rejected(self):
        from datetime import datetime
        with self.assertRaises(RetentionError):
            self.engine.assess(make_record(), as_of=datetime(2024, 1, 1))

    def test_duplicate_record_ids_in_a_batch_are_rejected(self):
        records = [make_record(), make_record()]
        with self.assertRaises(RetentionError):
            self.engine.assess_all(records, as_of=date(2024, 1, 1))

    def test_invalid_rule_definitions_are_rejected(self):
        with self.assertRaises(RetentionError):
            RetentionRule("US", RecordClass.OTHER, -1, ClockStart.RECORD_CREATION, "a", "b")
        with self.assertRaises(RetentionError):
            # accessible sub-period longer than the retention period itself
            RetentionRule("US", RecordClass.OTHER, 3, ClockStart.RECORD_CREATION,
                          "a", "b", accessible_years=5)
        with self.assertRaises(RetentionError):
            RetentionRule("UK", RecordClass.OTHER, 5, ClockStart.RECORD_CREATION,
                          "a", "b", extension_years=3)

    def test_empty_rule_table_is_rejected(self):
        with self.assertRaises(RetentionError):
            RecordRetentionPeriodsByJurisdictionEngine(rules=[])


class TestCustomRules(unittest.TestCase):
    def test_a_later_rule_overrides_an_earlier_one_for_the_same_key(self):
        override = RetentionRule(
            jurisdiction="US",
            record_class=RecordClass.ORDER_AUDIT_TRAIL,
            min_years=3,
            clock_start=ClockStart.RECORD_CREATION,
            authority="SEC",
            citation="17 CFR 240.17a-4(b)(1) - non-FINRA-member firm",
            accessible_years=2,
        )
        engine = RecordRetentionPeriodsByJurisdictionEngine(
            rules=list(DEFAULT_RETENTION_RULES) + [override])
        rule = engine.rule_for("US", RecordClass.ORDER_AUDIT_TRAIL)
        self.assertEqual(rule.min_years, 3)

    def test_unmodelled_class_falls_back_to_the_jurisdiction_other_rule(self):
        engine = RecordRetentionPeriodsByJurisdictionEngine(rules=[
            RetentionRule("ZA", RecordClass.OTHER, 5, ClockStart.RECORD_CREATION,
                          "FSCA", "FMA 2012"),
        ])
        rule = engine.rule_for("ZA", RecordClass.COMMUNICATION)
        self.assertEqual(rule.min_years, 5)
        self.assertIs(rule.record_class, RecordClass.OTHER)


class TestBatchReport(unittest.TestCase):
    def setUp(self):
        self.engine = RecordRetentionPeriodsByJurisdictionEngine()

    def test_counts_partition_the_batch(self):
        records = [
            make_record(record_id="RETAIN", creation_date="2024-01-01"),
            make_record(record_id="ELIGIBLE", creation_date="2000-01-01"),
            make_record(record_id="HELD", creation_date="2000-01-01", legal_hold=True),
            make_record(record_id="UNKNOWN", jurisdictions=["ZZ"]),
        ]
        report = self.engine.assess_all(records, as_of=date(2026, 1, 1))
        self.assertEqual(report.total_records, 4)
        self.assertEqual(report.retain_count, 1)
        self.assertEqual(report.eligible_for_review_count, 1)
        self.assertEqual(report.legal_hold_count, 1)
        self.assertEqual(report.indeterminate_count, 1)
        self.assertEqual(
            report.retain_count + report.eligible_for_review_count
            + report.legal_hold_count + report.indeterminate_count,
            report.total_records)

    def test_records_still_inside_their_period_are_not_reported_as_issues(self):
        report = self.engine.assess_all(
            [make_record(creation_date="2024-01-01")], as_of=date(2025, 1, 1))
        self.assertEqual(report.overall_status, "NO_ISSUES_FOUND")

    def test_policy_shortfall_alone_raises_issues_found(self):
        report = self.engine.assess_all(
            [make_record(creation_date="2024-01-01", policy_retention_years=1.0)],
            as_of=date(2025, 1, 1))
        self.assertEqual(report.overall_status, "ISSUES_FOUND")
        self.assertEqual(report.policy_shortfall_count, 1)

    def test_audit_notes_warn_that_eligibility_is_not_deletion_approval(self):
        report = self.engine.assess_all(
            [make_record(creation_date="2000-01-01")], as_of=date(2026, 1, 1))
        self.assertIn("not a deletion approval", report.audit_notes)

    def test_empty_batch_is_reported_cleanly(self):
        report = self.engine.assess_all([], as_of=date(2026, 1, 1))
        self.assertEqual(report.total_records, 0)
        self.assertEqual(report.overall_status, "NO_ISSUES_FOUND")

    def test_non_record_input_is_rejected(self):
        with self.assertRaises(RetentionError):
            self.engine.assess_all([{"record_id": "X"}], as_of=date(2026, 1, 1))

    def test_engine_is_stateless_across_calls(self):
        record = make_record(creation_date="2019-03-15")
        first = self.engine.assess(record, as_of=date(2024, 1, 1))
        self.engine.assess_all([make_record(record_id="OTHER")], as_of=date(2030, 1, 1))
        second = self.engine.assess(record, as_of=date(2024, 1, 1))
        self.assertEqual(first, second)


if __name__ == "__main__":
    unittest.main()
