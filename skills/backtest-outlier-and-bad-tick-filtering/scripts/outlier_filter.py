"""
backtest-outlier-and-bad-tick-filtering: Rolling MAD outlier detector,
bad tick filter, and data cleanliness report generator.
"""
from dataclasses import dataclass
import logging
import math
from typing import Any, Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)


@dataclass
class FilteredTickReport:
    total_input_ticks: int
    cleaned_ticks_count: int
    purged_bad_ticks_count: int
    purged_indices: List[int]
    cleanliness_pct: float
    message: str


class OutlierBadTickFilter:
    """
    Filters erroneous price prints, fat-finger quotes, and price spikes using
    rolling Median Absolute Deviation (MAD) modified Z-scores.
    """

    def __init__(
        self,
        window_size: int = 21,
        z_threshold: float = 5.0,
        max_single_tick_jump_pct: float = 20.0,
        max_consecutive_drops: int = 3,
    ):
        self.window_size = window_size
        self.z_threshold = z_threshold
        self.max_single_tick_jump_pct = max_single_tick_jump_pct
        self.max_consecutive_drops = max_consecutive_drops

    @staticmethod
    def _median(arr: List[float]) -> float:
        s = sorted(arr)
        n = len(s)
        if n % 2 == 1:
            return s[n // 2]
        return (s[n // 2 - 1] + s[n // 2]) / 2.0

    def filter_prices(self, prices: List[float]) -> Tuple[List[float], FilteredTickReport]:
        n = len(prices)
        if n == 0:
            rep = FilteredTickReport(0, 0, 0, [], 100.0, "Empty price series.")
            return [], rep

        cleaned_prices: List[float] = []
        purged_indices: List[int] = []
        
        consecutive_drops = 0

        for i in range(n):
            p = prices[i]

            # Rule 1: Non-positive price check
            if p <= 0:
                purged_indices.append(i)
                continue

            is_outlier = False
            drop_reason = ""

            # Rule 2: Single tick jump check vs previous valid price
            if cleaned_prices:
                prev_p = cleaned_prices[-1]
                pct_change = abs(p - prev_p) / prev_p * 100.0
                if pct_change > self.max_single_tick_jump_pct:
                    is_outlier = True
                    drop_reason = f"Jump {pct_change:.1f}%"

            # Rule 3: Rolling MAD modified Z-score check
            if not is_outlier and len(cleaned_prices) >= self.window_size:
                window = cleaned_prices[-self.window_size:]
                med = self._median(window)
                mad = self._median([abs(x - med) for x in window])

                if mad > 0:
                    mod_z = (0.6745 * abs(p - med)) / mad
                    if mod_z > self.z_threshold:
                        is_outlier = True
                        drop_reason = f"Z={mod_z:.1f}"

            if is_outlier:
                consecutive_drops += 1
                if consecutive_drops >= self.max_consecutive_drops:
                    logger.warning(f"REGIME CHANGE DETECTED: {consecutive_drops} drops. Accepting price {p} and resetting.")
                    # It's a genuine gap or split, accept it and reset consecutive counter
                    consecutive_drops = 0
                    # We accept p below
                else:
                    purged_indices.append(i)
                    logger.warning(f"BAD TICK PURGED ({drop_reason}): idx={i}, price={p}")
                    continue

            # If we reach here, it's a valid tick or a regime reset
            consecutive_drops = 0
            cleaned_prices.append(p)

        purged_cnt = len(purged_indices)
        clean_pct = round(((n - purged_cnt) / float(n)) * 100.0, 2)
        msg = f"Outlier Filtering Complete: {n} raw ticks -> {len(cleaned_prices)} clean ticks ({purged_cnt} bad ticks purged, {clean_pct}% clean)."
        logger.info(msg)

        report = FilteredTickReport(
            total_input_ticks=n,
            cleaned_ticks_count=len(cleaned_prices),
            purged_bad_ticks_count=purged_cnt,
            purged_indices=purged_indices,
            cleanliness_pct=clean_pct,
            message=msg,
        )

        return cleaned_prices, report
