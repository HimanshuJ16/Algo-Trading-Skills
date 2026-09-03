"""Unit tests for multi-timeframe-backtest-consistency-checks.

Expected values are derived by hand in the test body wherever a number is
asserted. A test that recomputes the implementation's own formula and compares
it to itself verifies nothing.

Several tests are labelled REGRESSION: each one fails against an earlier
implementation and passes against the current one. They are the executable
record of the defects listed in `references/standards.md`.
"""
import logging
import math
import unittest

from timeframe_consistency import (
    ANCHOR_EPOCH,
    ANCHOR_SESSION,
    Bar,
    InsufficientDataError,
    TimeframeConsistencyChecker,
)

MINUTE = 60


def one_minute_bars(count, start_ts=0, base=100.0, step=0.1, volume=1000.0):
    """1-minute bars whose close is ``base + step * i``, open == close."""
    bars = []
    for i in range(count):
        price = base + step * i
        bars.append(
            Bar(
                timestamp=start_ts + i * MINUTE,
                open=price,
                high=price + 0.5,
                low=price - 0.5,
                close=price,
                volume=volume,
            )
        )
    return bars


def expected_five_minute_bars(count_groups, base=100.0, step=0.1, volume=1000.0):
    """Correct 5-minute aggregation of :func:`one_minute_bars`, derived by hand.

    Group j spans 1-minute bars 5j..5j+4, so:
        open   = base + step*(5j)        (first bar's open)
        close  = base + step*(5j+4)      (last bar's close)
        high   = close + 0.5             (prices rise, so the last bar is highest)
        low    = open - 0.5              (the first bar is lowest)
        volume = 5 * per-bar volume
    """
    bars = []
    for j in range(count_groups):
        first = base + step * (5 * j)
        last = base + step * (5 * j + 4)
        bars.append(
            Bar(
                timestamp=j * 5 * MINUTE,
                open=first,
                high=last + 0.5,
                low=first - 0.5,
                close=last,
                volume=5 * volume,
            )
        )
    return bars


class TestBarValidation(unittest.TestCase):
    def test_rejects_non_finite_prices(self):
        for bad in (float("nan"), float("inf"), float("-inf")):
            with self.assertRaises(ValueError):
                Bar(0, 10.0, 12.0, 9.0, bad, 100.0)

    def test_rejects_non_finite_volume(self):
        with self.assertRaises(ValueError):
            Bar(0, 10.0, 12.0, 9.0, 11.0, float("nan"))

    def test_rejects_negative_volume(self):
        with self.assertRaises(ValueError):
            Bar(0, 10.0, 12.0, 9.0, 11.0, -1.0)

    def test_rejects_high_below_low(self):
        with self.assertRaises(ValueError):
            Bar(0, 10.0, 9.0, 12.0, 11.0, 100.0)

    def test_rejects_close_outside_high_low(self):
        with self.assertRaises(ValueError):
            Bar(0, 10.0, 12.0, 9.0, 13.0, 100.0)

    def test_allows_negative_prices(self):
        """WTI settled negative in April 2020; a price floor would reject real data."""
        bar = Bar(0, -5.0, -1.0, -40.0, -37.0, 100.0)
        self.assertEqual(bar.close, -37.0)

    def test_allows_flat_bar(self):
        bar = Bar(0, 10.0, 10.0, 10.0, 10.0, 0.0)
        self.assertEqual(bar.high, bar.low)


class TestResampling(unittest.TestCase):
    def setUp(self):
        self.checker = TimeframeConsistencyChecker()

    def test_aggregates_ohlcv_correctly(self):
        bars = [
            Bar(0, 10.0, 12.0, 9.0, 11.0, 100.0),
            Bar(60, 11.0, 15.0, 10.0, 14.0, 200.0),
            Bar(120, 14.0, 16.0, 13.0, 15.0, 150.0),
        ]
        result = self.checker.resample_bars(bars, factor=3, bar_interval_seconds=MINUTE)
        self.assertEqual(len(result.bars), 1)
        aggregated = result.bars[0]
        self.assertEqual(aggregated.open, 10.0)     # first bar's open
        self.assertEqual(aggregated.high, 16.0)     # max(12, 15, 16)
        self.assertEqual(aggregated.low, 9.0)       # min(9, 10, 13)
        self.assertEqual(aggregated.close, 15.0)    # last bar's close
        self.assertEqual(aggregated.volume, 450.0)  # 100 + 200 + 150
        self.assertEqual(result.incomplete_buckets, 0)

    def test_bucket_is_left_labelled_at_opening_time(self):
        bars = one_minute_bars(10)
        result = self.checker.resample_bars(bars, factor=5, bar_interval_seconds=MINUTE)
        self.assertEqual([b.timestamp for b in result.bars], [0, 300])

    def test_regression_gap_does_not_shift_later_buckets(self):
        """REGRESSION: positional chunking shifts every bucket after a gap.

        Bars 0..9 minus the 09:03 bar (index 3) leaves 9 bars. Chunking by list
        position puts bars 0,1,2,4,5 in the first group and labels the second
        group with the 09:06 bar, so the 5-minute grid slips permanently. Wall-
        clock bucketing keeps the boundaries at 0 and 300 regardless.
        """
        bars = [b for b in one_minute_bars(10) if b.timestamp != 180]
        self.assertEqual(len(bars), 9)
        result = self.checker.resample_bars(bars, factor=5, bar_interval_seconds=MINUTE)

        self.assertEqual([b.timestamp for b in result.bars], [0, 300])
        # The gapped bucket holds 4 of 5 bars and is reported as incomplete.
        self.assertEqual(result.incomplete_buckets, 1)
        self.assertEqual(result.complete_buckets, 1)
        # Bucket 0 now closes on the 09:04 bar (close 100.4), unchanged by the gap.
        self.assertAlmostEqual(result.bars[0].close, 100.4)
        # Bucket 300 is untouched: opens at bar 5, closes at bar 9.
        self.assertAlmostEqual(result.bars[1].open, 100.5)
        self.assertAlmostEqual(result.bars[1].close, 100.9)

    def test_regression_series_starting_mid_bucket_snaps_to_grid(self):
        """REGRESSION: a series that starts off-grid must not define the grid.

        Starting at 00:02 with 5-minute buckets, the first bar belongs to the
        00:00 bucket. Positional chunking would instead open a bucket at 00:02
        and straddle the 00:05 boundary.
        """
        bars = one_minute_bars(8, start_ts=120)  # 00:02 .. 00:09
        result = self.checker.resample_bars(
            bars, factor=5, bar_interval_seconds=MINUTE, drop_incomplete_final=False
        )
        self.assertEqual([b.timestamp for b in result.bars], [0, 300])
        # 00:02, 00:03, 00:04 -> 3 bars in the first bucket; 00:05..00:09 -> 5.
        self.assertEqual(result.incomplete_buckets, 1)

    def test_session_anchor_differs_from_epoch_anchor(self):
        bars = one_minute_bars(8, start_ts=120)
        epoch = self.checker.resample_bars(
            bars, factor=5, bar_interval_seconds=MINUTE,
            anchor=ANCHOR_EPOCH, drop_incomplete_final=False,
        )
        session = self.checker.resample_bars(
            bars, factor=5, bar_interval_seconds=MINUTE,
            anchor=ANCHOR_SESSION, drop_incomplete_final=False,
        )
        self.assertEqual([b.timestamp for b in epoch.bars], [0, 300])
        self.assertEqual([b.timestamp for b in session.bars], [120, 420])

    def test_nse_thirty_minute_bucket_splits_the_opening_half_hour(self):
        """The documented NSE case: 09:15 IST is off the 30-minute epoch grid.

        09:15 IST == 03:45 UTC == 13500 s after UTC midnight, and
        13500 / 1800 = 7.5, so the epoch-anchored 30-minute bucket opens at
        03:30 UTC (09:00 IST) and holds only the 15 bars from 09:15 to 09:29.
        """
        day = 20000 * 86400  # an arbitrary whole UTC day
        session_open = day + 13500
        bars = one_minute_bars(60, start_ts=session_open)  # 09:15 .. 10:14 IST
        result = self.checker.resample_bars(
            bars, factor=30, bar_interval_seconds=MINUTE, anchor=ANCHOR_EPOCH
        )
        self.assertEqual(result.bars[0].timestamp, day + 12600)  # 03:30 UTC
        self.assertEqual(result.incomplete_buckets, 1)

        # Anchoring to the session start instead gives whole 09:15-09:45 buckets.
        session = self.checker.resample_bars(
            bars, factor=30, bar_interval_seconds=MINUTE, anchor=ANCHOR_SESSION
        )
        self.assertEqual([b.timestamp for b in session.bars], [session_open, session_open + 1800])
        self.assertEqual(session.incomplete_buckets, 0)

    def test_regression_trailing_partial_bucket_is_dropped(self):
        """REGRESSION: a still-forming bucket must not be emitted as a final bar."""
        bars = one_minute_bars(7)  # one full bucket + 2 bars of the next
        result = self.checker.resample_bars(bars, factor=5, bar_interval_seconds=MINUTE)
        self.assertEqual(len(result.bars), 1)
        self.assertTrue(result.dropped_incomplete_final)

    def test_trailing_partial_bucket_kept_when_explicitly_requested(self):
        bars = one_minute_bars(7)
        result = self.checker.resample_bars(
            bars, factor=5, bar_interval_seconds=MINUTE, drop_incomplete_final=False
        )
        self.assertEqual(len(result.bars), 2)
        self.assertFalse(result.dropped_incomplete_final)
        self.assertEqual(result.incomplete_buckets, 1)

    def test_incomplete_bucket_emits_warning(self):
        bars = [b for b in one_minute_bars(10) if b.timestamp != 180]
        with self.assertLogs("timeframe_consistency", level=logging.WARNING) as captured:
            self.checker.resample_bars(bars, factor=5, bar_interval_seconds=MINUTE)
        self.assertIn("fewer than", captured.output[0])


class TestResamplingValidation(unittest.TestCase):
    def setUp(self):
        self.checker = TimeframeConsistencyChecker()
        self.bars = one_minute_bars(10)

    def test_rejects_non_positive_factor(self):
        for bad in (0, -1):
            with self.assertRaises(ValueError):
                self.checker.resample_bars(self.bars, bad, MINUTE)

    def test_rejects_non_positive_interval(self):
        with self.assertRaises(ValueError):
            self.checker.resample_bars(self.bars, 5, 0)

    def test_rejects_unknown_anchor(self):
        with self.assertRaises(ValueError):
            self.checker.resample_bars(self.bars, 5, MINUTE, anchor="midnight")

    def test_rejects_empty_series(self):
        with self.assertRaises(InsufficientDataError):
            self.checker.resample_bars([], 5, MINUTE)

    def test_rejects_duplicate_timestamps(self):
        bars = self.bars + [self.bars[-1]]
        with self.assertRaises(ValueError):
            self.checker.resample_bars(bars, 5, MINUTE)

    def test_rejects_out_of_order_timestamps(self):
        bars = list(self.bars)
        bars[3], bars[4] = bars[4], bars[3]
        with self.assertRaises(ValueError):
            self.checker.resample_bars(bars, 5, MINUTE)

    def test_rejects_gap_that_is_not_a_multiple_of_the_interval(self):
        """A 90-second gap means the declared 60-second interval is wrong."""
        bars = one_minute_bars(3) + [Bar(210, 10.0, 10.5, 9.5, 10.0, 100.0)]
        with self.assertRaises(ValueError):
            self.checker.resample_bars(bars, 5, MINUTE)

    def test_rejects_non_integer_factor(self):
        with self.assertRaises(TypeError):
            self.checker.resample_bars(self.bars, 5.0, MINUTE)

    def test_rejects_negative_tolerance(self):
        with self.assertRaises(ValueError):
            TimeframeConsistencyChecker(divergence_tolerance_pct=-1.0)

    def test_rejects_non_finite_tolerance(self):
        with self.assertRaises(ValueError):
            TimeframeConsistencyChecker(divergence_tolerance_pct=float("nan"))


class TestComputeSma(unittest.TestCase):
    def setUp(self):
        self.checker = TimeframeConsistencyChecker()

    def test_sma_values_and_labels(self):
        """Hand-derived: closes 10, 20, 30, 40; period 3."""
        bars = [Bar(i * MINUTE, c, c + 1, c - 1, c, 1.0)
                for i, c in enumerate((10.0, 20.0, 30.0, 40.0))]
        self.assertEqual(
            self.checker.compute_sma(bars, 3),
            [(120, 20.0), (180, 30.0)],  # (10+20+30)/3, (20+30+40)/3
        )

    def test_sma_is_labelled_with_the_bar_that_completes_the_window(self):
        bars = one_minute_bars(5)
        first_ts = self.checker.compute_sma(bars, 3)[0][0]
        self.assertEqual(first_ts, 120)  # the 3rd bar, not the 1st

    def test_regression_sma_is_not_rounded(self):
        """REGRESSION: rounding to 6dp injected error near the tolerance itself.

        Mean of 1/3 and 2/3 is exactly 0.5, but a series that does not resolve
        cleanly must retain full precision. 1e-7 differences must survive.
        """
        bars = [Bar(i * MINUTE, 1.0, 1.0 + 1e-7, 1.0, 1.0 + (1e-7 if i else 0.0), 1.0)
                for i in range(2)]
        (_, sma), = self.checker.compute_sma(bars, 2)
        self.assertNotEqual(sma, 1.0)
        self.assertAlmostEqual(sma, 1.0 + 5e-8, places=12)

    def test_rejects_non_positive_period(self):
        bars = one_minute_bars(10)
        for bad in (0, -3):
            with self.assertRaises(ValueError):
                self.checker.compute_sma(bars, bad)


class TestResamplingIntegrity(unittest.TestCase):
    def setUp(self):
        self.checker = TimeframeConsistencyChecker()
        self.high_res = one_minute_bars(50)
        self.reference = expected_five_minute_bars(10)

    def test_matching_provenances_pass_exactly(self):
        report = self.checker.check_resampling_integrity(
            self.high_res, self.reference, factor=5, bar_interval_seconds=MINUTE
        )
        self.assertTrue(report.is_consistent)
        self.assertEqual(report.compared_buckets, 10)
        self.assertEqual(report.mismatched_buckets, 0)
        self.assertEqual(report.field_mismatches, {})

    def test_detects_volume_double_counting(self):
        """The documented 'volume double-counting' pitfall."""
        bad = list(self.reference)
        bad[3] = Bar(bad[3].timestamp, bad[3].open, bad[3].high, bad[3].low,
                     bad[3].close, bad[3].volume * 2)
        report = self.checker.check_resampling_integrity(
            self.high_res, bad, factor=5, bar_interval_seconds=MINUTE
        )
        self.assertFalse(report.is_consistent)
        self.assertEqual(report.field_mismatches, {"volume": 1})
        self.assertEqual(report.first_mismatch_timestamp, 900)

    def test_detects_boundary_misalignment_in_the_reference(self):
        """A reference built one bar late differs on every price field."""
        shifted = []
        for j in range(9):
            first = 100.0 + 0.1 * (5 * j + 1)   # opens one bar late
            last = 100.0 + 0.1 * (5 * j + 5)
            shifted.append(Bar(j * 300, first, last + 0.5, first - 0.5, last, 5000.0))
        report = self.checker.check_resampling_integrity(
            self.high_res, shifted, factor=5, bar_interval_seconds=MINUTE
        )
        self.assertFalse(report.is_consistent)
        self.assertEqual(report.mismatched_buckets, 9)
        self.assertEqual(report.first_mismatch_timestamp, 0)

    def test_reports_buckets_missing_from_the_reference(self):
        report = self.checker.check_resampling_integrity(
            self.high_res, self.reference[:-2], factor=5, bar_interval_seconds=MINUTE
        )
        self.assertFalse(report.is_consistent)
        self.assertEqual(report.missing_in_reference, 2)

    def test_raises_when_no_buckets_overlap(self):
        disjoint = [Bar(10 ** 9 + j * 300, 1.0, 1.0, 1.0, 1.0, 1.0) for j in range(5)]
        with self.assertRaises(InsufficientDataError):
            self.checker.check_resampling_integrity(
                self.high_res, disjoint, factor=5, bar_interval_seconds=MINUTE
            )


class TestConsistency(unittest.TestCase):
    def setUp(self):
        self.checker = TimeframeConsistencyChecker()
        self.high_res = one_minute_bars(50)
        self.reference = expected_five_minute_bars(10)

    def test_matching_provenances_diverge_by_zero(self):
        report = self.checker.check_consistency(
            self.high_res, self.reference, factor=5, sma_period=3, bar_interval_seconds=MINUTE
        )
        self.assertTrue(report.is_consistent)
        self.assertEqual(report.max_divergence_pct, 0.0)
        self.assertEqual(report.max_absolute_divergence, 0.0)
        # 10 low-res bars, 3-period SMA -> 8 points.
        self.assertEqual(report.matched_signals, 8)

    def test_regression_one_bar_boundary_error_is_detected(self):
        """REGRESSION: the exact defect the old 1.0% tolerance let through.

        A reference whose closes come from bar 5j+3 instead of 5j+4 is wrong by
        exactly 0.1 in price. Against a price near 100 that is ~0.099% -- inside
        the old 1.0% threshold published in standards.md, so the old checker
        reported PASSED on a genuinely broken series.
        """
        wrong = []
        for j in range(10):
            first = 100.0 + 0.1 * (5 * j)
            last = 100.0 + 0.1 * (5 * j + 3)      # one bar early
            wrong.append(Bar(j * 300, first, last + 0.6, first - 0.5, last, 5000.0))

        report = self.checker.check_consistency(
            self.high_res, wrong, factor=5, sma_period=3, bar_interval_seconds=MINUTE
        )
        self.assertFalse(report.is_consistent)
        self.assertAlmostEqual(report.max_absolute_divergence, 0.1, places=9)
        # Confirm the historical blind spot: the divergence really is under 1%.
        self.assertLess(report.max_divergence_pct, 1.0)
        lenient = TimeframeConsistencyChecker(divergence_tolerance_pct=1.0)
        self.assertTrue(
            lenient.check_consistency(
                self.high_res, wrong, factor=5, sma_period=3, bar_interval_seconds=MINUTE
            ).is_consistent,
            "documents an earlier false pass; the default tolerance must not allow it",
        )

    def test_regression_insufficient_history_raises_instead_of_passing(self):
        """REGRESSION: zero comparisons used to report 'PASSED'."""
        with self.assertRaises(InsufficientDataError):
            self.checker.check_consistency(
                one_minute_bars(20), expected_five_minute_bars(2),
                factor=5, sma_period=50, bar_interval_seconds=MINUTE,
            )

    def test_regression_empty_input_raises_instead_of_passing(self):
        with self.assertRaises(InsufficientDataError):
            self.checker.check_consistency(
                [], self.reference, factor=5, sma_period=3, bar_interval_seconds=MINUTE
            )

    def test_min_comparisons_guard_is_enforced(self):
        strict = TimeframeConsistencyChecker(min_comparisons=20)
        with self.assertRaises(InsufficientDataError):
            strict.check_consistency(
                self.high_res, self.reference,
                factor=5, sma_period=3, bar_interval_seconds=MINUTE,
            )

    def test_divergence_verdict_is_independent_of_price_level(self):
        """REGRESSION: the old check's verdict flipped with the price level.

        The same series shape at base 100 and base 10, both correctly resampled,
        must both pass. an earlier comparison reported 0.60% at base 100 and
        5.56% at base 10, so a 1% threshold passed one and failed the other.
        """
        for base in (100.0, 10.0):
            high_res = one_minute_bars(50, base=base)
            reference = expected_five_minute_bars(10, base=base)
            report = self.checker.check_consistency(
                high_res, reference, factor=5, sma_period=3, bar_interval_seconds=MINUTE
            )
            self.assertTrue(report.is_consistent, f"base={base}")
            self.assertEqual(report.max_divergence_pct, 0.0, f"base={base}")

    def test_worst_timestamp_points_at_the_offending_bucket(self):
        wrong = list(self.reference)
        target = wrong[7]
        wrong[7] = Bar(target.timestamp, target.open, target.high + 5.0,
                       target.low, target.close + 5.0, target.volume)
        report = self.checker.check_consistency(
            self.high_res, wrong, factor=5, sma_period=1, bar_interval_seconds=MINUTE
        )
        self.assertFalse(report.is_consistent)
        self.assertEqual(report.worst_timestamp, target.timestamp)

    def test_zero_reference_value_is_not_silently_dropped(self):
        """A zero reference used to be skipped, hiding a total mismatch."""
        high_res = [Bar(i * MINUTE, 0.0, 1.0, -1.0, 1.0 if i % 5 == 4 else 0.0, 1.0)
                    for i in range(10)]
        reference = [Bar(j * 300, 0.0, 1.0, -1.0, 0.0, 5.0) for j in range(2)]
        report = self.checker.check_consistency(
            high_res, reference, factor=5, sma_period=1, bar_interval_seconds=MINUTE
        )
        self.assertFalse(report.is_consistent)
        self.assertTrue(math.isinf(report.max_divergence_pct))
        self.assertEqual(report.max_absolute_divergence, 1.0)

    def test_rejects_non_positive_sma_period(self):
        for bad in (0, -3):
            with self.assertRaises(ValueError):
                self.checker.check_consistency(
                    self.high_res, self.reference,
                    factor=5, sma_period=bad, bar_interval_seconds=MINUTE,
                )


if __name__ == "__main__":
    unittest.main()
