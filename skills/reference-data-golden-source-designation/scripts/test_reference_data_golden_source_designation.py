import unittest
from datetime import datetime, timedelta, timezone

from reference_data_golden_source_designation import (
    COMPARE_CASEFOLD_STRIP,
    COMPARE_EXACT,
    FINDING_FIELD_HAS_NO_USABLE_VALUE,
    FINDING_NO_PRIORITY_RULE,
    FINDING_NO_RULED_VENDOR_SUPPLIED_VALUE,
    FINDING_UNGOVERNED_FALLBACK,
    FINDING_UNKNOWN_VENDOR_IN_RULE,
    FINDING_VENDOR_AS_OF_IN_FUTURE,
    FINDING_VENDOR_AS_OF_MISSING,
    FINDING_VENDOR_DISAGREEMENT,
    FINDING_VENDOR_RECORD_STALE,
    RULE_NO_VALUE,
    RULE_PRIORITY,
    RULE_UNGOVERNED_FALLBACK,
    SKIP_BLANK,
    SKIP_NULL,
    SKIP_SENTINEL,
    SKIP_STALE,
    STATUS_CONFLICTS_FOUND,
    STATUS_MISSING_DATA,
    STATUS_RESOLVED,
    STATUS_UNGOVERNED_FIELDS,
    GoldenSourceConfig,
    GoldenSourceConfigError,
    GoldenSourceDesignationEngine,
    GoldenSourceInputError,
    VendorFieldData,
)

UTC = timezone.utc


def codes(report):
    """Finding codes present on a report."""
    return {f.code for f in report.findings}


def resolution(report, field_name):
    return next(r for r in report.resolutions if r.field_name == field_name)


class TestPriorityResolution(unittest.TestCase):
    """The designated-source path: the behaviour the skill exists to provide."""

    def setUp(self):
        self.config = GoldenSourceConfig(priority_rules={
            "isin": ["Bloomberg", "Refinitiv"],
            "tick_size": ["Exchange", "Bloomberg", "Refinitiv"],
            "currency": ["Bloomberg", "Refinitiv"],
        })
        self.engine = GoldenSourceDesignationEngine(self.config)

    def test_bloomberg_golden_for_isin(self):
        report = self.engine.resolve_golden_record("AAPL", [
            VendorFieldData("Bloomberg", {"isin": "US0378331005", "currency": "USD"}),
            VendorFieldData("Refinitiv", {"isin": "US0378331005_OLD", "currency": "USD"}),
        ])
        self.assertEqual(report.golden_record["isin"], "US0378331005")
        isin = resolution(report, "isin")
        self.assertEqual(isin.golden_vendor, "Bloomberg")
        self.assertTrue(isin.has_conflict)
        self.assertEqual(isin.resolution_rule, RULE_PRIORITY)
        self.assertTrue(isin.is_governed)
        self.assertEqual(isin.overridden_vendors, ["Refinitiv"])
        self.assertTrue(report.is_fully_governed)

    def test_fallback_to_secondary_when_primary_null(self):
        report = self.engine.resolve_golden_record("AAPL", [
            VendorFieldData("Bloomberg", {"isin": None, "currency": "USD"}),
            VendorFieldData("Refinitiv", {"isin": "US0378331005", "currency": "USD"}),
        ])
        self.assertEqual(report.golden_record["isin"], "US0378331005")
        isin = resolution(report, "isin")
        self.assertEqual(isin.golden_vendor, "Refinitiv")
        # Falling through a ranked vendor's NULL is still governance, not a guess.
        self.assertEqual(isin.resolution_rule, RULE_PRIORITY)
        self.assertEqual(isin.skipped_vendors, {"Bloomberg": SKIP_NULL})
        self.assertTrue(report.is_fully_governed)

    def test_priority_is_rank_not_list_order(self):
        """Reversing the argument order must not change the winner."""
        forward = [
            VendorFieldData("Bloomberg", {"isin": "BBG_VALUE"}),
            VendorFieldData("Refinitiv", {"isin": "RTR_VALUE"}),
        ]
        a = self.engine.resolve_golden_record("AAPL", forward)
        b = self.engine.resolve_golden_record("AAPL", list(reversed(forward)))
        self.assertEqual(a.golden_record, b.golden_record)
        self.assertEqual(a.golden_record["isin"], "BBG_VALUE")

    def test_third_rank_wins_when_first_two_absent(self):
        report = self.engine.resolve_golden_record("AAPL", [
            VendorFieldData("Refinitiv", {"tick_size": "0.01"}),
        ])
        self.assertEqual(report.golden_record["tick_size"], "0.01")
        self.assertEqual(resolution(report, "tick_size").golden_vendor, "Refinitiv")

    def test_no_conflicts_resolved_status(self):
        report = self.engine.resolve_golden_record("AAPL", [
            VendorFieldData("Bloomberg", {"isin": "US0378331005", "currency": "USD"}),
            VendorFieldData("Refinitiv", {"isin": "US0378331005", "currency": "USD"}),
        ])
        self.assertEqual(report.status, STATUS_RESOLVED)
        self.assertEqual(report.conflicts_detected, 0)
        self.assertTrue(report.is_fully_governed)

    def test_conflicts_found_status(self):
        report = self.engine.resolve_golden_record("AAPL", [
            VendorFieldData("Bloomberg", {"isin": "US0378331005"}),
            VendorFieldData("Refinitiv", {"isin": "DIFFERENT_ISIN"}),
        ])
        self.assertEqual(report.status, STATUS_CONFLICTS_FOUND)
        self.assertEqual(report.conflicts_detected, 1)
        self.assertIn(FINDING_VENDOR_DISAGREEMENT, codes(report))
        # A conflict a designated source resolved is still a fully governed record.
        self.assertTrue(report.is_fully_governed)

    def test_agreement_between_two_of_three_is_not_a_conflict(self):
        report = self.engine.resolve_golden_record("AAPL", [
            VendorFieldData("Bloomberg", {"currency": "USD"}),
            VendorFieldData("Refinitiv", {"currency": "USD"}),
        ])
        self.assertFalse(resolution(report, "currency").has_conflict)


class TestUngovernedFieldsAreNotGuessed(unittest.TestCase):
    """Regression tests against v1.0.0's silent arbitrary-vendor fallback."""

    def setUp(self):
        self.config = GoldenSourceConfig(priority_rules={"isin": ["Bloomberg"]})
        self.engine = GoldenSourceDesignationEngine(self.config)

    def test_field_without_rule_is_left_empty_by_default(self):
        # v1.0.0 wrote Refinitiv's lot_size here and reported RESOLVED.
        report = self.engine.resolve_golden_record("AAPL", [
            VendorFieldData("Refinitiv", {"isin": "X", "lot_size": "100"}),
            VendorFieldData("Bloomberg", {"isin": "US0378331005"}),
        ])
        self.assertIsNone(report.golden_record["lot_size"])
        lot = resolution(report, "lot_size")
        self.assertEqual(lot.resolution_rule, RULE_NO_VALUE)
        self.assertFalse(lot.is_governed)
        self.assertIn(FINDING_NO_PRIORITY_RULE, codes(report))
        self.assertEqual(report.status, STATUS_MISSING_DATA)
        self.assertFalse(report.is_fully_governed)

    def test_unruled_field_does_not_depend_on_argument_order(self):
        """v1.0.0 returned a different golden record for the same data reordered."""
        engine = GoldenSourceDesignationEngine(
            GoldenSourceConfig(priority_rules={}, allow_undesignated_fallback=True))
        data = [
            VendorFieldData("Zeta", {"lot_size": "1"}),
            VendorFieldData("Alpha", {"lot_size": "100"}),
        ]
        a = engine.resolve_golden_record("AAPL", data)
        b = engine.resolve_golden_record("AAPL", list(reversed(data)))
        self.assertEqual(a.golden_record, b.golden_record)
        self.assertEqual(a.golden_record["lot_size"], "100")  # 'Alpha' sorts first

    def test_opt_in_fallback_is_labelled_ungoverned(self):
        engine = GoldenSourceDesignationEngine(
            GoldenSourceConfig(priority_rules={"isin": ["Bloomberg"]},
                               allow_undesignated_fallback=True))
        report = engine.resolve_golden_record("AAPL", [
            VendorFieldData("Bloomberg", {"isin": "US0378331005"}),
            VendorFieldData("Refinitiv", {"lot_size": "100"}),
        ])
        self.assertEqual(report.golden_record["lot_size"], "100")
        lot = resolution(report, "lot_size")
        self.assertEqual(lot.resolution_rule, RULE_UNGOVERNED_FALLBACK)
        self.assertFalse(lot.is_governed)
        self.assertEqual(report.ungoverned_field_count, 1)
        self.assertEqual(report.status, STATUS_UNGOVERNED_FIELDS)
        self.assertFalse(report.is_fully_governed)
        self.assertIn(FINDING_UNGOVERNED_FALLBACK, codes(report))

    def test_undesignated_vendor_does_not_fill_a_ruled_field(self):
        # The rule names Bloomberg; only Refinitiv supplied the field. v1.0.0 took
        # Refinitiv's value and attributed it as golden.
        report = self.engine.resolve_golden_record("AAPL", [
            VendorFieldData("Refinitiv", {"isin": "RTR_ISIN"}),
        ])
        self.assertIsNone(report.golden_record["isin"])
        self.assertIn(FINDING_NO_RULED_VENDOR_SUPPLIED_VALUE, codes(report))

    def test_rule_naming_only_absent_vendors_is_flagged(self):
        report = self.engine.resolve_golden_record("AAPL", [
            VendorFieldData("Refinitiv", {"isin": "RTR_ISIN"}),
        ])
        self.assertIn(FINDING_UNKNOWN_VENDOR_IN_RULE, codes(report))

    def test_rules_for_fields_this_instrument_lacks_are_not_flagged(self):
        """A rule set wider than the instrument must not bury real findings in noise."""
        engine = GoldenSourceDesignationEngine(GoldenSourceConfig(priority_rules={
            "isin": ["Bloomberg"],
            "lot_size": ["Exchange"],
            "cfi": ["Exchange"],
            "mic": ["Exchange"],
        }))
        report = engine.resolve_golden_record("AAPL", [
            VendorFieldData("Bloomberg", {"isin": "US0378331005"}),
        ])
        # Only `isin` was reported, and Bloomberg governs it, so nothing is unknown.
        self.assertNotIn(FINDING_UNKNOWN_VENDOR_IN_RULE, codes(report))
        self.assertEqual(report.findings, [])
        self.assertTrue(report.is_fully_governed)

    def test_no_usable_value_anywhere(self):
        report = self.engine.resolve_golden_record("AAPL", [
            VendorFieldData("Bloomberg", {"isin": None}),
        ])
        self.assertIsNone(report.golden_record["isin"])
        self.assertEqual(report.fields_without_data, 1)
        self.assertIn(FINDING_FIELD_HAS_NO_USABLE_VALUE, codes(report))


class TestBlankAndSentinelValues(unittest.TestCase):
    """A populated-looking placeholder must not outrank a real value."""

    def test_blank_string_does_not_win_over_real_value(self):
        engine = GoldenSourceDesignationEngine(GoldenSourceConfig(
            priority_rules={"isin": ["Bloomberg", "Refinitiv"]}))
        report = engine.resolve_golden_record("AAPL", [
            VendorFieldData("Bloomberg", {"isin": "   "}),
            VendorFieldData("Refinitiv", {"isin": "US0378331005"}),
        ])
        self.assertEqual(report.golden_record["isin"], "US0378331005")
        self.assertEqual(resolution(report, "isin").skipped_vendors["Bloomberg"], SKIP_BLANK)

    def test_blank_is_kept_when_gating_disabled(self):
        engine = GoldenSourceDesignationEngine(GoldenSourceConfig(
            priority_rules={"isin": ["Bloomberg", "Refinitiv"]},
            treat_blank_as_missing=False))
        report = engine.resolve_golden_record("AAPL", [
            VendorFieldData("Bloomberg", {"isin": "   "}),
            VendorFieldData("Refinitiv", {"isin": "US0378331005"}),
        ])
        self.assertEqual(report.golden_record["isin"], "   ")

    def test_sentinel_is_treated_as_absent_case_insensitively(self):
        engine = GoldenSourceDesignationEngine(GoldenSourceConfig(
            priority_rules={"isin": ["Bloomberg", "Refinitiv"]},
            missing_sentinels=frozenset({"N/A"})))
        report = engine.resolve_golden_record("AAPL", [
            VendorFieldData("Bloomberg", {"isin": " n/a "}),
            VendorFieldData("Refinitiv", {"isin": "US0378331005"}),
        ])
        self.assertEqual(report.golden_record["isin"], "US0378331005")
        self.assertEqual(resolution(report, "isin").skipped_vendors["Bloomberg"], SKIP_SENTINEL)

    def test_sentinel_not_declared_is_a_real_value(self):
        engine = GoldenSourceDesignationEngine(GoldenSourceConfig(
            priority_rules={"isin": ["Bloomberg", "Refinitiv"]}))
        report = engine.resolve_golden_record("AAPL", [
            VendorFieldData("Bloomberg", {"isin": "N/A"}),
            VendorFieldData("Refinitiv", {"isin": "US0378331005"}),
        ])
        self.assertEqual(report.golden_record["isin"], "N/A")

    def test_blank_values_do_not_count_as_a_conflict(self):
        engine = GoldenSourceDesignationEngine(GoldenSourceConfig(
            priority_rules={"currency": ["Bloomberg", "Refinitiv"]}))
        report = engine.resolve_golden_record("AAPL", [
            VendorFieldData("Bloomberg", {"currency": "USD"}),
            VendorFieldData("Refinitiv", {"currency": ""}),
        ])
        self.assertFalse(resolution(report, "currency").has_conflict)
        self.assertEqual(report.conflicts_detected, 0)


class TestConflictComparison(unittest.TestCase):

    def _report(self, mode):
        engine = GoldenSourceDesignationEngine(GoldenSourceConfig(
            priority_rules={"currency": ["Bloomberg", "Refinitiv"]},
            conflict_comparison=mode))
        return engine.resolve_golden_record("AAPL", [
            VendorFieldData("Bloomberg", {"currency": "USD"}),
            VendorFieldData("Refinitiv", {"currency": " usd "}),
        ])

    def test_exact_mode_reports_a_casing_difference_as_a_conflict(self):
        self.assertTrue(resolution(self._report(COMPARE_EXACT), "currency").has_conflict)

    def test_casefold_strip_mode_suppresses_it(self):
        self.assertFalse(
            resolution(self._report(COMPARE_CASEFOLD_STRIP), "currency").has_conflict)

    def test_normalisation_never_alters_the_stored_value(self):
        report = self._report(COMPARE_CASEFOLD_STRIP)
        self.assertEqual(report.golden_record["currency"], "USD")

    def test_casefold_does_not_reconcile_numeric_formatting(self):
        engine = GoldenSourceDesignationEngine(GoldenSourceConfig(
            priority_rules={"tick_size": ["Bloomberg", "Refinitiv"]},
            conflict_comparison=COMPARE_CASEFOLD_STRIP))
        report = engine.resolve_golden_record("AAPL", [
            VendorFieldData("Bloomberg", {"tick_size": "0.01"}),
            VendorFieldData("Refinitiv", {"tick_size": "0.0100"}),
        ])
        self.assertTrue(resolution(report, "tick_size").has_conflict)


class TestStaleness(unittest.TestCase):

    def setUp(self):
        self.now = datetime(2026, 8, 27, 12, 0, tzinfo=UTC)
        self.config = GoldenSourceConfig(
            priority_rules={"isin": ["Bloomberg", "Exchange"]},
            max_staleness=timedelta(days=1))
        self.engine = GoldenSourceDesignationEngine(self.config)

    def test_stale_top_priority_record_loses_to_fresh_secondary(self):
        report = self.engine.resolve_golden_record("AAPL", [
            VendorFieldData("Bloomberg", {"isin": "OLD"}, as_of=self.now - timedelta(days=30)),
            VendorFieldData("Exchange", {"isin": "NEW"}, as_of=self.now - timedelta(minutes=5)),
        ], evaluation_time=self.now)
        self.assertEqual(report.golden_record["isin"], "NEW")
        self.assertEqual(resolution(report, "isin").skipped_vendors["Bloomberg"], SKIP_STALE)
        self.assertIn(FINDING_VENDOR_RECORD_STALE, codes(report))

    def test_record_exactly_at_the_limit_is_still_usable(self):
        report = self.engine.resolve_golden_record("AAPL", [
            VendorFieldData("Bloomberg", {"isin": "EDGE"}, as_of=self.now - timedelta(days=1)),
        ], evaluation_time=self.now)
        self.assertEqual(report.golden_record["isin"], "EDGE")

    def test_one_second_past_the_limit_is_stale(self):
        report = self.engine.resolve_golden_record("AAPL", [
            VendorFieldData("Bloomberg", {"isin": "EDGE"},
                            as_of=self.now - timedelta(days=1, seconds=1)),
        ], evaluation_time=self.now)
        self.assertIsNone(report.golden_record["isin"])

    def test_missing_as_of_excludes_the_snapshot(self):
        report = self.engine.resolve_golden_record("AAPL", [
            VendorFieldData("Bloomberg", {"isin": "UNDATED"}),
            VendorFieldData("Exchange", {"isin": "DATED"}, as_of=self.now),
        ], evaluation_time=self.now)
        self.assertEqual(report.golden_record["isin"], "DATED")
        self.assertIn(FINDING_VENDOR_AS_OF_MISSING, codes(report))

    def test_as_of_after_evaluation_time_excludes_the_snapshot(self):
        report = self.engine.resolve_golden_record("AAPL", [
            VendorFieldData("Bloomberg", {"isin": "FUTURE"},
                            as_of=self.now + timedelta(hours=1)),
            VendorFieldData("Exchange", {"isin": "SANE"}, as_of=self.now),
        ], evaluation_time=self.now)
        self.assertEqual(report.golden_record["isin"], "SANE")
        self.assertIn(FINDING_VENDOR_AS_OF_IN_FUTURE, codes(report))

    def test_staleness_is_measured_across_timezones(self):
        """A tz-aware as_of in another zone is compared on the absolute instant."""
        ist = timezone(timedelta(hours=5, minutes=30))
        as_of = self.now.astimezone(ist) - timedelta(hours=2)
        report = self.engine.resolve_golden_record("AAPL", [
            VendorFieldData("Bloomberg", {"isin": "OK"}, as_of=as_of),
        ], evaluation_time=self.now)
        self.assertEqual(report.golden_record["isin"], "OK")

    def test_evaluation_time_required_when_staleness_configured(self):
        with self.assertRaises(GoldenSourceInputError):
            self.engine.resolve_golden_record("AAPL", [
                VendorFieldData("Bloomberg", {"isin": "X"}, as_of=self.now)])

    def test_ungated_report_says_so_and_keeps_an_ancient_record(self):
        """Without a gate, age is not consulted -- and the report admits it."""
        engine = GoldenSourceDesignationEngine(GoldenSourceConfig(
            priority_rules={"isin": ["Bloomberg", "Exchange"]}))
        report = engine.resolve_golden_record("AAPL", [
            VendorFieldData("Bloomberg", {"isin": "ANCIENT"},
                            as_of=self.now - timedelta(days=3650)),
            VendorFieldData("Exchange", {"isin": "FRESH"}, as_of=self.now),
        ], evaluation_time=self.now)
        self.assertFalse(report.staleness_gated)
        self.assertEqual(report.golden_record["isin"], "ANCIENT")
        self.assertEqual(report.evaluation_time, self.now)

    def test_gated_report_flags_itself_as_gated(self):
        report = self.engine.resolve_golden_record("AAPL", [
            VendorFieldData("Bloomberg", {"isin": "X"}, as_of=self.now),
        ], evaluation_time=self.now)
        self.assertTrue(report.staleness_gated)


class TestInputValidation(unittest.TestCase):

    def setUp(self):
        self.engine = GoldenSourceDesignationEngine(
            GoldenSourceConfig(priority_rules={"isin": ["Bloomberg"]}))

    def test_empty_vendor_data_is_not_a_resolved_record(self):
        with self.assertRaises(GoldenSourceInputError):
            self.engine.resolve_golden_record("AAPL", [])

    def test_vendors_supplying_no_fields_is_not_a_resolved_record(self):
        # The empty-record bug wearing a different shape: vendors are present but every
        # snapshot is empty, so there is still nothing to reconcile.
        with self.assertRaises(GoldenSourceInputError):
            self.engine.resolve_golden_record("AAPL", [
                VendorFieldData("Bloomberg", {}),
                VendorFieldData("Refinitiv", {}),
            ])

    def test_blank_instrument_id_rejected(self):
        with self.assertRaises(GoldenSourceInputError):
            self.engine.resolve_golden_record("  ", [
                VendorFieldData("Bloomberg", {"isin": "X"})])

    def test_duplicate_vendor_rejected(self):
        with self.assertRaises(GoldenSourceInputError):
            self.engine.resolve_golden_record("AAPL", [
                VendorFieldData("Bloomberg", {"isin": "FIRST"}),
                VendorFieldData("Bloomberg", {"isin": "SECOND"}),
            ])

    def test_non_string_value_rejected(self):
        with self.assertRaises(GoldenSourceInputError):
            self.engine.resolve_golden_record("AAPL", [
                VendorFieldData("Bloomberg", {"tick_size": 0.01})])

    def test_blank_vendor_name_rejected(self):
        with self.assertRaises(GoldenSourceInputError):
            self.engine.resolve_golden_record("AAPL", [
                VendorFieldData("  ", {"isin": "X"})])

    def test_blank_field_name_rejected(self):
        with self.assertRaises(GoldenSourceInputError):
            self.engine.resolve_golden_record("AAPL", [
                VendorFieldData("Bloomberg", {"": "X"})])

    def test_naive_as_of_rejected(self):
        with self.assertRaises(GoldenSourceInputError):
            self.engine.resolve_golden_record("AAPL", [
                VendorFieldData("Bloomberg", {"isin": "X"},
                                as_of=datetime(2026, 8, 27, 12, 0))])

    def test_naive_evaluation_time_rejected(self):
        with self.assertRaises(GoldenSourceInputError):
            self.engine.resolve_golden_record(
                "AAPL", [VendorFieldData("Bloomberg", {"isin": "X"})],
                evaluation_time=datetime(2026, 8, 27, 12, 0))

    def test_errors_are_value_errors(self):
        with self.assertRaises(ValueError):
            self.engine.resolve_golden_record("AAPL", [])


class TestConfigValidation(unittest.TestCase):

    def test_duplicate_vendor_in_rule_rejected(self):
        with self.assertRaises(GoldenSourceConfigError):
            GoldenSourceConfig(priority_rules={"isin": ["Bloomberg", "Bloomberg"]})

    def test_string_instead_of_vendor_list_rejected(self):
        with self.assertRaises(GoldenSourceConfigError):
            GoldenSourceConfig(priority_rules={"isin": "Bloomberg"})

    def test_blank_vendor_in_rule_rejected(self):
        with self.assertRaises(GoldenSourceConfigError):
            GoldenSourceConfig(priority_rules={"isin": ["Bloomberg", ""]})

    def test_unknown_comparison_mode_rejected(self):
        with self.assertRaises(GoldenSourceConfigError):
            GoldenSourceConfig(conflict_comparison="FUZZY")

    def test_non_positive_max_staleness_rejected(self):
        with self.assertRaises(GoldenSourceConfigError):
            GoldenSourceConfig(max_staleness=timedelta(0))

    def test_engine_rejects_wrong_config_type(self):
        with self.assertRaises(GoldenSourceConfigError):
            GoldenSourceDesignationEngine(config={"isin": ["Bloomberg"]})

    def test_default_config_governs_nothing(self):
        """A rule-less engine must not silently become a first-vendor-wins engine."""
        engine = GoldenSourceDesignationEngine()
        report = engine.resolve_golden_record("AAPL", [
            VendorFieldData("Bloomberg", {"isin": "X"})])
        self.assertIsNone(report.golden_record["isin"])
        self.assertFalse(report.is_fully_governed)


class TestReportIntegrity(unittest.TestCase):

    def test_all_vendor_values_preserves_raw_input(self):
        engine = GoldenSourceDesignationEngine(GoldenSourceConfig(
            priority_rules={"isin": ["Bloomberg", "Refinitiv"]}))
        report = engine.resolve_golden_record("AAPL", [
            VendorFieldData("Bloomberg", {"isin": "  "}),
            VendorFieldData("Refinitiv", {"isin": "US0378331005"}),
        ])
        # The audit record shows what each vendor actually sent, blanks included.
        self.assertEqual(resolution(report, "isin").all_vendor_values,
                         {"Bloomberg": "  ", "Refinitiv": "US0378331005"})

    def test_absent_vendor_is_distinguishable_from_explicit_null(self):
        engine = GoldenSourceDesignationEngine(GoldenSourceConfig(
            priority_rules={"isin": ["Bloomberg"], "cusip": ["Bloomberg"]}))
        report = engine.resolve_golden_record("AAPL", [
            VendorFieldData("Bloomberg", {"isin": None}),
            VendorFieldData("Refinitiv", {"cusip": "037833100"}),
        ])
        self.assertEqual(resolution(report, "isin").all_vendor_values, {"Bloomberg": None})
        self.assertNotIn("Refinitiv", resolution(report, "isin").all_vendor_values)

    def test_fields_are_reported_in_sorted_order(self):
        engine = GoldenSourceDesignationEngine()
        report = engine.resolve_golden_record("AAPL", [
            VendorFieldData("Bloomberg", {"zeta": "1", "alpha": "2", "mid": "3"})])
        self.assertEqual([r.field_name for r in report.resolutions],
                         ["alpha", "mid", "zeta"])

    def test_counts_agree_with_resolutions(self):
        engine = GoldenSourceDesignationEngine(GoldenSourceConfig(
            priority_rules={"isin": ["Bloomberg", "Refinitiv"]},
            allow_undesignated_fallback=True))
        report = engine.resolve_golden_record("AAPL", [
            VendorFieldData("Bloomberg", {"isin": "A", "lot_size": "100", "empty": None}),
            VendorFieldData("Refinitiv", {"isin": "B"}),
        ])
        self.assertEqual(report.total_fields, 3)
        self.assertEqual(report.conflicts_detected,
                         sum(1 for r in report.resolutions if r.has_conflict))
        self.assertEqual(report.fields_without_data,
                         sum(1 for r in report.resolutions if r.golden_value is None))
        self.assertEqual(report.ungoverned_field_count,
                         sum(1 for r in report.resolutions
                             if r.resolution_rule == RULE_UNGOVERNED_FALLBACK))

    def test_engine_holds_no_state_between_calls(self):
        engine = GoldenSourceDesignationEngine(GoldenSourceConfig(
            priority_rules={"isin": ["Bloomberg"]}))
        first = engine.resolve_golden_record("AAPL", [
            VendorFieldData("Bloomberg", {"isin": "A"})])
        second = engine.resolve_golden_record("MSFT", [
            VendorFieldData("Bloomberg", {"isin": "B"})])
        self.assertEqual(first.golden_record, {"isin": "A"})
        self.assertEqual(second.golden_record, {"isin": "B"})
        self.assertEqual(first.instrument_id, "AAPL")

    def test_status_precedence_ungoverned_outranks_missing_and_conflict(self):
        engine = GoldenSourceDesignationEngine(GoldenSourceConfig(
            priority_rules={"isin": ["Bloomberg", "Refinitiv"]},
            allow_undesignated_fallback=True))
        report = engine.resolve_golden_record("AAPL", [
            VendorFieldData("Bloomberg", {"isin": "A", "lot_size": "100"}),
            VendorFieldData("Refinitiv", {"isin": "B"}),
        ])
        self.assertEqual(report.conflicts_detected, 1)
        self.assertEqual(report.ungoverned_field_count, 1)
        self.assertEqual(report.status, STATUS_UNGOVERNED_FIELDS)

    def test_audit_notes_carry_every_count(self):
        engine = GoldenSourceDesignationEngine(GoldenSourceConfig(
            priority_rules={"isin": ["Bloomberg"]}))
        report = engine.resolve_golden_record("AAPL", [
            VendorFieldData("Bloomberg", {"isin": "A"})])
        for token in ("Fields =", "Conflicts =", "Missing =", "Ungoverned ="):
            self.assertIn(token, report.audit_notes)


if __name__ == '__main__':
    unittest.main()
