"""
Unit tests for point-in-time-database-for-ml-training-data.

Expected values are derived by hand from the fixtures below, never by
re-running the engine's own arithmetic. Several tests are explicit regression
tests against defects in the v1.0.0 engine and are annotated as such.
"""
import datetime as dt
import math
import unittest

from pit_ml_database import (
    DATE_ONLY_START_OF_DAY,
    FeatureRecord,
    LabelRecord,
    PointInTimeMLDatabase,
)


class TestAsOfJoinCore(unittest.TestCase):
    """Q4 EPS: period ends 2022-12-31, filing lands 2023-01-20."""

    def setUp(self):
        self.db = PointInTimeMLDatabase()
        self.db.insert_features([
            FeatureRecord("AAPL", "eps", 1.50, "2022-12-31", "2023-01-20"),
            FeatureRecord("AAPL", "eps", 1.80, "2023-03-31", "2023-04-18"),
        ])

    def test_unreleased_value_is_not_joined(self):
        rows, report = self.db.as_of_join(
            [LabelRecord("AAPL", "2023-01-10", 0.02)], "eps"
        )
        self.assertFalse(rows[0].is_valid_pit)
        self.assertIsNone(rows[0].feature_value)
        self.assertEqual(report.missing_feature_rows, 1)

    def test_released_value_is_joined(self):
        rows, _ = self.db.as_of_join(
            [LabelRecord("AAPL", "2023-01-25", 0.01)], "eps"
        )
        self.assertTrue(rows[0].is_valid_pit)
        self.assertEqual(rows[0].feature_value, 1.50)
        # available_at 2023-01-20 resolves to 2023-01-21T00:00Z under the
        # end-of-day default; 2023-01-25T00:00Z is exactly 4 days later.
        self.assertAlmostEqual(rows[0].staleness_days, 4.0)

    def test_missing_symbol_yields_invalid_row_not_an_error(self):
        rows, report = self.db.as_of_join(
            [LabelRecord("MSFT", "2023-01-25", 0.05)], "eps"
        )
        self.assertEqual(report.missing_feature_rows, 1)
        self.assertFalse(rows[0].is_valid_pit)
        self.assertIsNone(rows[0].naive_join_value)
        self.assertFalse(rows[0].leakage_blocked)

    def test_rows_are_returned_in_label_input_order(self):
        labels = [
            LabelRecord("AAPL", "2023-05-01", 0.03),
            LabelRecord("AAPL", "2023-01-25", 0.01),
        ]
        rows, _ = self.db.as_of_join(labels, "eps")
        self.assertEqual(rows[0].feature_value, 1.80)
        self.assertEqual(rows[1].feature_value, 1.50)


class TestSameDayPublicationBoundary(unittest.TestCase):
    """
    Regression: v1.0.0 compared raw date strings, so ``available_at`` equal to
    the label date satisfied ``<=`` and a value published on day D was usable
    for a decision made on day D.
    """

    def setUp(self):
        self.db = PointInTimeMLDatabase()
        self.db.insert_features([
            FeatureRecord("AAPL", "eps", 1.50, "2022-12-31", "2023-01-20"),
        ])

    def test_same_day_publication_is_not_joinable_by_default(self):
        rows, _ = self.db.as_of_join(
            [LabelRecord("AAPL", "2023-01-20", 0.01)], "eps"
        )
        self.assertIsNone(rows[0].feature_value)

    def test_next_day_midnight_is_the_exact_inclusive_boundary(self):
        rows, _ = self.db.as_of_join(
            [LabelRecord("AAPL", "2023-01-21T00:00:00Z", 0.01)], "eps"
        )
        self.assertEqual(rows[0].feature_value, 1.50)
        self.assertAlmostEqual(rows[0].staleness_days, 0.0)

    def test_one_microsecond_before_the_boundary_is_excluded(self):
        rows, _ = self.db.as_of_join(
            [LabelRecord("AAPL", "2023-01-20T23:59:59.999999Z", 0.01)], "eps"
        )
        self.assertIsNone(rows[0].feature_value)

    def test_start_of_day_policy_permits_same_day_use(self):
        permissive = PointInTimeMLDatabase(
            date_only_availability=DATE_ONLY_START_OF_DAY
        )
        permissive.insert_features([
            FeatureRecord("AAPL", "eps", 1.50, "2022-12-31", "2023-01-20"),
        ])
        rows, _ = permissive.as_of_join(
            [LabelRecord("AAPL", "2023-01-20", 0.01)], "eps"
        )
        self.assertEqual(rows[0].feature_value, 1.50)


class TestTimestampNormalisation(unittest.TestCase):
    """
    Regression: v1.0.0 ordered timestamps by lexicographic string comparison.
    RFC 3339 section 5.1 permits that only when every value shares one zone
    representation and one fractional-second precision.
    """

    def test_utc_offset_is_converted_before_comparison(self):
        db = PointInTimeMLDatabase()
        # 09:00-05:00 is 14:00Z. String comparison puts "09:00..." before
        # "12:00...", which would wrongly admit it at a 12:00Z decision.
        db.insert_features([
            FeatureRecord("X", "f", 7.0, "2023-01-01", "2023-02-01T09:00:00-05:00"),
        ])
        early, _ = db.as_of_join([LabelRecord("X", "2023-02-01T12:00:00Z", 1.0)], "f")
        self.assertIsNone(early[0].feature_value)

        late, _ = db.as_of_join([LabelRecord("X", "2023-02-01T15:00:00Z", 1.0)], "f")
        self.assertEqual(late[0].feature_value, 7.0)

    def test_unpadded_date_is_rejected_at_ingest(self):
        db = PointInTimeMLDatabase()
        with self.assertRaises(ValueError):
            db.insert_features([
                FeatureRecord("X", "f", 1.0, "2023-01-01", "2023-9-01"),
            ])

    def test_naive_datetime_is_interpreted_as_utc(self):
        db = PointInTimeMLDatabase()
        db.insert_features([
            FeatureRecord(
                "X", "f", 3.0, "2023-01-01", dt.datetime(2023, 2, 1, 12, 0, 0)
            ),
        ])
        rows, _ = db.as_of_join([LabelRecord("X", "2023-02-01T12:00:00Z", 1.0)], "f")
        self.assertEqual(rows[0].feature_value, 3.0)

    def test_date_object_is_treated_as_date_granular(self):
        db = PointInTimeMLDatabase()
        db.insert_features([
            FeatureRecord("X", "f", 4.0, "2023-01-01", dt.date(2023, 2, 1)),
        ])
        same_day, _ = db.as_of_join([LabelRecord("X", "2023-02-01", 1.0)], "f")
        self.assertIsNone(same_day[0].feature_value)
        next_day, _ = db.as_of_join([LabelRecord("X", "2023-02-02", 1.0)], "f")
        self.assertEqual(next_day[0].feature_value, 4.0)


class TestRevisionResolution(unittest.TestCase):
    """
    Regression: v1.0.0 resolved ties with ``max(..., key=available_at)``, which
    returns the first maximal element and so depended on insertion order.
    """

    def test_highest_revision_wins_at_the_same_instant(self):
        for order in ((1, 0), (0, 1)):
            with self.subTest(insert_order=order):
                db = PointInTimeMLDatabase()
                db.insert_features([
                    FeatureRecord(
                        "X", "f", 10.0 * rev, "2023-01-01",
                        "2023-02-01T12:00:00Z", revision=rev,
                    )
                    for rev in order
                ])
                rows, _ = db.as_of_join([LabelRecord("X", "2023-03-01", 1.0)], "f")
                self.assertEqual(rows[0].feature_value, 10.0)

    def test_restatement_is_invisible_until_it_is_published(self):
        db = PointInTimeMLDatabase()
        db.insert_features([
            FeatureRecord("AAPL", "eps", 1.50, "2022-12-31", "2023-02-15", revision=0),
            FeatureRecord("AAPL", "eps", 1.20, "2022-12-31", "2023-08-10", revision=1),
        ])
        before, _ = db.as_of_join([LabelRecord("AAPL", "2023-03-01", 0.0)], "eps")
        self.assertEqual(before[0].feature_value, 1.50)
        after, _ = db.as_of_join([LabelRecord("AAPL", "2023-09-01", 0.0)], "eps")
        self.assertEqual(after[0].feature_value, 1.20)


class TestNaiveJoinLeakageAudit(unittest.TestCase):
    """
    Regression: v1.0.0 reported ``len(naive_candidates) - len(valid_candidates)``
    -- a count of filtered *records*. A naive join returns at most one value per
    label, so at most one label row can be wrong. The three unreleased records
    below made v1.0.0 report 3; the truth is 1.
    """

    def setUp(self):
        self.db = PointInTimeMLDatabase()
        self.db.insert_features([
            FeatureRecord("X", "f", 10.0, "2023-01-31", "2023-04-18"),
            FeatureRecord("X", "f", 20.0, "2023-02-28", "2023-04-18"),
            FeatureRecord("X", "f", 30.0, "2023-03-31", "2023-04-18"),
        ])

    def test_one_label_row_is_flagged_not_three_records(self):
        rows, report = self.db.as_of_join([LabelRecord("X", "2023-04-01", 0.0)], "f")
        self.assertEqual(report.future_leakage_prevented_count, 1)
        self.assertTrue(rows[0].leakage_blocked)
        # A naive event-date join picks the latest event on or before Apr 1:
        # the 2023-03-31 record, value 30.0 -- knowledge from Apr 18.
        self.assertEqual(rows[0].naive_join_value, 30.0)
        self.assertIsNone(rows[0].feature_value)

    def test_no_flag_once_the_naive_and_pit_answers_coincide(self):
        rows, report = self.db.as_of_join([LabelRecord("X", "2023-05-01", 0.0)], "f")
        self.assertEqual(report.future_leakage_prevented_count, 0)
        self.assertFalse(rows[0].leakage_blocked)
        self.assertEqual(rows[0].feature_value, 30.0)
        self.assertEqual(rows[0].naive_join_value, 30.0)

    def test_quarter_end_before_filing_is_the_classic_leak(self):
        db = PointInTimeMLDatabase()
        db.insert_features([
            FeatureRecord("AAPL", "eps", 1.50, "2022-12-31", "2023-01-20"),
            FeatureRecord("AAPL", "eps", 1.80, "2023-03-31", "2023-04-18"),
        ])
        # On Apr 1 the quarter has ended but the filing has not landed.
        rows, report = db.as_of_join([LabelRecord("AAPL", "2023-04-01", 0.0)], "eps")
        self.assertEqual(rows[0].feature_value, 1.50)
        self.assertEqual(rows[0].naive_join_value, 1.80)
        self.assertEqual(report.future_leakage_prevented_count, 1)


class TestStalenessBound(unittest.TestCase):
    def test_value_older_than_the_bound_is_refused(self):
        db = PointInTimeMLDatabase(max_staleness_days=90)
        db.insert_features([FeatureRecord("X", "f", 5.0, "2010-01-01", "2010-01-05")])
        rows, report = db.as_of_join([LabelRecord("X", "2023-01-01", 1.0)], "f")
        self.assertTrue(rows[0].is_stale)
        self.assertFalse(rows[0].is_valid_pit)
        self.assertIsNone(rows[0].feature_value)
        self.assertEqual(report.stale_feature_rows, 1)
        self.assertEqual(report.missing_feature_rows, 0)
        # The dropped value's provenance stays visible for the audit trail.
        self.assertIsNotNone(rows[0].feature_available_at)

    def test_exact_bound_is_inclusive(self):
        db = PointInTimeMLDatabase(max_staleness_days=10)
        db.insert_features([FeatureRecord("X", "f", 5.0, "2023-01-01", "2023-01-01")])
        # available_at resolves to 2023-01-02T00:00Z; +10 days is 2023-01-12.
        on_bound, _ = db.as_of_join([LabelRecord("X", "2023-01-12", 1.0)], "f")
        self.assertEqual(on_bound[0].feature_value, 5.0)
        past_bound, _ = db.as_of_join([LabelRecord("X", "2023-01-13", 1.0)], "f")
        self.assertIsNone(past_bound[0].feature_value)

    def test_unbounded_by_default(self):
        db = PointInTimeMLDatabase()
        db.insert_features([FeatureRecord("X", "f", 5.0, "2010-01-01", "2010-01-05")])
        rows, _ = db.as_of_join([LabelRecord("X", "2023-01-01", 1.0)], "f")
        self.assertTrue(rows[0].is_valid_pit)


class TestIngestionLag(unittest.TestCase):
    """Regression: v1.0.0 accepted ``default_lag_days`` and never applied it."""

    def test_lag_shifts_the_availability_instant(self):
        db = PointInTimeMLDatabase(ingestion_lag_days=2)
        db.insert_features([FeatureRecord("X", "f", 1.0, "2023-01-01", "2023-01-10")])
        # 2023-01-10 -> end of day 2023-01-11T00:00Z -> +2 days = 2023-01-13T00:00Z.
        before, _ = db.as_of_join([LabelRecord("X", "2023-01-12", 1.0)], "f")
        self.assertIsNone(before[0].feature_value)
        on_time, _ = db.as_of_join([LabelRecord("X", "2023-01-13", 1.0)], "f")
        self.assertEqual(on_time[0].feature_value, 1.0)


class TestEventAxisGate(unittest.TestCase):
    def test_forward_looking_value_is_joined_by_default(self):
        db = PointInTimeMLDatabase()
        # Guidance published 2023-01-05 about the quarter ending 2023-03-31.
        db.insert_features([FeatureRecord("X", "guide", 2.0, "2023-03-31", "2023-01-05")])
        rows, _ = db.as_of_join([LabelRecord("X", "2023-02-01", 1.0)], "guide")
        self.assertEqual(rows[0].feature_value, 2.0)

    def test_event_gate_excludes_it_when_requested(self):
        db = PointInTimeMLDatabase(require_event_before_label=True)
        db.insert_features([FeatureRecord("X", "guide", 2.0, "2023-03-31", "2023-01-05")])
        rows, _ = db.as_of_join([LabelRecord("X", "2023-02-01", 1.0)], "guide")
        self.assertIsNone(rows[0].feature_value)
        after, _ = db.as_of_join([LabelRecord("X", "2023-04-01", 1.0)], "guide")
        self.assertEqual(after[0].feature_value, 2.0)


class TestTrainingMatrix(unittest.TestCase):
    def setUp(self):
        self.db = PointInTimeMLDatabase()
        self.db.insert_features([
            FeatureRecord("AAPL", "eps", 1.5, "2022-12-31", "2023-01-20"),
            FeatureRecord("AAPL", "rev", 100.0, "2022-12-31", "2023-02-20"),
        ])

    def test_incomplete_rows_are_flagged_and_never_filled(self):
        rows, report = self.db.build_training_matrix(
            [LabelRecord("AAPL", "2023-01-25", 0.01),
             LabelRecord("AAPL", "2023-03-01", 0.03)],
            ["eps", "rev"],
        )
        self.assertEqual(rows[0].features, {"eps": 1.5, "rev": None})
        self.assertFalse(rows[0].is_complete)
        self.assertEqual(rows[1].features, {"eps": 1.5, "rev": 100.0})
        self.assertTrue(rows[1].is_complete)
        # 2 label rows x 2 features = 4 cells, 1 of them missing.
        self.assertEqual(report.total_joined_rows, 2)
        self.assertEqual(report.missing_feature_rows, 1)
        self.assertEqual(report.valid_pit_rows, 3)

    def test_duplicate_feature_names_are_rejected(self):
        with self.assertRaises(ValueError):
            self.db.build_training_matrix(
                [LabelRecord("AAPL", "2023-03-01", 0.0)], ["eps", "eps"]
            )

    def test_a_bare_string_is_not_accepted_as_a_feature_list(self):
        with self.assertRaises(ValueError):
            self.db.build_training_matrix(
                [LabelRecord("AAPL", "2023-03-01", 0.0)], "eps"
            )

    def test_empty_feature_list_is_rejected(self):
        with self.assertRaises(ValueError):
            self.db.build_training_matrix(
                [LabelRecord("AAPL", "2023-03-01", 0.0)], []
            )


class TestInputValidation(unittest.TestCase):
    """Regression: v1.0.0 validated nothing and let NaN reach the matrix."""

    def setUp(self):
        self.db = PointInTimeMLDatabase()

    def test_non_finite_feature_value_is_rejected(self):
        for bad in (float("nan"), float("inf"), float("-inf")):
            with self.subTest(value=bad):
                with self.assertRaises(ValueError):
                    self.db.insert_features([
                        FeatureRecord("X", "f", bad, "2023-01-01", "2023-01-02"),
                    ])

    def test_non_finite_target_is_rejected(self):
        self.db.insert_features([FeatureRecord("X", "f", 1.0, "2023-01-01", "2023-01-02")])
        with self.assertRaises(ValueError):
            self.db.as_of_join([LabelRecord("X", "2023-02-01", float("nan"))], "f")

    def test_empty_identifiers_are_rejected(self):
        with self.assertRaises(ValueError):
            self.db.insert_features([
                FeatureRecord("  ", "f", 1.0, "2023-01-01", "2023-01-02"),
            ])
        with self.assertRaises(ValueError):
            self.db.insert_features([
                FeatureRecord("X", "", 1.0, "2023-01-01", "2023-01-02"),
            ])
        with self.assertRaises(ValueError):
            self.db.as_of_join([], "")

    def test_negative_revision_is_rejected(self):
        with self.assertRaises(ValueError):
            self.db.insert_features([
                FeatureRecord("X", "f", 1.0, "2023-01-01", "2023-01-02", revision=-1),
            ])

    def test_a_rejected_batch_leaves_the_store_unchanged(self):
        with self.assertRaises(ValueError):
            self.db.insert_features([
                FeatureRecord("X", "f", 1.0, "2023-01-01", "2023-01-02"),
                FeatureRecord("X", "f", float("nan"), "2023-01-01", "2023-01-03"),
            ])
        rows, _ = self.db.as_of_join([LabelRecord("X", "2023-06-01", 0.0)], "f")
        self.assertIsNone(rows[0].feature_value)

    def test_invalid_constructor_arguments_are_rejected(self):
        with self.assertRaises(ValueError):
            PointInTimeMLDatabase(ingestion_lag_days=-1)
        with self.assertRaises(ValueError):
            PointInTimeMLDatabase(max_staleness_days=0)
        with self.assertRaises(ValueError):
            PointInTimeMLDatabase(max_staleness_days=float("nan"))
        with self.assertRaises(ValueError):
            PointInTimeMLDatabase(date_only_availability="whenever")

    def test_wrong_record_types_are_rejected(self):
        with self.assertRaises(ValueError):
            self.db.insert_features([{"symbol": "X"}])
        with self.assertRaises(ValueError):
            self.db.insert_features(
                FeatureRecord("X", "f", 1.0, "2023-01-01", "2023-01-02")
            )
        with self.assertRaises(ValueError):
            self.db.as_of_join([("X", "2023-01-01", 0.0)], "f")

    def test_empty_label_set_produces_an_empty_report(self):
        rows, report = self.db.as_of_join([], "f")
        self.assertEqual(rows, [])
        self.assertEqual(report.total_joined_rows, 0)
        self.assertEqual(report.valid_pit_rows, 0)


class TestReportArithmetic(unittest.TestCase):
    def test_counters_partition_the_row_set(self):
        db = PointInTimeMLDatabase(max_staleness_days=30)
        db.insert_features([
            FeatureRecord("X", "f", 1.0, "2020-01-01", "2020-01-02"),  # stale by 2023
            FeatureRecord("Y", "f", 2.0, "2023-01-01", "2023-01-02"),  # fresh
        ])
        labels = [
            LabelRecord("X", "2023-01-10", 0.0),  # stale
            LabelRecord("Y", "2023-01-10", 0.0),  # valid
            LabelRecord("Z", "2023-01-10", 0.0),  # missing
        ]
        rows, report = db.as_of_join(labels, "f")
        self.assertEqual(report.total_joined_rows, 3)
        self.assertEqual(report.stale_feature_rows, 1)
        self.assertEqual(report.missing_feature_rows, 1)
        self.assertEqual(report.valid_pit_rows, 1)
        self.assertEqual(
            report.valid_pit_rows
            + report.stale_feature_rows
            + report.missing_feature_rows,
            report.total_joined_rows,
        )
        self.assertEqual(sum(1 for r in rows if r.is_valid_pit), 1)

    def test_no_valid_row_ever_carries_a_non_finite_value(self):
        db = PointInTimeMLDatabase()
        db.insert_features([FeatureRecord("X", "f", 1.0, "2023-01-01", "2023-01-02")])
        rows, _ = db.as_of_join([LabelRecord("X", "2023-06-01", 0.0)], "f")
        for row in rows:
            if row.is_valid_pit:
                self.assertTrue(math.isfinite(row.feature_value))


if __name__ == "__main__":
    unittest.main()
