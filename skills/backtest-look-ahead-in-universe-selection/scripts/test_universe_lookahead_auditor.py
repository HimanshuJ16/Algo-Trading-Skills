import unittest
from datetime import date, datetime, timedelta, timezone

from universe_lookahead_auditor import (
    AuditResult,
    ConstituentRecord,
    UniverseAuditError,
    UniverseFindingType,
    UniverseLookaheadAuditor,
)

SNAPSHOT = datetime(2020, 1, 1)


class TestUniverseLookaheadAuditor(unittest.TestCase):
    def setUp(self):
        self.auditor = UniverseLookaheadAuditor()

    def _types(self, result: AuditResult):
        return {f.finding_type for f in result.findings}

    # ------------------------------------------------------------------ baseline

    def test_clean_universe(self):
        constituents = [
            ConstituentRecord("AAPL", datetime(1980, 12, 12), None, datetime(2019, 12, 31)),
            ConstituentRecord("GE", datetime(1900, 1, 1), datetime(2025, 1, 1), datetime(2019, 12, 31)),
        ]
        result = self.auditor.audit_universe_snapshot(SNAPSHOT, constituents)
        self.assertTrue(result.is_clean)
        self.assertFalse(result.has_violations)
        self.assertEqual(result.findings, [])

    def test_lookahead_publication_date(self):
        constituents = [
            # Data wasn't publicly known until Jan 5th!
            ConstituentRecord("TSLA", datetime(2019, 1, 1), None, datetime(2020, 1, 5)),
        ]
        result = self.auditor.audit_universe_snapshot(SNAPSHOT, constituents)
        self.assertFalse(result.is_clean)
        self.assertTrue(result.has_violations)
        self.assertTrue(any("Lookahead Leak" in v for v in result.lookahead_violations))
        self.assertIn(UniverseFindingType.LOOKAHEAD_LEAK, self._types(result))

    def test_survivorship_bias_warning(self):
        # 51 constituents, NONE of them have a removed_date. Highly suspicious for a broad index.
        constituents = [
            ConstituentRecord(f"SYM_{i}", datetime(2010, 1, 1), None, datetime(2019, 12, 31))
            for i in range(51)
        ]
        result = self.auditor.audit_universe_snapshot(SNAPSHOT, constituents)
        self.assertFalse(result.is_clean)
        self.assertTrue(any("Survivorship Bias" in w for w in result.survivorship_warnings))
        # A heuristic warning must not be reported as a hard look-ahead violation.
        self.assertFalse(result.has_violations)
        self.assertEqual(result.lookahead_violations, [])

    def test_survivorship_threshold_is_configurable(self):
        constituents = [
            ConstituentRecord(f"SYM_{i}", datetime(2010, 1, 1), None, datetime(2019, 12, 31))
            for i in range(6)
        ]
        self.assertTrue(self.auditor.audit_universe_snapshot(SNAPSHOT, constituents).is_clean)
        strict = UniverseLookaheadAuditor(survivorship_warning_threshold=5)
        self.assertFalse(strict.audit_universe_snapshot(SNAPSHOT, constituents).is_clean)

    # ------------------------------------ same-day publication granularity (regression)

    def test_same_day_date_granular_publication_is_a_leak(self):
        """Regression: a date-only publication stamp on the snapshot date is not usable.

        The prior implementation compared raw instants, so midnight-on-the-snapshot-date
        compared as "not after" the snapshot and passed. A date-only stamp cannot prove
        the data existed before the decision instant, so it must be read as end of day.
        """
        constituents = [
            ConstituentRecord("SNAP", datetime(2019, 1, 1), None, datetime(2020, 1, 1)),
        ]
        result = self.auditor.audit_universe_snapshot(SNAPSHOT, constituents)
        self.assertTrue(result.has_violations)
        self.assertIn(UniverseFindingType.LOOKAHEAD_LEAK, self._types(result))
        self.assertIn("end of day", result.lookahead_violations[0])

    def test_prior_day_date_granular_publication_is_clean(self):
        """End-of-day normalisation must not flag a stamp from a strictly earlier date."""
        constituents = [
            ConstituentRecord("PRIOR", datetime(2019, 1, 1), None, datetime(2019, 12, 31)),
        ]
        self.assertTrue(self.auditor.audit_universe_snapshot(SNAPSHOT, constituents).is_clean)

    def test_intraday_publication_before_decision_instant_is_clean(self):
        """A real intraday stamp is compared as an instant, with no EOD normalisation."""
        snapshot = datetime(2020, 1, 2, 9, 30)
        constituents = [
            ConstituentRecord("INTRA", datetime(2019, 1, 1), None, datetime(2020, 1, 2, 9, 0)),
        ]
        self.assertTrue(self.auditor.audit_universe_snapshot(snapshot, constituents).is_clean)

    def test_intraday_publication_after_decision_instant_is_a_leak(self):
        snapshot = datetime(2020, 1, 2, 9, 30)
        constituents = [
            ConstituentRecord("LATE", datetime(2019, 1, 1), None, datetime(2020, 1, 2, 17, 15)),
        ]
        result = self.auditor.audit_universe_snapshot(snapshot, constituents)
        self.assertTrue(result.has_violations)
        self.assertNotIn("end of day", result.lookahead_violations[0])

    def test_end_of_day_normalisation_can_be_disabled(self):
        lenient = UniverseLookaheadAuditor(date_granular_publication_is_end_of_day=False)
        constituents = [
            ConstituentRecord("SNAP", datetime(2019, 1, 1), None, datetime(2020, 1, 1)),
        ]
        self.assertTrue(lenient.audit_universe_snapshot(SNAPSHOT, constituents).is_clean)

    # ------------------------------------------------- effective-date interval semantics

    def test_addition_effective_on_snapshot_date_is_a_member(self):
        """Index changes take effect prior to the open, so added_date == T is a member."""
        constituents = [
            ConstituentRecord("NEW", SNAPSHOT, None, datetime(2019, 12, 20)),
        ]
        self.assertTrue(self.auditor.audit_universe_snapshot(SNAPSHOT, constituents).is_clean)

    def test_future_addition_is_flagged(self):
        constituents = [
            ConstituentRecord("FUTR", datetime(2020, 1, 2), None, datetime(2019, 12, 20)),
        ]
        result = self.auditor.audit_universe_snapshot(SNAPSHOT, constituents)
        self.assertIn(UniverseFindingType.FUTURE_ADDITION, self._types(result))

    def test_removal_effective_on_snapshot_date_is_a_zombie(self):
        """Membership is half-open [added, removed): removed_date == T means not a member."""
        constituents = [
            ConstituentRecord("GONE", datetime(2010, 1, 1), SNAPSHOT, datetime(2019, 12, 20)),
        ]
        result = self.auditor.audit_universe_snapshot(SNAPSHOT, constituents)
        self.assertIn(UniverseFindingType.ZOMBIE_ASSET, self._types(result))

    def test_removal_after_snapshot_date_is_still_a_member(self):
        constituents = [
            ConstituentRecord(
                "STAY", datetime(2010, 1, 1), SNAPSHOT + timedelta(days=1), datetime(2019, 12, 20)
            ),
        ]
        self.assertTrue(self.auditor.audit_universe_snapshot(SNAPSHOT, constituents).is_clean)

    # ------------------------------------------------------------ universe-level checks

    def test_duplicate_symbol_is_flagged_case_insensitively(self):
        constituents = [
            ConstituentRecord("AAPL", datetime(2010, 1, 1), None, datetime(2019, 12, 20)),
            ConstituentRecord("aapl", datetime(2012, 1, 1), None, datetime(2019, 12, 20)),
        ]
        result = self.auditor.audit_universe_snapshot(SNAPSHOT, constituents)
        self.assertTrue(result.has_violations)
        self.assertIn(UniverseFindingType.DUPLICATE_SYMBOL, self._types(result))
        self.assertTrue(any("appears 2 times" in v for v in result.lookahead_violations))

    def test_vacuous_publication_dates_are_warned_about(self):
        """publication == effective everywhere means the leak check cannot ever fail."""
        added = datetime(2010, 1, 1)
        constituents = [
            ConstituentRecord("A", added, None, added),
            ConstituentRecord("B", added, None, added),
        ]
        result = self.auditor.audit_universe_snapshot(SNAPSHOT, constituents)
        self.assertFalse(result.is_clean)
        self.assertFalse(result.has_violations)
        self.assertIn(UniverseFindingType.VACUOUS_PUBLICATION_DATES, self._types(result))

    def test_one_shot_iterable_is_not_silently_drained(self):
        """A generator must not be consumed by validation, leaving a false-clean report."""
        constituents = (
            ConstituentRecord("LEAK", datetime(2019, 1, 1), None, datetime(2020, 1, 5))
            for _ in range(1)
        )
        result = self.auditor.audit_universe_snapshot(SNAPSHOT, constituents)
        self.assertTrue(result.has_violations)

    def test_empty_universe_audits_clean(self):
        result = self.auditor.audit_universe_snapshot(SNAPSHOT, [])
        self.assertTrue(result.is_clean)
        self.assertEqual(result.findings, [])

    def test_audit_does_not_mutate_input_records(self):
        record = ConstituentRecord("AAPL", datetime(2010, 1, 1), None, datetime(2019, 12, 31))
        self.auditor.audit_universe_snapshot(SNAPSHOT, [record])
        self.assertEqual(record.data_publication_date, datetime(2019, 12, 31))
        self.assertIsNone(record.removed_date)

    # --------------------------------------------------------------- input validation

    def test_mixed_timezone_awareness_raises(self):
        """Naive/aware comparison would otherwise raise TypeError mid-audit."""
        aware_snapshot = datetime(2020, 1, 1, tzinfo=timezone.utc)
        constituents = [
            ConstituentRecord("AAPL", datetime(2010, 1, 1), None, datetime(2019, 12, 31)),
        ]
        with self.assertRaises(UniverseAuditError):
            self.auditor.audit_universe_snapshot(aware_snapshot, constituents)

    def test_aware_timestamps_throughout_are_accepted(self):
        aware_snapshot = datetime(2020, 1, 1, tzinfo=timezone.utc)
        constituents = [
            ConstituentRecord(
                "AAPL",
                datetime(2010, 1, 1, tzinfo=timezone.utc),
                None,
                datetime(2019, 12, 31, tzinfo=timezone.utc),
            ),
        ]
        self.assertTrue(self.auditor.audit_universe_snapshot(aware_snapshot, constituents).is_clean)

    def test_plain_date_snapshot_raises(self):
        with self.assertRaises(UniverseAuditError):
            self.auditor.audit_universe_snapshot(date(2020, 1, 1), [])

    def test_removal_before_addition_raises(self):
        constituents = [
            ConstituentRecord("BAD", datetime(2015, 1, 1), datetime(2010, 1, 1), datetime(2009, 1, 1)),
        ]
        with self.assertRaises(UniverseAuditError):
            self.auditor.audit_universe_snapshot(SNAPSHOT, constituents)

    def test_blank_symbol_raises(self):
        constituents = [
            ConstituentRecord("   ", datetime(2010, 1, 1), None, datetime(2019, 12, 31)),
        ]
        with self.assertRaises(UniverseAuditError):
            self.auditor.audit_universe_snapshot(SNAPSHOT, constituents)

    def test_non_datetime_added_date_raises(self):
        constituents = [
            ConstituentRecord("BAD", date(2010, 1, 1), None, datetime(2019, 12, 31)),
        ]
        with self.assertRaises(UniverseAuditError):
            self.auditor.audit_universe_snapshot(SNAPSHOT, constituents)

    def test_negative_survivorship_threshold_raises(self):
        with self.assertRaises(UniverseAuditError):
            UniverseLookaheadAuditor(survivorship_warning_threshold=-1)


if __name__ == '__main__':
    unittest.main()
