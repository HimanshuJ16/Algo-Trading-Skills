"""Unit tests for backtest-database-schema-for-point-in-time-queries."""
import unittest

from pit_schema import PITRecord, PointInTimeStore, TemporalFormatError


class TestPointInTimeStore(unittest.TestCase):
    def setUp(self):
        self.store = PointInTimeStore()
        # Initial earnings report known on Jan 15
        self.store.insert(PITRecord("AAPL", "pe_ratio", 25.0, known_at="2023-01-15", valid_from="2022-12-31"))
        # Restated earnings report known on March 01
        self.store.insert(PITRecord("AAPL", "pe_ratio", 28.0, known_at="2023-03-01", valid_from="2022-12-31"))

    def test_pit_query_excludes_future_restatement(self):
        # On Feb 01, P/E ratio must return 25.0 (the March restatement was not known yet!)
        res = self.store.query_as_of("AAPL", "pe_ratio", as_of_date="2023-02-01")
        self.assertEqual(res.value, 25.0)
        self.assertEqual(res.known_at, "2023-01-15")

    def test_pit_query_includes_restatement_after_known_date(self):
        # On March 15, P/E ratio returns 28.0 (restatement is now known)
        res = self.store.query_as_of("AAPL", "pe_ratio", as_of_date="2023-03-15")
        self.assertEqual(res.value, 28.0)
        self.assertEqual(res.known_at, "2023-03-01")

    def test_audit_leakage_flags_future_records(self):
        report = self.store.audit_leakage("AAPL", "pe_ratio", backtest_date="2023-02-01")
        self.assertTrue(report.has_future_leakage)
        self.assertEqual(report.value, 25.0)
        # The audit must surface the wrong number a naive query would have used.
        self.assertEqual(report.naive_value, 28.0)
        self.assertEqual(report.naive_known_at, "2023-03-01")

    def test_audit_reports_no_leakage_once_all_records_are_known(self):
        report = self.store.audit_leakage("AAPL", "pe_ratio", backtest_date="2023-06-01")
        self.assertFalse(report.has_future_leakage)
        self.assertEqual(report.value, 28.0)
        self.assertIsNone(report.naive_value)

    def test_query_before_any_publication_returns_no_value(self):
        res = self.store.query_as_of("AAPL", "pe_ratio", as_of_date="2022-06-01")
        self.assertIsNone(res.value)
        self.assertIsNone(res.known_at)
        self.assertFalse(res.has_future_leakage)

    def test_unknown_series_returns_no_value(self):
        res = self.store.query_as_of("MSFT", "pe_ratio", as_of_date="2023-06-01")
        self.assertIsNone(res.value)

    def test_symbol_and_field_match_case_insensitively(self):
        # A case mismatch must not masquerade as "no data available".
        res = self.store.query_as_of("aapl", "PE_Ratio", as_of_date="2023-02-01")
        self.assertEqual(res.value, 25.0)


class TestConservativeDayBoundaries(unittest.TestCase):
    """Date-only values must widen away from admitting a record."""

    def setUp(self):
        self.store = PointInTimeStore()
        self.store.insert(PITRecord("AAPL", "eps", 1.5, known_at="2023-01-15", valid_from="2022-12-31"))

    def test_same_day_as_of_does_not_see_that_days_publication(self):
        # Publication time within Jan 15 is unknown, so it is not tradable on
        # Jan 15 -- this is the classic "released after the close" lookahead bug.
        res = self.store.query_as_of("AAPL", "eps", as_of_date="2023-01-15")
        self.assertIsNone(res.value)

    def test_next_day_as_of_sees_the_publication(self):
        res = self.store.query_as_of("AAPL", "eps", as_of_date="2023-01-16")
        self.assertEqual(res.value, 1.5)

    def test_intraday_timestamps_are_respected_exactly(self):
        store = PointInTimeStore()
        # Earnings released at 20:15 UTC, after the US cash close.
        store.insert(PITRecord("AAPL", "eps", 2.0, known_at="2023-06-01T20:15:00Z", valid_from="2023-03-31"))
        self.assertIsNone(store.query_as_of("AAPL", "eps", "2023-06-01T16:00:00Z").value)
        self.assertEqual(store.query_as_of("AAPL", "eps", "2023-06-01T20:15:00Z").value, 2.0)
        self.assertEqual(store.query_as_of("AAPL", "eps", "2023-06-02T09:30:00Z").value, 2.0)


class TestUtcOffsetNormalisation(unittest.TestCase):
    """Regression tests for RFC 3339 s5.1: string compare != chronological order."""

    def test_offset_timestamp_later_than_cutoff_is_excluded(self):
        store = PointInTimeStore()
        # 09:00 New York == 14:00 UTC, i.e. AFTER the 12:00 UTC cutoff.
        store.insert(PITRecord("AAPL", "eps", 3.0, known_at="2023-02-01T09:00:00-05:00", valid_from="2022-12-31"))
        # Naive string comparison reads "...T09:00:00-05:00" <= "...T12:00:00Z"
        # as True and leaks the record. UTC normalisation excludes it.
        res = store.query_as_of("AAPL", "eps", "2023-02-01T12:00:00Z")
        self.assertIsNone(res.value)

    def test_offset_timestamp_earlier_than_cutoff_is_included(self):
        store = PointInTimeStore()
        # 20:00 Tokyo == 11:00 UTC, i.e. BEFORE the 12:00 UTC cutoff.
        store.insert(PITRecord("AAPL", "eps", 4.0, known_at="2023-02-01T20:00:00+09:00", valid_from="2022-12-31"))
        # Naive string comparison reads "...T20:..." <= "...T12:..." as False
        # and wrongly hides an already-published figure.
        res = store.query_as_of("AAPL", "eps", "2023-02-01T12:00:00Z")
        self.assertEqual(res.value, 4.0)


class TestValidTimeAxis(unittest.TestCase):
    """known_at and valid_from are independent axes (SQL:2011 bitemporal model)."""

    def setUp(self):
        self.store = PointInTimeStore()
        # Guidance published Jan 10 but only effective for the period ending Jun 30.
        self.store.insert(PITRecord("AAPL", "eps_guidance", 9.0, known_at="2023-01-10", valid_from="2023-06-30"))

    def test_record_not_yet_in_effect_is_excluded(self):
        res = self.store.query_as_of("AAPL", "eps_guidance", as_of_date="2023-02-01")
        self.assertIsNone(res.value)

    def test_record_in_effect_is_returned(self):
        res = self.store.query_as_of("AAPL", "eps_guidance", as_of_date="2023-07-01")
        self.assertEqual(res.value, 9.0)

    def test_valid_as_of_decouples_the_two_axes(self):
        # "What did we know on Feb 01 about the period ending Dec 31?"
        res = self.store.query_as_of(
            "AAPL", "eps_guidance", as_of_date="2023-02-01", valid_as_of="2023-12-31"
        )
        self.assertEqual(res.value, 9.0)


class TestDeterministicResolution(unittest.TestCase):
    """Simultaneous publications must resolve identically every run."""

    @staticmethod
    def _build(order):
        store = PointInTimeStore()
        for rec in order:
            store.insert(rec)
        return store

    def setUp(self):
        self.original = PITRecord("AAPL", "eps", 5.0, known_at="2023-04-01", valid_from="2023-03-31", revision=0)
        self.corrected = PITRecord("AAPL", "eps", 6.0, known_at="2023-04-01", valid_from="2023-03-31", revision=1)

    def test_higher_revision_wins_regardless_of_insertion_order(self):
        forward = self._build([self.original, self.corrected])
        reverse = self._build([self.corrected, self.original])
        self.assertEqual(forward.query_as_of("AAPL", "eps", "2023-05-01").value, 6.0)
        self.assertEqual(reverse.query_as_of("AAPL", "eps", "2023-05-01").value, 6.0)

    def test_result_reports_the_selected_revision(self):
        store = self._build([self.original, self.corrected])
        self.assertEqual(store.query_as_of("AAPL", "eps", "2023-05-01").revision, 1)


class TestInputValidation(unittest.TestCase):
    def test_unpadded_date_is_rejected(self):
        # "2023-9-01" sorts AFTER "2023-10-01" lexicographically -- accepting it
        # would silently select the wrong point-in-time record.
        with self.assertRaises(TemporalFormatError):
            PITRecord("AAPL", "eps", 1.0, known_at="2023-9-01", valid_from="2022-12-31")

    def test_compact_basic_format_date_is_rejected(self):
        # Parseable by Python, but would skip the conservative end-of-day widening.
        with self.assertRaises(TemporalFormatError):
            PITRecord("AAPL", "eps", 1.0, known_at="20230115", valid_from="2022-12-31")

    def test_non_iso_and_empty_dates_are_rejected(self):
        for bad in ("01/15/2023", "", "   ", "not-a-date", "2023-13-45"):
            with self.subTest(bad=bad):
                with self.assertRaises(TemporalFormatError):
                    PITRecord("AAPL", "eps", 1.0, known_at=bad, valid_from="2022-12-31")

    def test_non_string_date_is_rejected(self):
        with self.assertRaises(TemporalFormatError):
            PITRecord("AAPL", "eps", 1.0, known_at=20230115, valid_from="2022-12-31")

    def test_invalid_valid_from_is_rejected(self):
        with self.assertRaises(TemporalFormatError):
            PITRecord("AAPL", "eps", 1.0, known_at="2023-01-15", valid_from="2022-12")

    def test_non_finite_values_are_rejected(self):
        for bad in (float("nan"), float("inf"), float("-inf")):
            with self.subTest(bad=bad):
                with self.assertRaises(ValueError):
                    PITRecord("AAPL", "eps", bad, known_at="2023-01-15", valid_from="2022-12-31")

    def test_empty_symbol_or_field_is_rejected(self):
        with self.assertRaises(ValueError):
            PITRecord("", "eps", 1.0, known_at="2023-01-15", valid_from="2022-12-31")
        with self.assertRaises(ValueError):
            PITRecord("AAPL", "  ", 1.0, known_at="2023-01-15", valid_from="2022-12-31")

    def test_negative_revision_is_rejected(self):
        with self.assertRaises(ValueError):
            PITRecord("AAPL", "eps", 1.0, known_at="2023-01-15", valid_from="2022-12-31", revision=-1)

    def test_bad_query_date_is_rejected(self):
        store = PointInTimeStore()
        store.insert(PITRecord("AAPL", "eps", 1.0, known_at="2023-01-15", valid_from="2022-12-31"))
        with self.assertRaises(TemporalFormatError):
            store.query_as_of("AAPL", "eps", as_of_date="2023-2-1")

    def test_insert_rejects_non_record(self):
        with self.assertRaises(TypeError):
            PointInTimeStore().insert({"symbol": "AAPL"})


if __name__ == "__main__":
    unittest.main()
