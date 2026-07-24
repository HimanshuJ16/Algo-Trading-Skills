"""
execution-algo-twap-vwap-slicing: Production-grade stateful TWAP/VWAP execution slicer engine,
randomized timing/size jitter, partial fill rescheduling, catch-up policy management,
and benchmark execution slippage reporting.
"""
from dataclasses import dataclass, field
from enum import Enum
import logging
import random
import time
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)


class SlicerType(str, Enum):
    TWAP = "TWAP"
    VWAP = "VWAP"


class CatchUpPolicy(str, Enum):
    AGGRESSIVE_CATCHUP = "AGGRESSIVE_CATCHUP"
    PASSIVE_CONTINUE = "PASSIVE_CONTINUE"
    GIVE_UP_AT_DEADLINE = "GIVE_UP_AT_DEADLINE"


@dataclass
class ChildOrderSlice:
    slice_id: int
    target_qty: float
    target_time: float
    filled_qty: float = 0.0
    filled_avg_price: float = 0.0
    status: str = "PENDING"  # PENDING, FILLED, PARTIAL, CANCELLED


@dataclass
class ExecutionReport:
    algo_type: SlicerType
    total_requested: float
    total_filled: float
    completion_pct: float
    vwap_achieved_price: float
    benchmark_price: float
    slippage_bps: float
    child_slices_count: int


class ExecutionSlicer:
    """
    Stateful execution slicer tracking child order progress, dynamic schedule recalculation,
    and benchmark slippage reporting.
    """

    def __init__(
        self,
        total_qty: float,
        algo_type: SlicerType = SlicerType.TWAP,
        num_intervals: int = 10,
        interval_seconds: float = 60.0,
        historical_volume_curve: Optional[List[float]] = None,
        jitter_pct: float = 0.15,
        catch_up_policy: CatchUpPolicy = CatchUpPolicy.PASSIVE_CONTINUE,
        start_time: Optional[float] = None,
    ):
        self.total_qty = total_qty
        self.algo_type = algo_type
        self.num_intervals = num_intervals
        self.interval_seconds = interval_seconds
        self.historical_volume_curve = historical_volume_curve
        self.jitter_pct = jitter_pct
        self.catch_up_policy = catch_up_policy
        self.start_time = start_time or time.time()

        self.slices: List[ChildOrderSlice] = []
        self._build_schedule()

    def _build_schedule(self):
        self.slices = []
        if self.algo_type == SlicerType.TWAP:
            raw_sizes = twap_schedule(self.total_qty, self.num_intervals, self.jitter_pct)
        else:
            curve = self.historical_volume_curve or [1.0 / self.num_intervals] * self.num_intervals
            raw_sizes = vwap_schedule(self.total_qty, curve, self.jitter_pct)

        for i, qty in enumerate(raw_sizes):
            # Apply time jitter +/- 15% of interval length
            time_offset = i * self.interval_seconds + random.uniform(-0.15, 0.15) * self.interval_seconds
            t = self.start_time + max(0.0, time_offset)
            self.slices.append(ChildOrderSlice(slice_id=i, target_qty=float(qty), target_time=t))

    def on_child_fill(self, slice_id: int, filled_qty: float, fill_price: float):
        """Updates fill state for a child order slice and reschedules if needed."""
        if slice_id < 0 or slice_id >= len(self.slices):
            return

        child = self.slices[slice_id]
        new_qty = child.filled_qty + filled_qty
        if new_qty > 0:
            child.filled_avg_price = (
                (child.filled_qty * child.filled_avg_price + filled_qty * fill_price) / new_qty
            )
        child.filled_qty = new_qty

        if child.filled_qty >= child.target_qty:
            child.status = "FILLED"
        else:
            child.status = "PARTIAL"

        # Re-evaluate remaining schedule if underfilled
        self._reschedule_unfilled()

    def _reschedule_unfilled(self):
        total_filled = sum(s.filled_qty for s in self.slices)
        remaining_qty = max(0.0, self.total_qty - total_filled)

        pending_slices = [s for s in self.slices if s.status == "PENDING"]
        if not pending_slices or remaining_qty <= 0:
            return

        if self.catch_up_policy == CatchUpPolicy.AGGRESSIVE_CATCHUP:
            base_size = remaining_qty / len(pending_slices)
            for s in pending_slices:
                s.target_qty = round(base_size, 4)

    def get_execution_report(self, benchmark_price: float) -> ExecutionReport:
        """Generates post-execution report with VWAP achieved price and benchmark slippage in bps."""
        total_filled = sum(s.filled_qty for s in self.slices)
        total_cost = sum(s.filled_qty * s.filled_avg_price for s in self.slices)
        achieved_vwap = total_cost / total_filled if total_filled > 0 else 0.0

        slippage_bps = 0.0
        if benchmark_price > 0 and achieved_vwap > 0:
            slippage_bps = ((achieved_vwap - benchmark_price) / benchmark_price) * 10_000.0

        return ExecutionReport(
            algo_type=self.algo_type,
            total_requested=self.total_qty,
            total_filled=total_filled,
            completion_pct=round((total_filled / self.total_qty * 100) if self.total_qty > 0 else 0.0, 2),
            vwap_achieved_price=round(achieved_vwap, 4),
            benchmark_price=round(benchmark_price, 4),
            slippage_bps=round(slippage_bps, 2),
            child_slices_count=len(self.slices),
        )


# Backward compatibility functions
def twap_schedule(total_qty, num_intervals, jitter_pct=0.15):
    base = total_qty / num_intervals
    sizes = [base * (1 + random.uniform(-jitter_pct, jitter_pct)) for _ in range(num_intervals)]
    scale = total_qty / sum(sizes) if sum(sizes) > 0 else 1.0
    rounded_sizes = [round(s * scale) for s in sizes]
    diff = int(total_qty - sum(rounded_sizes))
    if rounded_sizes:
        rounded_sizes[-1] += diff
    return rounded_sizes


def vwap_schedule(total_qty, historical_volume_curve, jitter_pct=0.15):
    total_proportion = sum(historical_volume_curve)
    if total_proportion <= 0:
        total_proportion = 1.0
    sizes = [
        total_qty * (p / total_proportion) * (1 + random.uniform(-jitter_pct, jitter_pct))
        for p in historical_volume_curve
    ]
    scale = total_qty / sum(sizes) if sum(sizes) > 0 else 1.0
    rounded_sizes = [round(s * scale) for s in sizes]
    diff = int(total_qty - sum(rounded_sizes))
    if rounded_sizes:
        rounded_sizes[-1] += diff
    return rounded_sizes
