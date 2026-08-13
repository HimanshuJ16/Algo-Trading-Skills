import datetime as dt
import unittest

import pandas as pd

from alt_data_lag_enforcer import (
    EFFECTIVE_PUBLICATION_COL,
    AltDataLagEnforcer,
    AltDataLagError,
)


class TestAltDataLagEnforcer(unittest.TestCase):

    def setUp(self):
        # Mock alternative data (e.g., credit card transaction volume)
        data = {
            "event_date": ["2023-11-24", "2023-11-25", "2023-11-26"],
            "cc_volume": [1500, 1600, 1200]
        }
        self.df_no_pub = pd.DataFrame(data)

        data_with_pub = {
            "event_date": ["2023-11-24", "2023-11-25", "2023-11-26"],
            "pub_date": ["2023-11-26", "2023-11-28", "2023-11-27"], # Out of order publication
            "cc_volume": [1500, 1600, 1200]
        }
        self.df_pub = pd.DataFrame(data_with_pub)

    # ----------------------------------------------------------------- baseline

    def test_default_lag_enforcement(self):
        # Default lag is 3 days
        enforcer = AltDataLagEnforcer(self.df_no_pub, default_lag_days=3)

        # As of Nov 26: Nov 24 + 3 days = Nov 27. Therefore NO data is available yet.
        df_pit = enforcer.get_point_in_time_data("2023-11-26")
        self.assertEqual(len(df_pit), 0)

        # As of Nov 27: Nov 24 data becomes available.
        df_pit = enforcer.get_point_in_time_data("2023-11-27")
        self.assertEqual(len(df_pit), 1)
        self.assertEqual(df_pit.iloc[0]["event_date"], pd.Timestamp("2023-11-24"))

        # As of Nov 29: All data available.
        df_pit = enforcer.get_point_in_time_data("2023-11-29")
        self.assertEqual(len(df_pit), 3)

    def test_explicit_publication_date_enforcement(self):
        enforcer = AltDataLagEnforcer(self.df_pub, publication_date_col="pub_date")

        # As of Nov 26: Only Nov 24 data is published.
        df_pit = enforcer.get_point_in_time_data("2023-11-26")
        self.assertEqual(len(df_pit), 1)
        self.assertEqual(df_pit.iloc[0]["event_date"], pd.Timestamp("2023-11-24"))

        # As of Nov 27: Nov 26 data is published early! Nov 25 is still unpublished.
        df_pit = enforcer.get_point_in_time_data("2023-11-27")
        self.assertEqual(len(df_pit), 2)
        # Check that it returns Nov 24 and Nov 26 (sorted by event_date)
        self.assertEqual(df_pit.iloc[1]["event_date"], pd.Timestamp("2023-11-26"))

    def test_source_dataframe_is_not_mutated(self):
        snapshot = self.df_pub.copy()
        AltDataLagEnforcer(self.df_pub, publication_date_col="pub_date")
        pd.testing.assert_frame_equal(self.df_pub, snapshot)

    def test_internal_column_is_not_leaked(self):
        enforcer = AltDataLagEnforcer(self.df_no_pub)
        self.assertNotIn("_effective_pub_date", enforcer.get_point_in_time_data("2023-11-30").columns)

    # ------------------------------ silently-voided PIT guarantee (regression, critical)

    def test_missing_publication_column_raises_instead_of_guessing(self):
        """Regression: a mistyped publication column silently used the fallback lag.

        With a real publication date of 2024-06-01 and a typo'd column name, the old
        enforcer applied default_lag_days=3 to the event date and admitted the row on
        2023-11-27 -- six months before it existed.
        """
        df = pd.DataFrame({
            "event_date": ["2023-11-24"], "pub_date": ["2024-06-01"], "v": [1],
        })
        with self.assertRaises(AltDataLagError) as ctx:
            AltDataLagEnforcer(df, publication_date_col="publication_date")
        self.assertIn("default_lag_days", str(ctx.exception))

        # Opting into the fallback explicitly is still allowed.
        AltDataLagEnforcer(df, publication_date_col=None)

    def test_publication_before_event_is_rejected(self):
        """Regression: a row published before its own event was accepted silently."""
        df = pd.DataFrame({
            "event_date": ["2023-11-24"], "pub_date": ["2023-11-20"], "v": [1],
        })
        with self.assertRaises(AltDataLagError):
            AltDataLagEnforcer(df, publication_date_col="pub_date")

    def test_negative_default_lag_is_rejected(self):
        """Regression: lag=-5 made data visible five days before the event occurred."""
        with self.assertRaises(AltDataLagError):
            AltDataLagEnforcer(self.df_no_pub, default_lag_days=-5)

    def test_zero_lag_is_permitted(self):
        enforcer = AltDataLagEnforcer(self.df_no_pub, default_lag_days=0)
        self.assertEqual(len(enforcer.get_point_in_time_data("2023-11-24")), 1)

    # ------------------------------------------------------------ revision handling

    def test_restatements_without_a_key_return_both_versions(self):
        """Documents the default: both the original and the revision are returned."""
        rev = pd.DataFrame({
            "event_date": ["2023-11-24", "2023-11-24"],
            "pub_date": ["2023-11-27", "2023-12-05"],
            "cc_volume": [1500, 1720],
        })
        enforcer = AltDataLagEnforcer(rev, publication_date_col="pub_date")
        self.assertEqual(len(enforcer.get_point_in_time_data("2023-12-06")), 2)
        self.assertEqual(enforcer.lag_audit()["duplicate_event_rows_without_key"], 2)

    def test_revision_key_returns_the_version_known_on_the_query_date(self):
        rev = pd.DataFrame({
            "event_date": ["2023-11-24", "2023-11-24"],
            "pub_date": ["2023-11-27", "2023-12-05"],
            "cc_volume": [1500, 1720],
        })
        enforcer = AltDataLagEnforcer(
            rev, publication_date_col="pub_date", revision_key_cols=["event_date"]
        )

        # Before the restatement: only the original estimate existed.
        early = enforcer.get_point_in_time_data("2023-11-28")
        self.assertEqual(len(early), 1)
        self.assertEqual(early.iloc[0]["cc_volume"], 1500)

        # After it: the revision supersedes, and the original is not double-counted.
        late = enforcer.get_point_in_time_data("2023-12-06")
        self.assertEqual(len(late), 1)
        self.assertEqual(late.iloc[0]["cc_volume"], 1720)

    def test_revision_key_preserves_distinct_entities(self):
        """Deduplication must key on the full logical identity, not the date alone."""
        rev = pd.DataFrame({
            "ticker": ["AAA", "BBB", "AAA"],
            "event_date": ["2023-11-24", "2023-11-24", "2023-11-24"],
            "pub_date": ["2023-11-27", "2023-11-27", "2023-12-05"],
            "v": [1, 2, 3],
        })
        enforcer = AltDataLagEnforcer(
            rev, publication_date_col="pub_date", revision_key_cols=["ticker", "event_date"]
        )
        out = enforcer.get_point_in_time_data("2023-12-06")
        self.assertEqual(len(out), 2)
        self.assertEqual(sorted(out["v"].tolist()), [2, 3])

    # -------------------------------------------------------------- lag conventions

    def test_business_day_lag_skips_the_weekend(self):
        """Fri 2023-11-24 + 3 business days is Wed 2023-11-29, not Mon 2023-11-27."""
        friday = pd.DataFrame({"event_date": ["2023-11-24"], "v": [1]})
        self.assertEqual(pd.Timestamp("2023-11-24").day_name(), "Friday")

        calendar = AltDataLagEnforcer(friday, default_lag_days=3)
        business = AltDataLagEnforcer(friday, default_lag_days=3, lag_calendar="business")

        self.assertEqual(len(calendar.get_point_in_time_data("2023-11-27")), 1)
        self.assertEqual(len(business.get_point_in_time_data("2023-11-27")), 0)
        self.assertEqual(len(business.get_point_in_time_data("2023-11-28")), 0)
        self.assertEqual(len(business.get_point_in_time_data("2023-11-29")), 1)

    def test_business_day_lag_ignores_holidays_unless_supplied(self):
        """pandas business-day offsets skip weekends but not holidays without a list."""
        # Wed 2023-11-22, +2 business days. Thanksgiving Thu 2023-11-23 is a weekday.
        event = pd.DataFrame({"event_date": ["2023-11-22"], "v": [1]})
        naive = AltDataLagEnforcer(event, default_lag_days=2, lag_calendar="business")
        self.assertEqual(len(naive.get_point_in_time_data("2023-11-24")), 1)

        aware = AltDataLagEnforcer(
            event, default_lag_days=2, lag_calendar="business", holidays=["2023-11-23"]
        )
        self.assertEqual(len(aware.get_point_in_time_data("2023-11-24")), 0)
        self.assertEqual(len(aware.get_point_in_time_data("2023-11-27")), 1)

    def test_weekend_event_rolls_forward_before_counting_business_days(self):
        """Sat 2023-11-25 + 3 business days is Thu 2023-11-30, not Wed 2023-11-29.

        A weekend event is rolled forward to the next business day before the lag is
        counted, because the vendor's pipeline does not run until Monday. pandas'
        CustomBusinessDay rolls backward and would publish a day earlier; for a
        look-ahead guard the later reading is the safe one.
        """
        saturday = pd.DataFrame({"event_date": ["2023-11-25"], "v": [1]})
        self.assertEqual(pd.Timestamp("2023-11-25").day_name(), "Saturday")

        enforcer = AltDataLagEnforcer(saturday, default_lag_days=3, lag_calendar="business")
        self.assertEqual(len(enforcer.get_point_in_time_data("2023-11-29")), 0)
        self.assertEqual(len(enforcer.get_point_in_time_data("2023-11-30")), 1)

    def test_invalid_lag_calendar_is_rejected(self):
        with self.assertRaises(AltDataLagError):
            AltDataLagEnforcer(self.df_no_pub, lag_calendar="weekly")

    # ------------------------------------------------- intraday availability (regression)

    def test_midnight_publication_can_be_pinned_to_a_delivery_time(self):
        """Regression: a bare date asserted the file existed at 00:00 that day.

        With availability_time set, a 09:30 backtest decision no longer sees a file the
        vendor does not deliver until 18:00.
        """
        friday = pd.DataFrame({"event_date": ["2023-11-24"], "v": [1]})

        midnight = AltDataLagEnforcer(friday, default_lag_days=3)
        self.assertEqual(len(midnight.get_point_in_time_data("2023-11-27 09:30")), 1)

        evening = AltDataLagEnforcer(
            friday, default_lag_days=3, availability_time=dt.time(18, 0)
        )
        self.assertEqual(len(evening.get_point_in_time_data("2023-11-27 09:30")), 0)
        self.assertEqual(len(evening.get_point_in_time_data("2023-11-27 18:00")), 1)

    def test_availability_time_leaves_intraday_stamps_alone(self):
        """A vendor stamp that already carries a time is real precision, not a date."""
        df = pd.DataFrame({
            "event_date": ["2023-11-24"], "pub_date": ["2023-11-27 06:15"], "v": [1],
        })
        enforcer = AltDataLagEnforcer(
            df, publication_date_col="pub_date", availability_time=dt.time(18, 0)
        )
        self.assertEqual(len(enforcer.get_point_in_time_data("2023-11-27 07:00")), 1)

    # --------------------------------------------------------------- auditability

    def test_effective_publication_date_can_be_returned(self):
        enforcer = AltDataLagEnforcer(self.df_no_pub, default_lag_days=3)
        out = enforcer.get_point_in_time_data("2023-11-30", include_effective_date=True)
        self.assertIn(EFFECTIVE_PUBLICATION_COL, out.columns)
        self.assertEqual(out.iloc[0][EFFECTIVE_PUBLICATION_COL], pd.Timestamp("2023-11-27"))

    def test_lag_audit_separates_evidence_from_assumption(self):
        mixed = pd.DataFrame({
            "event_date": ["2023-11-24", "2023-11-25"],
            "pub_date": ["2023-11-27", None],
            "v": [1, 2],
        })
        audit = AltDataLagEnforcer(mixed, publication_date_col="pub_date").lag_audit()
        self.assertEqual(audit["total_rows"], 2)
        self.assertEqual(audit["explicit_publication_rows"], 1)
        self.assertEqual(audit["fallback_rows"], 1)
        self.assertEqual(audit["lag_calendar"], "calendar")

        all_fallback = AltDataLagEnforcer(self.df_no_pub).lag_audit()
        self.assertEqual(all_fallback["fallback_rows"], 3)
        self.assertEqual(all_fallback["explicit_publication_rows"], 0)

    # --------------------------------------------------------------- input validation

    def test_missing_event_date_column_raises_clearly(self):
        with self.assertRaises(AltDataLagError):
            AltDataLagEnforcer(pd.DataFrame({"date": ["2023-11-24"], "v": [1]}))

    def test_timezone_mismatch_raises_actionable_error(self):
        enforcer = AltDataLagEnforcer(self.df_no_pub)
        with self.assertRaises(AltDataLagError) as ctx:
            enforcer.get_point_in_time_data(pd.Timestamp("2023-11-29", tz="UTC"))
        self.assertIn("timezone-naive", str(ctx.exception))

    def test_timezone_aware_dataset_works_end_to_end(self):
        df = pd.DataFrame({
            "event_date": pd.to_datetime(["2023-11-24"]).tz_localize("UTC"), "v": [1],
        })
        enforcer = AltDataLagEnforcer(df, default_lag_days=3)
        self.assertEqual(len(enforcer.get_point_in_time_data(pd.Timestamp("2023-11-27", tz="UTC"))), 1)
        with self.assertRaises(AltDataLagError):
            enforcer.get_point_in_time_data("2023-11-27")

    def test_unparseable_as_of_date_raises(self):
        enforcer = AltDataLagEnforcer(self.df_no_pub)
        with self.assertRaises(AltDataLagError):
            enforcer.get_point_in_time_data("not-a-date")

    def test_bad_revision_key_column_raises(self):
        with self.assertRaises(AltDataLagError):
            AltDataLagEnforcer(self.df_no_pub, revision_key_cols=["nope"])

    def test_empty_frame_is_handled(self):
        empty = pd.DataFrame({"event_date": pd.Series([], dtype="datetime64[ns]"), "v": []})
        enforcer = AltDataLagEnforcer(empty)
        self.assertEqual(len(enforcer.get_point_in_time_data("2023-11-30")), 0)
        self.assertEqual(enforcer.lag_audit()["total_rows"], 0)


if __name__ == '__main__':
    unittest.main()
