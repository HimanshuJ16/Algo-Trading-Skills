"""
multi-timeframe-backtest-consistency-checks: OHLCV bar resampler and
cross-timeframe signal divergence auditor.
"""
from dataclasses import dataclass
import logging
from typing import Dict, List, Tuple

logger = logging.getLogger(__name__)


@dataclass
class Bar:
    timestamp: int  # epoch seconds
    open: float
    high: float
    low: float
    close: float
    volume: float


@dataclass
class ConsistencyReport:
    high_res_bars: int
    low_res_bars: int
    matched_signals: int
    max_divergence_pct: float
    avg_divergence_pct: float
    is_consistent: bool
    message: str


class TimeframeConsistencyChecker:
    """Resamples high-res bars to lower-res and checks signal consistency."""

    def __init__(self, divergence_tolerance_pct: float = 1.0):
        self.tolerance_pct = divergence_tolerance_pct

    def resample_bars(self, bars: List[Bar], factor: int) -> List[Bar]:
        """Aggregates bars by factor (e.g. 5 x 1-min -> 1 x 5-min)."""
        resampled = []
        for i in range(0, len(bars), factor):
            chunk = bars[i:i+factor]
            if not chunk:
                continue
            resampled.append(Bar(
                timestamp=chunk[0].timestamp,
                open=chunk[0].open,
                high=max(b.high for b in chunk),
                low=min(b.low for b in chunk),
                close=chunk[-1].close,
                volume=sum(b.volume for b in chunk),
            ))
        return resampled

    def compute_sma(self, bars: List[Bar], period: int) -> List[Tuple[int, float]]:
        """Computes SMA on close prices, returning (timestamp, sma_value) pairs."""
        results = []
        for i in range(period - 1, len(bars)):
            window = bars[i - period + 1:i + 1]
            sma = sum(b.close for b in window) / period
            results.append((bars[i].timestamp, round(sma, 6)))
        return results

    def check_consistency(
        self,
        high_res_bars: List[Bar],
        resample_factor: int,
        sma_period: int,
    ) -> ConsistencyReport:
        low_res = self.resample_bars(high_res_bars, resample_factor)
        sma_high = dict(self.compute_sma(high_res_bars, sma_period * resample_factor))
        sma_low = dict(self.compute_sma(low_res, sma_period))

        divergences = []
        for ts, val_low in sma_low.items():
            val_high = sma_high.get(ts)
            if val_high is not None and val_high != 0:
                div_pct = abs(val_high - val_low) / abs(val_high) * 100.0
                divergences.append(div_pct)

        max_div = max(divergences) if divergences else 0.0
        avg_div = sum(divergences) / len(divergences) if divergences else 0.0
        is_ok = max_div <= self.tolerance_pct

        if is_ok:
            msg = f"Timeframe consistency PASSED: max divergence {max_div:.4f}%."
        else:
            msg = f"TIMEFRAME INCONSISTENCY: max divergence {max_div:.4f}% exceeds {self.tolerance_pct}%."
            logger.warning(msg)

        return ConsistencyReport(
            high_res_bars=len(high_res_bars),
            low_res_bars=len(low_res),
            matched_signals=len(divergences),
            max_divergence_pct=round(max_div, 4),
            avg_divergence_pct=round(avg_div, 4),
            is_consistent=is_ok,
            message=msg,
        )
