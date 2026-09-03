"""
Unit tests for walk-forward-optimization-window-management skill.

Tests:
1. Rolling window slice generation, exact slice count, and date boundary alignment.
2. Anchored expanding window slice generation.
3. Configuration validation (rejects the settings that used to hang or misgenerate).
4. Inclusive minimum date-range boundary.
5. Purge/embargo gap geometry and embargo-violation detection.
6. Temporal isolation validation and lookahead leakage detection.
7. Non-overlapping out-of-sample invariant across the slice sequence.
8. Walk-Forward Efficiency (WFE) calculation, undefined-denominator handling, thresholding.
"""
import datetime
import logging
import unittest
from walk_forward_manager import (
    WalkForwardError,
    WalkForwardWindowManager,
    WFEEvaluation,
    WindowMode,
    WindowSlice,
)


def setUpModule():
    # The helper logs a warning for every embargo_days=0 generation; keep test output readable.
    logging.getLogger("walk_forward_manager").setLevel(logging.CRITICAL)


class TestWalkForwardWindowManager(unittest.TestCase):

    def setUp(self):
        self.mgr = WalkForwardWindowManager(
            in_sample_days=365, out_of_sample_days=90, step_days=90, mode=WindowMode.ROLLING
        )
        self.start_date = datetime.date(2023, 1, 1)
        self.end_date = datetime.date(2026, 1, 1)

    # ------------------------------------------------------------------ geometry

    def test_rolling_window_generation(self):
        slices = self.mgr.generate_windows(self.start_date, self.end_date)

        # Independently derived: the span is inclusive of both endpoints, so day offsets run
        # 0..1096. Slice k ends at offset 90k + 364 + 90 = 90k + 454, which must be <= 1096,
        # so 90k <= 642 and k <= 7 -> exactly 8 slices.
        self.assertEqual(len(slices), 8)

        first_slice = slices[0]
        self.assertEqual(first_slice.is_start, datetime.date(2023, 1, 1))
        # IS end date should be is_start + 364 days = 2023-12-31
        self.assertEqual(first_slice.is_end, datetime.date(2023, 12, 31))
        # OOS start date should be 2024-01-01
        self.assertEqual(first_slice.oos_start, datetime.date(2024, 1, 1))
        # OOS end date should be oos_start + 89 days = 2024-03-30
        self.assertEqual(first_slice.oos_end, datetime.date(2024, 3, 30))
        # Default warmup_days=30 places the warm-up start 30 days before is_start.
        self.assertEqual(first_slice.warmup_start, datetime.date(2022, 12, 2))
        # No embargo requested, so the gap bounds stay unset.
        self.assertIsNone(first_slice.embargo_start)
        self.assertIsNone(first_slice.embargo_end)

        # Each in-sample window has exactly in_sample_days inclusive days and steps by step_days.
        for previous, current in zip(slices, slices[1:]):
            self.assertEqual((previous.is_end - previous.is_start).days + 1, 365)
            self.assertEqual((previous.oos_end - previous.oos_start).days + 1, 90)
            self.assertEqual((current.is_start - previous.is_start).days, 90)

    def test_anchored_window_generation(self):
        anchored_mgr = WalkForwardWindowManager(
            in_sample_days=365, out_of_sample_days=90, step_days=90, mode=WindowMode.ANCHORED
        )
        slices = anchored_mgr.generate_windows(self.start_date, self.end_date)
        self.assertEqual(len(slices), 8)

        # All anchored slices must have the same is_start date
        for s in slices:
            self.assertEqual(s.is_start, datetime.date(2023, 1, 1))
            self.assertEqual(s.warmup_start, datetime.date(2022, 12, 2))

        # ...and the in-sample window must expand by step_days each slice.
        for previous, current in zip(slices, slices[1:]):
            self.assertEqual((current.is_end - previous.is_end).days, 90)

    def test_minimum_range_boundary_is_inclusive(self):
        # 2023-01-01 .. 2024-03-30 is exactly 365 + 90 inclusive days and yields one slice.
        # Regression: the guard previously compared an exclusive day count and rejected this.
        slices = self.mgr.generate_windows(self.start_date, datetime.date(2024, 3, 30))
        self.assertEqual(len(slices), 1)
        self.assertEqual(slices[0].oos_end, datetime.date(2024, 3, 30))

        with self.assertRaises(WalkForwardError):
            self.mgr.generate_windows(self.start_date, datetime.date(2024, 3, 29))

    def test_min_required_days_accounts_for_embargo(self):
        self.assertEqual(self.mgr.min_required_days(), 455)
        embargoed = WalkForwardWindowManager(
            in_sample_days=365, out_of_sample_days=90, step_days=90, embargo_days=21
        )
        self.assertEqual(embargoed.min_required_days(), 476)

    # ------------------------------------------------------------- configuration

    def test_non_advancing_step_is_rejected(self):
        # Regression: step_days <= 0 never advanced the cursor, so generate_windows looped
        # forever appending slices instead of terminating.
        for bad_step in (0, -30):
            with self.assertRaises(WalkForwardError):
                WalkForwardWindowManager(step_days=bad_step)

    def test_invalid_configuration_rejected(self):
        for kwargs in (
            {"in_sample_days": 0},
            {"in_sample_days": -1},
            {"out_of_sample_days": 0},
            {"warmup_days": -1},
            {"embargo_days": -1},
            {"in_sample_days": 365.5},
            {"in_sample_days": True},
            {"mode": "ROLLING"},
            {"min_wfe_threshold": float("nan")},
            {"min_is_sharpe": float("inf")},
        ):
            with self.subTest(**kwargs):
                with self.assertRaises(WalkForwardError):
                    WalkForwardWindowManager(**kwargs)

    def test_date_argument_guards(self):
        # A datetime.datetime is a datetime.date subclass and would silently produce slice
        # bounds carrying a time component, so it is rejected explicitly.
        with self.assertRaises(WalkForwardError):
            self.mgr.generate_windows(datetime.datetime(2023, 1, 1), self.end_date)
        with self.assertRaises(WalkForwardError):
            self.mgr.generate_windows("2023-01-01", self.end_date)
        with self.assertRaises(WalkForwardError):
            self.mgr.generate_windows(self.end_date, self.start_date)

    # ------------------------------------------------------------------- embargo

    def test_embargo_gap_geometry(self):
        mgr = WalkForwardWindowManager(
            in_sample_days=365, out_of_sample_days=90, step_days=90, embargo_days=21
        )
        slices = mgr.generate_windows(self.start_date, self.end_date)
        # Offsets: slice k ends at 90k + 364 + 21 + 90 = 90k + 475 <= 1096 -> k <= 6 -> 7 slices.
        self.assertEqual(len(slices), 7)

        first = slices[0]
        self.assertEqual(first.is_end, datetime.date(2023, 12, 31))
        self.assertEqual(first.embargo_start, datetime.date(2024, 1, 1))
        self.assertEqual(first.embargo_end, datetime.date(2024, 1, 21))
        self.assertEqual(first.oos_start, datetime.date(2024, 1, 22))
        for s in slices:
            self.assertEqual((s.oos_start - s.is_end).days - 1, 21)

    def test_embargo_violation_detected(self):
        slices = self.mgr.generate_windows(self.start_date, self.end_date)
        # The default manager leaves a 0-day gap, which fails a 30-day embargo requirement.
        with self.assertRaises(WalkForwardError):
            WalkForwardWindowManager.validate_window_isolation(slices[0], min_embargo_days=30)
        # ...and passes when no embargo is required.
        WalkForwardWindowManager.validate_window_isolation(slices[0], min_embargo_days=0)

    def test_zero_embargo_emits_leakage_warning(self):
        with self.assertLogs("walk_forward_manager", level="WARNING") as captured:
            self.mgr.generate_windows(self.start_date, self.end_date)
        self.assertTrue(any("embargo_days=0" in line for line in captured.output))

    # ------------------------------------------------------- isolation validation

    def test_lookahead_leakage_detection(self):
        invalid_slice = WindowSlice(
            index=0,
            warmup_start=datetime.date(2023, 1, 1),
            is_start=datetime.date(2023, 1, 1),
            is_end=datetime.date(2023, 6, 30),
            oos_start=datetime.date(2023, 6, 25),  # Overlap! OOS start <= IS end
            oos_end=datetime.date(2023, 9, 30),
        )

        with self.assertRaises(WalkForwardError):
            WalkForwardWindowManager.validate_window_isolation(invalid_slice)

    def test_adjacent_windows_are_the_isolation_boundary(self):
        # oos_start == is_end is leakage; oos_start == is_end + 1 day is the tightest legal slice.
        base = dict(
            index=0,
            warmup_start=datetime.date(2022, 12, 1),
            is_start=datetime.date(2023, 1, 1),
            is_end=datetime.date(2023, 6, 30),
            oos_end=datetime.date(2023, 9, 30),
        )
        with self.assertRaises(WalkForwardError):
            WalkForwardWindowManager.validate_window_isolation(
                WindowSlice(oos_start=datetime.date(2023, 6, 30), **base)
            )
        WalkForwardWindowManager.validate_window_isolation(
            WindowSlice(oos_start=datetime.date(2023, 7, 1), **base)
        )

    def test_malformed_slice_bounds_rejected(self):
        good = dict(
            index=0,
            warmup_start=datetime.date(2022, 12, 1),
            is_start=datetime.date(2023, 1, 1),
            is_end=datetime.date(2023, 6, 30),
            oos_start=datetime.date(2023, 7, 1),
            oos_end=datetime.date(2023, 9, 30),
        )
        for override in (
            {"is_start": datetime.date(2023, 8, 1)},                 # IS start after IS end
            {"oos_end": datetime.date(2023, 6, 1)},                  # OOS start after OOS end
            {"warmup_start": datetime.date(2023, 2, 1)},             # warm-up inside IS
            {"embargo_start": datetime.date(2023, 6, 1),
             "embargo_end": datetime.date(2023, 6, 30)},             # embargo inside IS
            {"embargo_start": datetime.date(2023, 7, 1),
             "embargo_end": datetime.date(2023, 7, 5)},              # embargo inside OOS
            {"embargo_start": datetime.date(2023, 7, 1)},            # only one bound declared
        ):
            with self.subTest(**override):
                with self.assertRaises(WalkForwardError):
                    WalkForwardWindowManager.validate_window_isolation(
                        WindowSlice(**{**good, **override})
                    )

    # ------------------------------------------------------- OOS non-overlap rule

    def test_generated_oos_intervals_do_not_overlap(self):
        for mode in (WindowMode.ROLLING, WindowMode.ANCHORED):
            with self.subTest(mode=mode):
                mgr = WalkForwardWindowManager(
                    in_sample_days=365, out_of_sample_days=90, step_days=90, mode=mode
                )
                slices = mgr.generate_windows(self.start_date, self.end_date)
                for previous, current in zip(slices, slices[1:]):
                    self.assertGreater(current.oos_start, previous.oos_end)
                # step_days == out_of_sample_days, so the stitched OOS curve is also contiguous.
                for previous, current in zip(slices, slices[1:]):
                    self.assertEqual(
                        current.oos_start, previous.oos_end + datetime.timedelta(days=1)
                    )

    def test_step_shorter_than_oos_rejected_by_default(self):
        # Regression: this configuration used to generate silently overlapping OOS intervals,
        # which double-count periods when the per-slice OOS equity curves are concatenated.
        with self.assertRaises(WalkForwardError):
            WalkForwardWindowManager(out_of_sample_days=90, step_days=30)

    def test_overlapping_oos_opt_in_is_flagged_by_sequence_validator(self):
        mgr = WalkForwardWindowManager(
            out_of_sample_days=90, step_days=30, allow_overlapping_oos=True
        )
        slices = mgr.generate_windows(self.start_date, self.end_date)
        self.assertGreater(len(slices), 1)
        self.assertLessEqual(slices[1].oos_start, slices[0].oos_end)
        with self.assertRaises(WalkForwardError):
            WalkForwardWindowManager.validate_slice_sequence(slices)

    def test_sequence_validator_accepts_gapped_slices(self):
        # step_days > out_of_sample_days leaves untested gaps, which is legal (just logged).
        mgr = WalkForwardWindowManager(out_of_sample_days=60, step_days=120)
        slices = mgr.generate_windows(self.start_date, self.end_date)
        self.assertGreater(len(slices), 1)
        self.assertGreater(
            (slices[1].oos_start - slices[0].oos_end).days - 1, 0
        )
        WalkForwardWindowManager.validate_slice_sequence(slices)

    def test_sequence_validator_rejects_out_of_order_slices(self):
        mgr = WalkForwardWindowManager()
        slices = mgr.generate_windows(self.start_date, self.end_date)
        with self.assertRaises(WalkForwardError):
            WalkForwardWindowManager.validate_slice_sequence(list(reversed(slices)))

    # ----------------------------------------------------------------------- WFE

    def test_wfe_calculation(self):
        res_robust = self.mgr.calculate_wfe(is_sharpe=2.0, oos_sharpe=1.4)
        self.assertAlmostEqual(res_robust.wfe_ratio, 0.70, delta=0.01)
        self.assertTrue(res_robust.is_robust)
        self.assertEqual(res_robust.undefined_reason, "")

        res_weak = self.mgr.calculate_wfe(is_sharpe=2.0, oos_sharpe=0.6)
        self.assertAlmostEqual(res_weak.wfe_ratio, 0.30, delta=0.01)
        self.assertFalse(res_weak.is_robust)

    def test_wfe_threshold_is_inclusive(self):
        # 1.0 / 2.0 == 0.50 == min_wfe_threshold, which counts as robust.
        at_threshold = self.mgr.calculate_wfe(is_sharpe=2.0, oos_sharpe=1.0)
        self.assertEqual(at_threshold.wfe_ratio, 0.5)
        self.assertTrue(at_threshold.is_robust)

        just_below = self.mgr.calculate_wfe(is_sharpe=2.0, oos_sharpe=0.99)
        self.assertFalse(just_below.is_robust)

    def test_wfe_undefined_for_non_positive_is_sharpe(self):
        # Regression: the in-sample Sharpe used to be clamped to 0.001, so a strategy that
        # LOST money in-sample (-1.0) but made 0.5 out-of-sample scored WFE 500 and was
        # reported robust. There is no in-sample edge to generalize, so WFE is undefined.
        for is_sharpe in (-1.0, -0.001, 0.0):
            with self.subTest(is_sharpe=is_sharpe):
                res = self.mgr.calculate_wfe(is_sharpe=is_sharpe, oos_sharpe=0.5)
                self.assertNotEqual(res.wfe_ratio, res.wfe_ratio)  # NaN
                self.assertFalse(res.is_robust)
                self.assertNotEqual(res.undefined_reason, "")
                self.assertEqual(res.is_sharpe, is_sharpe)
                self.assertEqual(res.oos_sharpe, 0.5)

        both_negative = self.mgr.calculate_wfe(is_sharpe=-2.0, oos_sharpe=-1.0)
        self.assertNotEqual(both_negative.wfe_ratio, both_negative.wfe_ratio)
        self.assertFalse(both_negative.is_robust)

    def test_wfe_denominator_is_not_clamped(self):
        # A small positive in-sample Sharpe must divide honestly (0.002 / 0.001 == 2.0),
        # not be silently replaced by a 0.001 floor.
        res = self.mgr.calculate_wfe(is_sharpe=0.001, oos_sharpe=0.002)
        self.assertAlmostEqual(res.wfe_ratio, 2.0, places=9)

    def test_wfe_min_is_sharpe_floor(self):
        strict = WalkForwardWindowManager(min_is_sharpe=0.5)
        below = strict.calculate_wfe(is_sharpe=0.3, oos_sharpe=0.2)
        self.assertFalse(below.is_robust)
        self.assertNotEqual(below.undefined_reason, "")

        above = strict.calculate_wfe(is_sharpe=1.0, oos_sharpe=0.6)
        self.assertAlmostEqual(above.wfe_ratio, 0.6, places=9)
        self.assertTrue(above.is_robust)

    def test_wfe_rejects_non_finite_inputs(self):
        for is_sharpe, oos_sharpe in (
            (float("nan"), 1.0),
            (1.0, float("nan")),
            (float("inf"), 1.0),
            (2.0, float("inf")),
            (2.0, float("-inf")),
        ):
            with self.subTest(is_sharpe=is_sharpe, oos_sharpe=oos_sharpe):
                res = self.mgr.calculate_wfe(is_sharpe=is_sharpe, oos_sharpe=oos_sharpe)
                self.assertNotEqual(res.wfe_ratio, res.wfe_ratio)  # NaN
                self.assertFalse(res.is_robust)
                self.assertNotEqual(res.undefined_reason, "")

    def test_wfe_evaluation_shape(self):
        res = self.mgr.calculate_wfe(is_sharpe=1.5, oos_sharpe=1.2)
        self.assertIsInstance(res, WFEEvaluation)
        self.assertEqual(res.is_sharpe, 1.5)
        self.assertEqual(res.oos_sharpe, 1.2)
        self.assertAlmostEqual(res.wfe_ratio, 0.8, places=9)


if __name__ == "__main__":
    unittest.main()
