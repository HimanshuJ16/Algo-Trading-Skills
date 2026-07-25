"""Unit tests for point-in-time-database-for-ml-training-data."""
import unittest
from pit_ml_database import PointInTimeMLDatabase, FeatureRecord, LabelRecord


class TestPointInTimeMLDatabase(unittest.TestCase):
    def setUp(self):
        self.db = PointInTimeMLDatabase()
        # Q4 earnings event on 2022-12-31, but available_at public release on 2023-01-20
        self.db.insert_features([
            FeatureRecord("AAPL", "eps", 1.50, event_timestamp="2022-12-31", available_at="2023-01-20"),
            FeatureRecord("AAPL", "eps", 1.80, event_timestamp="2023-03-31", available_at="2023-04-18"),
        ])

    def test_as_of_join_blocks_unreleased_data(self):
        labels = [
            LabelRecord("AAPL", label_timestamp="2023-01-10", target_value=0.02),  # Before public release!
            LabelRecord("AAPL", label_timestamp="2023-01-25", target_value=0.01),  # After public release!
        ]

        rows, report = self.db.as_of_join(labels, "eps")

        # Jan 10 query -> EPS should be None (not available yet)
        self.assertFalse(rows[0].is_valid_pit)
        self.assertIsNone(rows[0].feature_value)

        # Jan 25 query -> EPS = 1.50 (now available)
        self.assertTrue(rows[1].is_valid_pit)
        self.assertEqual(rows[1].feature_value, 1.50)
        self.assertGreater(report.future_leakage_prevented_count, 0)

    def test_missing_symbol_returns_invalid_pit(self):
        labels = [LabelRecord("MSFT", label_timestamp="2023-01-25", target_value=0.05)]
        rows, report = self.db.as_of_join(labels, "eps")
        self.assertEqual(report.missing_feature_rows, 1)
        self.assertFalse(rows[0].is_valid_pit)


if __name__ == "__main__":
    unittest.main()
