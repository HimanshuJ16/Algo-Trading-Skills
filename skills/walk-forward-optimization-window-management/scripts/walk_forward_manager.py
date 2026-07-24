"""
walk-forward-optimization-window-management: Production-grade Walk-Forward Optimization (WFO)
window slice generator, temporal isolation leakage validator, and Walk-Forward Efficiency (WFE) evaluator.
"""
from dataclasses import dataclass
import datetime
from enum import Enum
import logging
import math
from typing import Any, Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)


class WalkForwardError(ValueError):
    """Raised when window configuration is invalid or temporal leakage occurs."""
    pass


class WindowMode(str, Enum):
    ROLLING = "ROLLING"    # Fixed-length sliding in-sample window
    ANCHORED = "ANCHORED"  # Expanding in-sample window anchored to start_date


@dataclass
class WindowSlice:
    index: int
    warmup_start: datetime.date
    is_start: datetime.date
    is_end: datetime.date
    oos_start: datetime.date
    oos_end: datetime.date


@dataclass
class WFEEvaluation:
    wfe_ratio: float
    is_sharpe: float
    oos_sharpe: float
    is_robust: bool  # True if WFE >= 0.50


class WalkForwardWindowManager:
    """
    Generates non-overlapping Walk-Forward Optimization time windows, enforces strict
    temporal isolation to eliminate lookahead bias, and evaluates out-of-sample efficiency.
    """

    def __init__(
        self,
        in_sample_days: int = 365,
        out_of_sample_days: int = 90,
        step_days: int = 90,
        warmup_days: int = 30,
        mode: WindowMode = WindowMode.ROLLING,
        min_wfe_threshold: float = 0.50,
    ):
        self.in_sample_days = in_sample_days
        self.out_of_sample_days = out_of_sample_days
        self.step_days = step_days
        self.warmup_days = warmup_days
        self.mode = mode
        self.min_wfe_threshold = min_wfe_threshold

    def generate_windows(self, start_date: datetime.date, end_date: datetime.date) -> List[WindowSlice]:
        """Generates sequence of In-Sample and Out-of-Sample window slices."""
        total_days = (end_date - start_date).days
        min_required = self.in_sample_days + self.out_of_sample_days
        if total_days < min_required:
            raise WalkForwardError(
                f"Date range ({total_days} days) is less than required minimum ({min_required} days)."
            )

        slices: List[WindowSlice] = []
        slice_idx = 0
        current_is_start = start_date

        while True:
            if self.mode == WindowMode.ROLLING:
                is_start = current_is_start
            else:  # ANCHORED
                is_start = start_date

            is_end = current_is_start + datetime.timedelta(days=self.in_sample_days - 1)
            oos_start = is_end + datetime.timedelta(days=1)
            oos_end = oos_start + datetime.timedelta(days=self.out_of_sample_days - 1)

            if oos_end > end_date:
                break

            warmup_start = is_start - datetime.timedelta(days=self.warmup_days)

            slice_obj = WindowSlice(
                index=slice_idx,
                warmup_start=warmup_start,
                is_start=is_start,
                is_end=is_end,
                oos_start=oos_start,
                oos_end=oos_end,
            )

            self.validate_window_isolation(slice_obj)
            slices.append(slice_obj)

            slice_idx += 1
            current_is_start = current_is_start + datetime.timedelta(days=self.step_days)

        logger.info(f"Generated {len(slices)} WFO window slices ({self.mode.value} mode).")
        return slices

    @staticmethod
    def validate_window_isolation(slice_obj: WindowSlice):
        """Verifies strict temporal isolation between IS and OOS windows."""
        if slice_obj.is_end >= slice_obj.oos_start:
            raise WalkForwardError(
                f"LOOKAHEAD LEAKAGE DETECTED in Slice {slice_obj.index}! "
                f"IS end ({slice_obj.is_end}) >= OOS start ({slice_obj.oos_start})."
            )
        if slice_obj.is_start > slice_obj.is_end:
            raise WalkForwardError(f"Invalid IS window in Slice {slice_obj.index}: start > end.")
        if slice_obj.oos_start > slice_obj.oos_end:
            raise WalkForwardError(f"Invalid OOS window in Slice {slice_obj.index}: start > end.")

    def calculate_wfe(self, is_sharpe: float, oos_sharpe: float) -> WFEEvaluation:
        """
        Calculates Walk-Forward Efficiency ratio (OOS Sharpe / IS Sharpe).
        WFE >= 0.50 indicates robust out-of-sample generalization.
        """
        safe_is_sharpe = max(is_sharpe, 0.001)
        wfe = oos_sharpe / safe_is_sharpe
        is_robust = wfe >= self.min_wfe_threshold

        logger.info(
            f"WFE Evaluation: OOS Sharpe ({oos_sharpe:.2f}) / IS Sharpe ({is_sharpe:.2f}) = {wfe:.2f} (Robust: {is_robust})"
        )
        return WFEEvaluation(
            wfe_ratio=wfe,
            is_sharpe=is_sharpe,
            oos_sharpe=oos_sharpe,
            is_robust=is_robust,
        )
