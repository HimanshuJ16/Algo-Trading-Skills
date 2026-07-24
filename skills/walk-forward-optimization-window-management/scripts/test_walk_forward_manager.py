"""
Unit tests for walk-forward-optimization-window-management skill.

Tests:
1. Rolling window slice generation and date boundary alignment.
2. Anchored expanding window slice generation.
3. Temporal isolation validation and lookahead leakage detection.
4. Walk-Forward Efficiency (WFE) calculation and robustness thresholding.
"""
import datetime
import unittest
from walk_forward_manager import (
    WalkForwardError,
    WalkForwardWindowManager,
    WFEEvaluation,
    WindowMode,
    WindowSlice,
)


class TestWalkForwardWindowManager(unittest.TestCase):

    def setUp(self):
        self.mgr = WalkForwardWindowManager(
            in_sample_days=365, out_of_sample_days=90, step_days=90, mode=WindowMode.ROLLING
        )
        self.start_date = datetime.date(2023, 1, 1)
        self.end_date = datetime.date(2026, 1, 1)

    def test_rolling_window_generation(self):
        slices = self.mgr.generate_windows(self.start_date, self.end_date)
        self.assertTrue(len(slices) >= 5)

        first_slice = slices[0]
        self.assertEqual(first_slice.is_start, datetime.date(2023, 1, 1))
        # IS end date should be is_start + 364 days = 2023-12-31
        self.assertEqual(first_slice.is_end, datetime.date(2023, 12, 31))
        # OOS start date should be 2024-01-01
        self.assertEqual(first_slice.oos_start, datetime.date(2024, 1, 1))

    def test_anchored_window_generation(self):
        anchored_mgr = WalkForwardWindowManager(
            in_sample_days=365, out_of_sample_days=90, step_days=90, mode=WindowMode.ANCHORED
        )
        slices = anchored_mgr.generate_windows(self.start_date, self.end_date)
        self.assertTrue(len(slices) >= 5)

        # All anchored slices must have the same is_start date
        for s in slices:
            self.assertEqual(s.is_start, datetime.date(2023, 1, 1))

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

    def test_wfe_calculation(self):
        res_robust = self.mgr.calculate_wfe(is_sharpe=2.0, oos_sharpe=1.4)
        self.assertAlmostEqual(res_robust.wfe_ratio, 0.70, delta=0.01)
        self.assertTrue(res_robust.is_robust)

        res_weak = self.mgr.calculate_wfe(is_sharpe=2.0, oos_sharpe=0.6)
        self.assertAlmostEqual(res_weak.wfe_ratio, 0.30, delta=0.01)
        self.assertFalse(res_weak.is_robust)


if __name__ == "__main__":
    unittest.main()
