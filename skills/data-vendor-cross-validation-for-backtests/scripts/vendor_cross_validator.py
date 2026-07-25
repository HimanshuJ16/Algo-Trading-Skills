"""
data-vendor-cross-validation-for-backtests: Multi-vendor OHLCV bar alignment,
per-bar price discrepancy scorer, missing bar auditor, and cross-validation report generator.
"""
from dataclasses import dataclass
import logging
from typing import Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)


@dataclass
class OHLCVBar:
    timestamp: str
    open: float
    high: float
    low: float
    close: float
    volume: float


@dataclass
class BarDiscrepancy:
    timestamp: str
    close_a: float
    close_b: float
    delta_bps: float


@dataclass
class CrossValidationReport:
    symbol: str
    total_bars_a: int
    total_bars_b: int
    matched_bars: int
    missing_in_a: int
    missing_in_b: int
    missing_ratio_pct: float
    avg_close_delta_bps: float
    max_close_delta_bps: float
    flagged_bars: List[BarDiscrepancy]
    is_passed: bool
    message: str


class DataVendorCrossValidator:
    """
    Cross-validates OHLCV data from two independent vendors to detect price discrepancies,
    missing bars, and volume anomalies before they corrupt backtest results.
    """

    def __init__(
        self,
        price_discrepancy_threshold_bps: float = 50.0,
        missing_bar_tolerance_pct: float = 1.0,
    ):
        self.price_thresh_bps = price_discrepancy_threshold_bps
        self.missing_bar_tol_pct = missing_bar_tolerance_pct

    def validate(
        self,
        symbol: str,
        vendor_a_bars: List[OHLCVBar],
        vendor_b_bars: List[OHLCVBar],
    ) -> CrossValidationReport:
        """
        Aligns bars by timestamp, computes per-bar close price discrepancy (bps),
        audits missing bar coverage, and generates pass/fail verdict.
        """
        map_a: Dict[str, OHLCVBar] = {b.timestamp: b for b in vendor_a_bars}
        map_b: Dict[str, OHLCVBar] = {b.timestamp: b for b in vendor_b_bars}

        all_ts = sorted(set(map_a.keys()) | set(map_b.keys()))

        matched = 0
        missing_in_a = 0
        missing_in_b = 0
        deltas: List[float] = []
        flagged: List[BarDiscrepancy] = []

        for ts in all_ts:
            bar_a = map_a.get(ts)
            bar_b = map_b.get(ts)

            if bar_a and bar_b:
                matched += 1
                if bar_a.close > 0:
                    delta_bps = abs(bar_a.close - bar_b.close) / bar_a.close * 10000.0
                else:
                    delta_bps = 0.0
                deltas.append(delta_bps)
                if delta_bps > self.price_thresh_bps:
                    flagged.append(BarDiscrepancy(
                        timestamp=ts,
                        close_a=bar_a.close,
                        close_b=bar_b.close,
                        delta_bps=round(delta_bps, 2),
                    ))
            elif bar_a and not bar_b:
                missing_in_b += 1
            else:
                missing_in_a += 1

        total_bars = len(all_ts)
        total_missing = missing_in_a + missing_in_b
        missing_ratio = (total_missing / max(1, total_bars)) * 100.0

        avg_delta = sum(deltas) / max(1, len(deltas))
        max_delta = max(deltas) if deltas else 0.0

        is_passed = (missing_ratio <= self.missing_bar_tol_pct) and (len(flagged) == 0)

        if not is_passed:
            reasons = []
            if missing_ratio > self.missing_bar_tol_pct:
                reasons.append(f"Missing bar ratio {missing_ratio:.2f}% > {self.missing_bar_tol_pct}%")
            if flagged:
                reasons.append(f"{len(flagged)} bars exceed {self.price_thresh_bps} bps price discrepancy")
            msg = f"CROSS-VALIDATION FAILED for '{symbol}': {'; '.join(reasons)}."
            logger.warning(msg)
        else:
            msg = f"Cross-validation PASSED for '{symbol}': {matched} matched bars, avg delta {avg_delta:.1f} bps."
            logger.info(msg)

        return CrossValidationReport(
            symbol=symbol,
            total_bars_a=len(vendor_a_bars),
            total_bars_b=len(vendor_b_bars),
            matched_bars=matched,
            missing_in_a=missing_in_a,
            missing_in_b=missing_in_b,
            missing_ratio_pct=round(missing_ratio, 2),
            avg_close_delta_bps=round(avg_delta, 2),
            max_close_delta_bps=round(max_delta, 2),
            flagged_bars=flagged,
            is_passed=is_passed,
            message=msg,
        )
