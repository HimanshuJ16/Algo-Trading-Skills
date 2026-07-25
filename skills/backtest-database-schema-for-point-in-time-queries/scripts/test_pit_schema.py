"""Unit tests for backtest-database-schema-for-point-in-time-queries."""
import unittest
from pit_schema import PointInTimeStore, PITRecord


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


if __name__ == "__main__":
    unittest.main()
