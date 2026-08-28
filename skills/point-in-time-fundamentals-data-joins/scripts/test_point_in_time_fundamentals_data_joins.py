"""Unit tests for point-in-time-fundamentals-data-joins.

The expected values here are derived from the calendar and the SEC filing rules,
not from re-running the engine's own expressions. Several tests are explicit
regressions: they fail against the pre-2.0.0 implementation and pass against the
current one, and each says so in its docstring.
"""
import unittest

from point_in_time_fundamentals_data_joins import (
    Config,
    Engine,
    FundamentalFilingRecord,
    LeakageType,
    PITFundamentalsReport,
    PITQuery,
    PointInTimeFundamentalsDataJoinsEngine,
)


def _record(**overrides) -> FundamentalFilingRecord:
    """A valid baseline record; override only the field under test."""
    kwargs = dict(
        ticker="AAPL",
        metric_name="eps",
        value=1.50,
        period_end_date="2022-12-31",
        filing_date="2023-02-15",
    )
    kwargs.update(overrides)
    return FundamentalFilingRecord(**kwargs)


class TestLegacyEngine(unittest.TestCase):
    def test_legacy_init_and_process(self):
        engine = Engine(Config("test_engine"))
        self.assertEqual(engine.config.name, "test_engine")
        self.assertEqual(engine.process(100), 100)


class TestAvailabilityLag(unittest.TestCase):
    """EDGAR assigns filing date D to anything accepted before 5:30 p.m. ET
    (17 CFR 232.13(a)(2)), which is after the 4:00 p.m. ET close -- so a record
    filed on D is not usable at D's close."""

    def setUp(self):
        self.engine = PointInTimeFundamentalsDataJoinsEngine()

    def test_default_lag_is_one_day(self):
        self.assertEqual(self.engine.availability_lag_days, 1)
        self.assertEqual(Config().default_availability_lag_days, 1)

    def test_same_day_as_filing_is_blocked_by_default(self):
        """REGRESSION: pre-2.0.0 used `filing_date <= as_of_date` and returned
        the value on the filing date itself."""
        self.engine.insert_filings([_record()])
        report = self.engine.execute_pit_join(PITQuery("AAPL", "eps", "2023-02-15"))
        self.assertEqual(report.status, "NO_DATA_AVAILABLE_AS_OF_DATE")
        self.assertIsNone(report.matched_record_value)

    def test_day_after_filing_is_available(self):
        self.engine.insert_filings([_record()])
        report = self.engine.execute_pit_join(PITQuery("AAPL", "eps", "2023-02-16"))
        self.assertEqual(report.status, "RECORD_FOUND_PIT_VALID")
        self.assertEqual(report.matched_record_value, 1.50)
        # 2023-02-15 + 1 calendar day.
        self.assertEqual(report.matched_available_from, "2023-02-16")
        self.assertEqual(report.availability_lag_days, 1)

    def test_zero_lag_opt_out_restores_same_day_availability(self):
        engine = PointInTimeFundamentalsDataJoinsEngine(
            Config(default_availability_lag_days=0)
        )
        engine.insert_filings([_record()])
        report = engine.execute_pit_join(PITQuery("AAPL", "eps", "2023-02-15"))
        self.assertEqual(report.status, "RECORD_FOUND_PIT_VALID")
        self.assertEqual(report.matched_available_from, "2023-02-15")

    def test_larger_lag_shifts_availability(self):
        engine = PointInTimeFundamentalsDataJoinsEngine(
            Config(default_availability_lag_days=3)
        )
        engine.insert_filings([_record()])
        # 2023-02-15 + 3 days = 2023-02-18.
        self.assertEqual(
            engine.execute_pit_join(PITQuery("AAPL", "eps", "2023-02-17")).status,
            "NO_DATA_AVAILABLE_AS_OF_DATE",
        )
        self.assertEqual(
            engine.execute_pit_join(PITQuery("AAPL", "eps", "2023-02-18")).status,
            "RECORD_FOUND_PIT_VALID",
        )

    def test_negative_lag_rejected(self):
        with self.assertRaises(ValueError):
            Config(default_availability_lag_days=-1)

    def test_non_integer_lag_rejected(self):
        with self.assertRaises(ValueError):
            Config(default_availability_lag_days=1.5)
        with self.assertRaises(ValueError):
            Config(default_availability_lag_days=True)


class TestRecordSelection(unittest.TestCase):
    def setUp(self):
        self.engine = PointInTimeFundamentalsDataJoinsEngine()
        # Original FY-2022 EPS, the next quarter's original EPS, and a 10-K/A
        # amendment restating FY-2022 filed *after* the Q1-2023 10-Q.
        self.engine.insert_filings([
            _record(value=1.50, period_end_date="2022-12-31",
                    filing_date="2023-02-15", revision_number=0),
            _record(value=1.80, period_end_date="2023-03-31",
                    filing_date="2023-04-20", revision_number=0),
            _record(value=1.20, period_end_date="2022-12-31",
                    filing_date="2023-08-10", revision_number=1),
        ])

    def test_latest_fiscal_period_wins_over_later_filed_amendment(self):
        """REGRESSION: pre-2.0.0 sorted by filing_date first, so the 2023-08-10
        amendment to FY-2022 outranked the 2023-04-20 filing for Q1-2023 and
        'latest known EPS' resolved to a stale period."""
        report = self.engine.execute_pit_join(PITQuery("AAPL", "eps", "2023-09-01"))
        self.assertEqual(report.matched_period_end_date, "2023-03-31")
        self.assertEqual(report.matched_record_value, 1.80)

    def test_period_scoped_query_returns_as_restated_value(self):
        report = self.engine.execute_pit_join(
            PITQuery("AAPL", "eps", "2023-09-01", period_end_date="2022-12-31")
        )
        self.assertEqual(report.matched_record_value, 1.20)
        self.assertEqual(report.revision_number, 1)
        self.assertIs(report.leakage_type, LeakageType.NONE)

    def test_period_scoped_query_before_restatement_returns_as_reported(self):
        report = self.engine.execute_pit_join(
            PITQuery("AAPL", "eps", "2023-05-01", period_end_date="2022-12-31")
        )
        self.assertEqual(report.matched_record_value, 1.50)
        self.assertEqual(report.revision_number, 0)
        self.assertTrue(report.restatement_leakage_blocked)
        self.assertIs(report.leakage_type, LeakageType.RESTATEMENT)

    def test_period_filter_with_no_such_period_returns_no_data(self):
        report = self.engine.execute_pit_join(
            PITQuery("AAPL", "eps", "2023-09-01", period_end_date="2021-12-31")
        )
        self.assertEqual(report.status, "NO_DATA_AVAILABLE_AS_OF_DATE")
        self.assertIsNone(report.naive_join_value)
        self.assertIs(report.leakage_type, LeakageType.NONE)

    def test_future_fiscal_period_excluded_even_when_filed(self):
        """A period ending after as_of_date can never be used, whatever its
        filing date -- a filing_date-only filter would let it through."""
        report = self.engine.execute_pit_join(PITQuery("AAPL", "eps", "2023-03-30"))
        self.assertEqual(report.matched_period_end_date, "2022-12-31")

    def test_selection_is_insertion_order_independent(self):
        forward = PointInTimeFundamentalsDataJoinsEngine()
        records = [
            _record(value=2.00, period_end_date="2022-12-31",
                    filing_date="2023-02-15", revision_number=0),
            _record(value=2.10, period_end_date="2022-12-31",
                    filing_date="2023-02-15", revision_number=1),
        ]
        forward.insert_filings(list(records))
        reverse = PointInTimeFundamentalsDataJoinsEngine()
        reverse.insert_filings(list(reversed(records)))
        query = PITQuery("AAPL", "eps", "2023-06-01")
        self.assertEqual(
            forward.execute_pit_join(query).matched_record_value,
            reverse.execute_pit_join(query).matched_record_value,
        )
        self.assertEqual(forward.execute_pit_join(query).revision_number, 1)


class TestRestatementIsolation(unittest.TestCase):
    def setUp(self):
        self.engine = PointInTimeFundamentalsDataJoinsEngine()
        self.engine.insert_filings([
            _record(value=1.50, filing_date="2023-02-15", revision_number=0),
            _record(value=1.20, filing_date="2023-08-10", revision_number=1),
        ])

    def test_original_returned_between_filing_and_restatement(self):
        report = self.engine.execute_pit_join(PITQuery("AAPL", "eps", "2023-05-01"))
        self.assertEqual(report.matched_record_value, 1.50)
        self.assertEqual(report.revision_number, 0)
        self.assertTrue(report.restatement_leakage_blocked)
        self.assertFalse(report.unreleased_filing_blocked)
        self.assertIs(report.leakage_type, LeakageType.RESTATEMENT)
        # The audit must say what the naive join would have used.
        self.assertEqual(report.naive_join_value, 1.20)
        self.assertEqual(report.naive_join_filing_date, "2023-08-10")

    def test_restated_returned_after_restatement_is_public(self):
        report = self.engine.execute_pit_join(PITQuery("AAPL", "eps", "2023-09-01"))
        self.assertEqual(report.matched_record_value, 1.20)
        self.assertEqual(report.revision_number, 1)
        self.assertFalse(report.restatement_leakage_blocked)
        self.assertIs(report.leakage_type, LeakageType.NONE)

    def test_restatement_boundary_day(self):
        """Restatement filed 2023-08-10 becomes available 2023-08-11."""
        self.assertEqual(
            self.engine.execute_pit_join(
                PITQuery("AAPL", "eps", "2023-08-10")).matched_record_value,
            1.50,
        )
        self.assertEqual(
            self.engine.execute_pit_join(
                PITQuery("AAPL", "eps", "2023-08-11")).matched_record_value,
            1.20,
        )


class TestLeakageClassification(unittest.TestCase):
    def test_unreleased_filing_is_not_reported_as_a_restatement(self):
        """REGRESSION: pre-2.0.0 set restatement_leakage_blocked whenever any
        record was filtered, so a plain unreleased-earnings block was reported as
        blocked restatement leakage even with zero restatements in the data."""
        engine = PointInTimeFundamentalsDataJoinsEngine()
        engine.insert_filings([_record(value=1.50, filing_date="2023-02-15")])
        report = engine.execute_pit_join(PITQuery("AAPL", "eps", "2023-01-15"))
        self.assertEqual(report.status, "NO_DATA_AVAILABLE_AS_OF_DATE")
        self.assertFalse(report.restatement_leakage_blocked)
        self.assertTrue(report.unreleased_filing_blocked)
        self.assertIs(report.leakage_type, LeakageType.UNRELEASED_FILING)
        self.assertEqual(report.naive_join_value, 1.50)
        self.assertEqual(report.naive_join_period_end_date, "2022-12-31")

    def test_no_leakage_when_everything_is_already_public(self):
        engine = PointInTimeFundamentalsDataJoinsEngine()
        engine.insert_filings([_record(value=1.50, filing_date="2023-02-15")])
        report = engine.execute_pit_join(PITQuery("AAPL", "eps", "2023-06-01"))
        self.assertIs(report.leakage_type, LeakageType.NONE)
        self.assertFalse(report.restatement_leakage_blocked)
        self.assertFalse(report.unreleased_filing_blocked)
        self.assertEqual(report.naive_join_value, 1.50)

    def test_combined_unreleased_and_restatement(self):
        engine = PointInTimeFundamentalsDataJoinsEngine()
        engine.insert_filings([
            _record(value=1.50, period_end_date="2022-12-31",
                    filing_date="2023-02-15", revision_number=0),
            _record(value=1.20, period_end_date="2022-12-31",
                    filing_date="2023-08-10", revision_number=1),
            _record(value=1.80, period_end_date="2023-03-31",
                    filing_date="2023-04-20", revision_number=0),
        ])
        # Q1-2023 has ended but is not filed; FY-2022 is filed but later restated.
        report = engine.execute_pit_join(PITQuery("AAPL", "eps", "2023-04-01"))
        self.assertEqual(report.matched_record_value, 1.50)
        self.assertIs(report.leakage_type, LeakageType.UNRELEASED_AND_RESTATEMENT)
        self.assertTrue(report.restatement_leakage_blocked)
        self.assertTrue(report.unreleased_filing_blocked)
        self.assertEqual(report.naive_join_value, 1.80)

    def test_no_records_at_all_reports_no_leakage(self):
        engine = PointInTimeFundamentalsDataJoinsEngine()
        report = engine.execute_pit_join(PITQuery("MSFT", "eps", "2023-06-01"))
        self.assertEqual(report.status, "NO_DATA_AVAILABLE_AS_OF_DATE")
        self.assertIs(report.leakage_type, LeakageType.NONE)
        self.assertIsNone(report.naive_join_value)
        self.assertFalse(report.restatement_leakage_blocked)
        self.assertFalse(report.unreleased_filing_blocked)


class TestNonReliance(unittest.TestCase):
    """Item 4.02 Form 8-K disclosures precede the corrected filing; the engine
    keeps returning the as-reported value but flags it."""

    def setUp(self):
        self.engine = PointInTimeFundamentalsDataJoinsEngine()
        self.engine.insert_filings([
            _record(value=1.50, filing_date="2023-02-15",
                    non_reliance_date="2023-06-05"),
        ])

    def test_not_flagged_before_disclosure(self):
        report = self.engine.execute_pit_join(PITQuery("AAPL", "eps", "2023-06-04"))
        self.assertFalse(report.is_non_reliance_flagged)
        self.assertNotIn("NON-RELIANCE", report.audit_notes)

    def test_flagged_on_and_after_disclosure_but_value_still_returned(self):
        report = self.engine.execute_pit_join(PITQuery("AAPL", "eps", "2023-06-05"))
        self.assertTrue(report.is_non_reliance_flagged)
        self.assertEqual(report.matched_record_value, 1.50)
        self.assertIn("NON-RELIANCE", report.audit_notes)

    def test_non_reliance_before_filing_is_rejected(self):
        with self.assertRaises(ValueError):
            PointInTimeFundamentalsDataJoinsEngine().insert_filings([
                _record(filing_date="2023-02-15", non_reliance_date="2023-01-01")
            ])


class TestRecordValidation(unittest.TestCase):
    def setUp(self):
        self.engine = PointInTimeFundamentalsDataJoinsEngine()

    def _assert_rejected(self, **overrides):
        with self.assertRaises(ValueError):
            self.engine.insert_filings([_record(**overrides)])

    def test_non_iso_dates_rejected(self):
        # Lexicographic string comparison silently mis-orders every one of these.
        for bad in ("2023-2-15", "02/15/2023", "20230215", "15-02-2023", "2023-02"):
            with self.subTest(bad=bad):
                self._assert_rejected(filing_date=bad)

    def test_impossible_calendar_date_rejected(self):
        self._assert_rejected(filing_date="2023-02-30")
        self._assert_rejected(filing_date="2023-13-01")

    def test_filing_before_period_end_rejected(self):
        """A filing cannot report a period that has not ended."""
        self._assert_rejected(period_end_date="2022-12-31", filing_date="2022-12-30")

    def test_filing_on_period_end_allowed(self):
        self.engine.insert_filings([
            _record(period_end_date="2022-12-31", filing_date="2022-12-31")
        ])
        self.assertEqual(len(self.engine.filing_database), 1)

    def test_non_finite_value_rejected(self):
        self._assert_rejected(value=float("nan"))
        self._assert_rejected(value=float("inf"))
        self._assert_rejected(value=float("-inf"))

    def test_non_numeric_value_rejected(self):
        self._assert_rejected(value="1.50")
        self._assert_rejected(value=None)
        self._assert_rejected(value=True)

    def test_blank_identifiers_rejected(self):
        self._assert_rejected(ticker="")
        self._assert_rejected(ticker="   ")
        self._assert_rejected(metric_name="")

    def test_negative_or_non_integer_revision_rejected(self):
        self._assert_rejected(revision_number=-1)
        self._assert_rejected(revision_number=1.5)

    def test_wrong_type_rejected(self):
        with self.assertRaises(ValueError):
            self.engine.insert_filings([{"ticker": "AAPL"}])

    def test_batch_insert_is_atomic(self):
        """A rejected record must not leave earlier records in the same batch
        stored -- a partially ingested batch queries as if it were complete."""
        with self.assertRaises(ValueError):
            self.engine.insert_filings([_record(), _record(value=float("nan"))])
        self.assertEqual(self.engine.filing_database, [])


class TestQueryValidation(unittest.TestCase):
    def setUp(self):
        self.engine = PointInTimeFundamentalsDataJoinsEngine()
        self.engine.insert_filings([_record()])

    def test_bad_as_of_date_rejected(self):
        with self.assertRaises(ValueError):
            self.engine.execute_pit_join(PITQuery("AAPL", "eps", "2023-3-1"))

    def test_bad_period_filter_rejected(self):
        with self.assertRaises(ValueError):
            self.engine.execute_pit_join(
                PITQuery("AAPL", "eps", "2023-03-01", period_end_date="Dec 2022")
            )

    def test_blank_query_identifiers_rejected(self):
        with self.assertRaises(ValueError):
            self.engine.execute_pit_join(PITQuery("", "eps", "2023-03-01"))
        with self.assertRaises(ValueError):
            self.engine.execute_pit_join(PITQuery("AAPL", " ", "2023-03-01"))

    def test_wrong_query_type_rejected(self):
        with self.assertRaises(ValueError):
            self.engine.execute_pit_join(("AAPL", "eps", "2023-03-01"))


class TestMatchingNormalisation(unittest.TestCase):
    def setUp(self):
        self.engine = PointInTimeFundamentalsDataJoinsEngine()
        self.engine.insert_filings([_record(ticker=" aapl ", metric_name=" EPS ")])

    def test_case_and_whitespace_insensitive_match(self):
        report = self.engine.execute_pit_join(PITQuery("AaPl", "ePs", "2023-03-01"))
        self.assertEqual(report.status, "RECORD_FOUND_PIT_VALID")
        self.assertEqual(report.ticker, "AAPL")

    def test_other_ticker_does_not_match(self):
        report = self.engine.execute_pit_join(PITQuery("AAPLX", "eps", "2023-03-01"))
        self.assertEqual(report.status, "NO_DATA_AVAILABLE_AS_OF_DATE")

    def test_other_metric_does_not_match(self):
        report = self.engine.execute_pit_join(PITQuery("AAPL", "revenue", "2023-03-01"))
        self.assertEqual(report.status, "NO_DATA_AVAILABLE_AS_OF_DATE")


class TestAmbiguousDuplicates(unittest.TestCase):
    def test_conflicting_duplicates_are_flagged_not_silently_resolved(self):
        engine = PointInTimeFundamentalsDataJoinsEngine()
        engine.insert_filings([
            _record(value=1.50, filing_date="2023-02-15", revision_number=0),
            _record(value=9.99, filing_date="2023-02-15", revision_number=0),
        ])
        report = engine.execute_pit_join(PITQuery("AAPL", "eps", "2023-03-01"))
        self.assertEqual(report.ambiguous_candidate_count, 2)
        self.assertIn("WARNING", report.audit_notes)

    def test_unique_match_is_not_flagged(self):
        engine = PointInTimeFundamentalsDataJoinsEngine()
        engine.insert_filings([_record()])
        report = engine.execute_pit_join(PITQuery("AAPL", "eps", "2023-03-01"))
        self.assertEqual(report.ambiguous_candidate_count, 1)
        self.assertNotIn("WARNING", report.audit_notes)

    def test_identical_duplicate_rows_are_not_flagged(self):
        """Byte-identical duplicates are common and benign. Warning on them
        trains the reader to ignore the warning that matters."""
        engine = PointInTimeFundamentalsDataJoinsEngine()
        engine.insert_filings([_record(value=1.50), _record(value=1.50)])
        report = engine.execute_pit_join(PITQuery("AAPL", "eps", "2023-03-01"))
        self.assertEqual(report.ambiguous_candidate_count, 1)
        self.assertNotIn("WARNING", report.audit_notes)
        self.assertEqual(report.matched_record_value, 1.50)


class TestDateRangeEdges(unittest.TestCase):
    def test_leap_day_accepted(self):
        engine = PointInTimeFundamentalsDataJoinsEngine()
        engine.insert_filings([
            _record(period_end_date="2024-02-29", filing_date="2024-02-29")
        ])
        report = engine.execute_pit_join(PITQuery("AAPL", "eps", "2024-03-01"))
        self.assertEqual(report.matched_available_from, "2024-03-01")

    def test_non_leap_day_rejected(self):
        with self.assertRaises(ValueError):
            PointInTimeFundamentalsDataJoinsEngine().insert_filings([
                _record(period_end_date="2023-02-28", filing_date="2023-02-29")
            ])

    def test_lag_past_max_date_raises_value_error_not_overflow_error(self):
        """Callers of this module should only ever have to catch ValueError."""
        engine = PointInTimeFundamentalsDataJoinsEngine()
        engine.insert_filings([
            _record(period_end_date="9999-12-31", filing_date="9999-12-31")
        ])
        with self.assertRaises(ValueError):
            engine.execute_pit_join(PITQuery("AAPL", "eps", "9999-12-31"))


class TestReportContract(unittest.TestCase):
    def test_report_shape(self):
        engine = PointInTimeFundamentalsDataJoinsEngine()
        engine.insert_filings([_record()])
        report = engine.execute_pit_join(PITQuery("AAPL", "eps", "2023-03-01"))
        self.assertIsInstance(report, PITFundamentalsReport)
        self.assertEqual(report.as_of_date, "2023-03-01")
        self.assertEqual(report.matched_filing_date, "2023-02-15")
        self.assertEqual(report.matched_period_end_date, "2022-12-31")
        self.assertEqual(report.status, "RECORD_FOUND_PIT_VALID")


if __name__ == "__main__":
    unittest.main()
