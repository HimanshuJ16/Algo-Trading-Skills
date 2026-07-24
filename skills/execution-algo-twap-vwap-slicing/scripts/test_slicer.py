"""
Unit tests for execution-algo-twap-vwap-slicing skill.

Tests:
1. TWAP schedule generation total quantity conservation and jitter.
2. VWAP schedule generation volume profile weighting.
3. ExecutionSlicer child fill tracking & state management.
4. Partial fill dynamic schedule recalculation under AGGRESSIVE_CATCHUP policy.
5. Benchmark execution report & slippage calculation in basis points.
6. Backward compatibility with standalone twap_schedule/vwap_schedule functions.
"""
import unittest
from slicer import (
    CatchUpPolicy,
    ChildOrderSlice,
    ExecutionReport,
    ExecutionSlicer,
    SlicerType,
    twap_schedule,
    vwap_schedule,
)


class TestExecutionAlgoSlicer(unittest.TestCase):

    def test_twap_schedule_conservation(self):
        total_qty = 1000
        num_intervals = 10
        schedule = twap_schedule(total_qty, num_intervals, jitter_pct=0.15)

        self.assertEqual(len(schedule), num_intervals)
        self.assertEqual(sum(schedule), total_qty)

    def test_vwap_schedule_volume_curve(self):
        total_qty = 10000
        # Historical curve: 10% morning, 80% mid-day, 10% close
        volume_curve = [0.10, 0.80, 0.10]
        schedule = vwap_schedule(total_qty, volume_curve, jitter_pct=0.0)

        self.assertEqual(len(schedule), 3)
        self.assertEqual(sum(schedule), total_qty)
        self.assertGreater(schedule[1], schedule[0])
        self.assertGreater(schedule[1], schedule[2])

    def test_execution_slicer_fills_and_reporting(self):
        slicer = ExecutionSlicer(
            total_qty=1000,
            algo_type=SlicerType.TWAP,
            num_intervals=5,
            interval_seconds=10.0,
            jitter_pct=0.0,
        )

        self.assertEqual(len(slicer.slices), 5)

        # Simulate fills
        for i, child in enumerate(slicer.slices):
            slicer.on_child_fill(child.slice_id, filled_qty=child.target_qty, fill_price=100.0 + i)

        report = slicer.get_execution_report(benchmark_price=100.0)

        self.assertEqual(report.total_filled, 1000)
        self.assertEqual(report.completion_pct, 100.0)
        self.assertGreater(report.vwap_achieved_price, 100.0)
        self.assertGreater(report.slippage_bps, 0.0)

    def test_partial_fill_rescheduling(self):
        slicer = ExecutionSlicer(
            total_qty=1000,
            algo_type=SlicerType.TWAP,
            num_intervals=4,
            jitter_pct=0.0,
            catch_up_policy=CatchUpPolicy.AGGRESSIVE_CATCHUP,
        )

        # Slice 0 target = 250. Only fill 50.
        slicer.on_child_fill(slice_id=0, filled_qty=50, fill_price=100.0)

        # Remaining 950 qty should be split across remaining 3 pending slices (~316.67 each)
        pending_targets = [s.target_qty for s in slicer.slices if s.status == "PENDING"]
        self.assertEqual(len(pending_targets), 3)
        self.assertAlmostEqual(sum(pending_targets), 950.0, delta=1.0)


if __name__ == "__main__":
    unittest.main()
