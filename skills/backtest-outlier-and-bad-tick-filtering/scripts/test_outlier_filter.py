"""Unit tests for backtest-outlier-and-bad-tick-filtering."""
import math
import unittest

from outlier_filter import OutlierBadTickFilter, OutlierFilterError


class TestOutlierBadTickFilter(unittest.TestCase):
    def setUp(self):
        self.filter = OutlierBadTickFilter(window_size=10, z_threshold=5.0, max_single_tick_jump_pct=20.0)

    # ----------------------------------------------------------------- baseline

    def test_purges_single_bad_tick_print(self):
        # 100.0 price series with a single 10.0 bad print
        prices = [100.0 + (i * 0.1) for i in range(15)]
        prices.insert(8, 10.0)  # Bad print at index 8

        cleaned, report = self.filter.filter_prices(prices)

        self.assertEqual(report.purged_bad_ticks_count, 1)
        self.assertNotIn(10.0, cleaned)
        self.assertEqual(report.total_input_ticks, 16)
        self.assertEqual(report.purged_indices, [8])

    def test_purges_zero_and_negative_prices(self):
        prices = [100.0, 0.0, -5.0, 101.0]
        cleaned, report = self.filter.filter_prices(prices)
        self.assertEqual(cleaned, [100.0, 101.0])
        self.assertEqual(report.purged_bad_ticks_count, 2)
        self.assertEqual(report.non_positive_count, 2)

    def test_empty_series(self):
        cleaned, report = self.filter.filter_prices([])
        self.assertEqual(cleaned, [])
        self.assertEqual(report.total_input_ticks, 0)
        self.assertEqual(report.cleanliness_pct, 100.0)

    def test_report_counts_are_self_consistent(self):
        prices = [100.0, float("nan"), 0.0, 101.0, 10.0, 101.5]
        cleaned, report = self.filter.filter_prices(prices)
        self.assertEqual(report.cleaned_ticks_count, len(cleaned))
        self.assertEqual(
            report.cleaned_ticks_count + report.purged_bad_ticks_count, report.total_input_ticks
        )
        self.assertEqual(len(report.kept_indices), report.cleaned_ticks_count)
        self.assertEqual(
            sorted(report.kept_indices + report.purged_indices), list(range(len(prices)))
        )

    # -------------------------------------------------- non-finite values (regression)

    def test_purges_nan_prices(self):
        """Regression: NaN passes every comparison, so it used to enter the clean series.

        `nan <= 0` is False and `abs(nan - prev)/prev > pct` is False, so the old
        implementation accepted NaN and then corrupted every subsequent median.
        """
        prices = [100.0, 100.1, float("nan"), 100.2, 100.3]
        cleaned, report = self.filter.filter_prices(prices)
        self.assertFalse(any(math.isnan(p) for p in cleaned))
        self.assertEqual(report.non_finite_count, 1)
        self.assertEqual(report.purged_indices, [2])

    def test_purges_infinite_prices_including_leading(self):
        """Regression: an infinite first tick used to be accepted as the jump anchor."""
        prices = [float("inf"), 100.0, 100.1, 100.2]
        cleaned, report = self.filter.filter_prices(prices)
        self.assertEqual(cleaned, [100.0, 100.1, 100.2])
        self.assertEqual(report.non_finite_count, 1)

        cleaned_neg, report_neg = self.filter.filter_prices([100.0, float("-inf"), 100.1])
        self.assertEqual(cleaned_neg, [100.0, 100.1])
        self.assertEqual(report_neg.non_finite_count, 1)

    # ------------------------------------------------ regime change / data preservation

    def test_recovers_from_genuine_regime_gap_without_losing_ticks(self):
        """A confirmed level shift restores the prints purged on the way into it.

        The previous implementation left the first `max_consecutive_drops - 1` ticks
        of every genuine gap permanently deleted, silently destroying real data.
        """
        prices = [100.0] * 10 + [50.0] * 5
        cleaned, report = self.filter.filter_prices(prices)

        self.assertEqual(report.purged_bad_ticks_count, 0)
        self.assertEqual(report.regime_changes_detected, 1)
        self.assertEqual(cleaned, prices)
        self.assertEqual(report.kept_indices, list(range(15)))

    def test_streaming_mode_preserves_causal_behaviour(self):
        """With restoration disabled the pass is strictly causal, dropping those ticks."""
        streaming = OutlierBadTickFilter(
            window_size=10, max_single_tick_jump_pct=20.0, restore_ticks_on_regime_change=False
        )
        cleaned, report = streaming.filter_prices([100.0] * 10 + [50.0] * 5)
        self.assertEqual(report.purged_bad_ticks_count, 2)
        self.assertEqual(report.purged_indices, [10, 11])
        self.assertEqual(cleaned[-3:], [50.0, 50.0, 50.0])

    def test_leading_bad_tick_does_not_destroy_real_ticks(self):
        """Regression: a bad first tick used to anchor the jump test and eat real data.

        Old output for this input was [10.0, 100.2, ...] — the bad print survived and
        two genuine prices were deleted. The anchor still survives (a trailing filter
        has no information before the file starts), but no real tick is lost.
        """
        prices = [10.0] + [100.0 + i * 0.1 for i in range(10)]
        cleaned, report = self.filter.filter_prices(prices)

        self.assertEqual(report.purged_bad_ticks_count, 0)
        self.assertEqual(cleaned, prices)
        self.assertEqual(report.regime_changes_detected, 1)

    def test_unconfirmed_run_at_end_of_series_stays_purged(self):
        """A run that never reaches max_consecutive_drops cannot be confirmed."""
        prices = [100.0] * 10 + [50.0, 50.0]
        cleaned, report = self.filter.filter_prices(prices)
        self.assertEqual(report.purged_indices, [10, 11])
        self.assertEqual(report.regime_changes_detected, 0)

    def test_one_gap_produces_one_regime_change_without_post_gap_churn(self):
        """Regression: the MAD window must restart at the new level after a shift.

        Otherwise the window stays contaminated by pre-shift prices for `window_size`
        more ticks and keeps re-flagging good prints. In streaming mode, where those
        flags are not restored, that churn deleted a long run of genuine data.
        """
        prices = [100.0] * 30 + [92.0 + i * 0.01 for i in range(30)]
        streaming = OutlierBadTickFilter(
            window_size=21,
            max_single_tick_jump_pct=5.0,
            min_deviation=0.01,
            restore_ticks_on_regime_change=False,
        )
        _, report = streaming.filter_prices(prices)

        self.assertEqual(report.regime_changes_detected, 1)
        # Only the two unconfirmable ticks at the shift itself may be lost.
        self.assertEqual(report.purged_indices, [30, 31])

        restoring = OutlierBadTickFilter(
            window_size=21, max_single_tick_jump_pct=5.0, min_deviation=0.01
        )
        cleaned, restoring_report = restoring.filter_prices(prices)
        self.assertEqual(restoring_report.purged_bad_ticks_count, 0)
        self.assertEqual(cleaned, prices)

    def test_isolated_bad_print_is_not_treated_as_regime_change(self):
        prices = [100.0] * 10 + [10.0] + [100.0] * 5
        cleaned, report = self.filter.filter_prices(prices)
        self.assertEqual(report.purged_indices, [10])
        self.assertEqual(report.regime_changes_detected, 0)

    # ------------------------------------------------------ MAD test, verified by hand

    def test_modified_z_score_boundary_matches_hand_computation(self):
        """Window [10,11,12,13,14]: median 12, MAD 1, so the limit is 5/0.6745 = 7.4129.

        Values are derived from the Iglewicz-Hoaglin definition independently of the
        implementation. The jump rule is disabled so only the MAD rule can fire.
        """
        base = [10.0, 11.0, 12.0, 13.0, 14.0]
        f = OutlierBadTickFilter(window_size=5, z_threshold=5.0, max_single_tick_jump_pct=1000.0)

        limit = 5.0 / 0.6745
        self.assertAlmostEqual(limit, 7.412898, places=5)

        just_over = 12.0 + 7.42
        just_under = 12.0 + 7.41

        _, over_report = f.filter_prices(base + [just_over])
        self.assertEqual(over_report.purged_indices, [5])

        _, under_report = f.filter_prices(base + [just_under])
        self.assertEqual(under_report.purged_indices, [])

    def test_mad_test_is_skipped_and_counted_when_mad_is_zero(self):
        """Flat window, no floor: the scale estimate degenerates and the test is inert."""
        f = OutlierBadTickFilter(window_size=5, max_single_tick_jump_pct=20.0)
        cleaned, report = f.filter_prices([100.0] * 10 + [105.0])
        # 5% move: below the jump threshold, and MAD is zero so nothing screens it.
        self.assertIn(105.0, cleaned)
        self.assertGreater(report.mad_test_skipped_count, 0)

    def test_min_deviation_floor_gives_the_test_power_in_flat_windows(self):
        """Regression: with a Brownlees-Gallo floor the same 5% print is caught."""
        f = OutlierBadTickFilter(window_size=5, max_single_tick_jump_pct=20.0, min_deviation=0.01)
        cleaned, report = f.filter_prices([100.0] * 10 + [105.0])
        self.assertNotIn(105.0, cleaned)
        self.assertEqual(report.purged_indices, [10])
        self.assertEqual(report.mad_test_skipped_count, 0)

    def test_min_deviation_floor_spares_a_one_tick_move(self):
        """The floor exists so quiet windows do not flag minimum price variations."""
        f = OutlierBadTickFilter(window_size=5, max_single_tick_jump_pct=20.0, min_deviation=0.05)
        cleaned, _ = f.filter_prices([100.0] * 10 + [100.01])
        self.assertIn(100.01, cleaned)

    # ------------------------------------------------------------ index realignment

    def test_kept_indices_realign_a_parallel_timestamp_array(self):
        timestamps = [f"t{i}" for i in range(6)]
        prices = [100.0, 100.1, 10.0, 100.2, float("nan"), 100.3]
        cleaned, report = self.filter.filter_prices(prices)
        aligned = [timestamps[i] for i in report.kept_indices]
        self.assertEqual(len(aligned), len(cleaned))
        self.assertEqual(aligned, ["t0", "t1", "t3", "t5"])

    # --------------------------------------------------------------- input validation

    def test_non_numeric_price_raises(self):
        with self.assertRaises(OutlierFilterError):
            self.filter.filter_prices([100.0, "oops", 101.0])
        with self.assertRaises(OutlierFilterError):
            self.filter.filter_prices([100.0, None, 101.0])
        with self.assertRaises(OutlierFilterError):
            self.filter.filter_prices([100.0, True, 101.0])

    def test_window_size_zero_is_rejected(self):
        """Regression: prices[-0:] is prices[0:], so 0 silently used the whole history."""
        with self.assertRaises(OutlierFilterError):
            OutlierBadTickFilter(window_size=0)
        with self.assertRaises(OutlierFilterError):
            OutlierBadTickFilter(window_size=2)
        with self.assertRaises(OutlierFilterError):
            OutlierBadTickFilter(window_size=-5)

    def test_max_consecutive_drops_below_two_is_rejected(self):
        """Regression: 1 made every outlier an instant regime change, purging nothing."""
        with self.assertRaises(OutlierFilterError):
            OutlierBadTickFilter(max_consecutive_drops=1)
        with self.assertRaises(OutlierFilterError):
            OutlierBadTickFilter(max_consecutive_drops=0)

    def test_invalid_thresholds_are_rejected(self):
        with self.assertRaises(OutlierFilterError):
            OutlierBadTickFilter(z_threshold=0)
        with self.assertRaises(OutlierFilterError):
            OutlierBadTickFilter(z_threshold=float("nan"))
        with self.assertRaises(OutlierFilterError):
            OutlierBadTickFilter(max_single_tick_jump_pct=-1.0)
        with self.assertRaises(OutlierFilterError):
            OutlierBadTickFilter(min_deviation=-0.01)

    def test_filter_does_not_mutate_input(self):
        prices = [100.0, 10.0, 100.1, 100.2]
        snapshot = list(prices)
        self.filter.filter_prices(prices)
        self.assertEqual(prices, snapshot)


if __name__ == "__main__":
    unittest.main()
