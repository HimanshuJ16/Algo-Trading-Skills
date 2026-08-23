"""
data-vendor-cross-validation-for-backtests: Multi-vendor OHLCV bar alignment,
per-bar price discrepancy scorer, volume anomaly detector, bar integrity auditor,
missing bar auditor, and cross-validation report generator.
"""
from dataclasses import dataclass, field
import logging
import math
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
class VolumeDiscrepancy:
    timestamp: str
    volume_a: float
    volume_b: float
    ratio: float


@dataclass
class BarIntegrityIssue:
    """A bar that cannot be meaningfully compared at all, as opposed to one that
    simply disagrees between vendors."""
    timestamp: str
    vendor: str          # 'A' or 'B'
    reason: str


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
    # Additive fields (v1.1.0); defaulted so existing construction still works.
    volume_flagged_bars: List[VolumeDiscrepancy] = field(default_factory=list)
    integrity_issues: List[BarIntegrityIssue] = field(default_factory=list)
    comparable_bars: int = 0


class DataVendorCrossValidator:
    """
    Cross-validates OHLCV data from two independent vendors to detect price
    discrepancies, volume anomalies, unusable bars, and missing bars before they
    corrupt backtest results.

    Vendor A is the *reference* vendor: the relative close delta is expressed as a
    fraction of the Vendor A close, so the metric is deliberately asymmetric.
    Swapping the two arguments changes the reported bps values (though, for small
    deltas, not the pass/fail verdict). Pass the primary/production vendor as A.

    Timestamps are matched as exact strings. Callers MUST normalise both vendors to
    a single canonical UTC timestamp representation before calling; this class
    cannot detect a timezone offset, only the total absence of overlap it produces.
    """

    def __init__(
        self,
        price_discrepancy_threshold_bps: float = 50.0,
        missing_bar_tolerance_pct: float = 1.0,
        volume_spike_ratio_threshold: float = 3.0,
    ):
        self.price_thresh_bps = price_discrepancy_threshold_bps
        self.missing_bar_tol_pct = missing_bar_tolerance_pct
        self.volume_spike_ratio_threshold = volume_spike_ratio_threshold

    @staticmethod
    def _index_bars(
        bars: List[OHLCVBar], vendor: str
    ) -> Tuple[Dict[str, OHLCVBar], List[BarIntegrityIssue]]:
        """
        Builds a timestamp -> bar index, recording rather than silently swallowing
        duplicate timestamps. Duplicate bars are a known symptom of duplicate trade
        reporting and of a botched vendor backfill; last-write-wins would hide them.
        """
        index: Dict[str, OHLCVBar] = {}
        issues: List[BarIntegrityIssue] = []
        for bar in bars:
            if bar.timestamp in index:
                issues.append(BarIntegrityIssue(
                    timestamp=bar.timestamp,
                    vendor=vendor,
                    reason="duplicate timestamp in vendor feed",
                ))
            index[bar.timestamp] = bar
        return index, issues

    def _bar_integrity_issue(
        self, ts: str, bar_a: OHLCVBar, bar_b: OHLCVBar
    ) -> Optional[BarIntegrityIssue]:
        """
        Returns an integrity issue if the pair cannot yield a meaningful bps delta.

        Non-finite closes (NaN/Inf) and a zero reference close are not agreement;
        they are unusable data. Treating them as a 0 bps match is exactly how a
        corrupt feed silently passes a validation gate.
        """
        for bar, vendor in ((bar_a, "A"), (bar_b, "B")):
            if not math.isfinite(bar.close):
                return BarIntegrityIssue(ts, vendor, f"non-finite close ({bar.close})")
            if not math.isfinite(bar.volume):
                return BarIntegrityIssue(ts, vendor, f"non-finite volume ({bar.volume})")
            if bar.volume < 0:
                return BarIntegrityIssue(ts, vendor, f"negative volume ({bar.volume})")
        if bar_a.close == 0.0:
            return BarIntegrityIssue(ts, "A", "zero reference close (no-trade sentinel?)")
        return None

    def _volume_ratio(self, volume_a: float, volume_b: float) -> float:
        """Symmetric volume ratio (max/min), so a spike in either vendor is caught."""
        hi = max(volume_a, volume_b)
        lo = min(volume_a, volume_b)
        if lo <= 0.0:
            return math.inf if hi > 0.0 else 1.0
        return hi / lo

    def validate(
        self,
        symbol: str,
        vendor_a_bars: List[OHLCVBar],
        vendor_b_bars: List[OHLCVBar],
    ) -> CrossValidationReport:
        """
        Aligns bars by timestamp, computes per-bar close price discrepancy (bps),
        audits volume ratios, bar integrity, and missing bar coverage, then
        generates a pass/fail verdict.

        Fails on: missing bar ratio over tolerance, any bar over the price
        threshold, any integrity issue, or zero overlap between the two vendors.
        Volume spikes are flagged for audit but do not by themselves fail the
        verdict, since a genuine volume difference is common between a consolidated
        tape and a primary-exchange-only feed.

        Raises:
            ValueError: if both vendor datasets are empty. There is nothing to
                cross-validate, and returning PASS would be a vacuously green gate.
        """
        if not vendor_a_bars and not vendor_b_bars:
            raise ValueError(
                f"Cannot cross-validate '{symbol}': both vendor datasets are empty."
            )

        map_a, issues_a = self._index_bars(vendor_a_bars, "A")
        map_b, issues_b = self._index_bars(vendor_b_bars, "B")
        integrity_issues: List[BarIntegrityIssue] = issues_a + issues_b

        all_ts = sorted(set(map_a.keys()) | set(map_b.keys()))

        matched = 0
        missing_in_a = 0
        missing_in_b = 0
        deltas: List[float] = []
        flagged: List[BarDiscrepancy] = []
        volume_flagged: List[VolumeDiscrepancy] = []

        for ts in all_ts:
            bar_a = map_a.get(ts)
            bar_b = map_b.get(ts)

            if bar_a is None:
                missing_in_a += 1
                continue
            if bar_b is None:
                missing_in_b += 1
                continue

            matched += 1

            issue = self._bar_integrity_issue(ts, bar_a, bar_b)
            if issue is not None:
                integrity_issues.append(issue)
                continue

            delta_bps = abs(bar_a.close - bar_b.close) / abs(bar_a.close) * 10000.0
            deltas.append(delta_bps)
            if delta_bps > self.price_thresh_bps:
                flagged.append(BarDiscrepancy(
                    timestamp=ts,
                    close_a=bar_a.close,
                    close_b=bar_b.close,
                    delta_bps=round(delta_bps, 2),
                ))

            vol_ratio = self._volume_ratio(bar_a.volume, bar_b.volume)
            if vol_ratio > self.volume_spike_ratio_threshold:
                volume_flagged.append(VolumeDiscrepancy(
                    timestamp=ts,
                    volume_a=bar_a.volume,
                    volume_b=bar_b.volume,
                    ratio=round(vol_ratio, 2) if math.isfinite(vol_ratio) else vol_ratio,
                ))

        total_bars = len(all_ts)
        total_missing = missing_in_a + missing_in_b
        missing_ratio = (total_missing / max(1, total_bars)) * 100.0

        avg_delta = sum(deltas) / len(deltas) if deltas else 0.0
        max_delta = max(deltas) if deltas else 0.0

        reasons: List[str] = []
        if missing_ratio > self.missing_bar_tol_pct:
            reasons.append(
                f"Missing bar ratio {missing_ratio:.2f}% > {self.missing_bar_tol_pct}%")
        if matched == 0:
            reasons.append(
                "zero overlapping timestamps: check that both vendors were normalised "
                "to the same timezone and timestamp format before comparison")
        if flagged:
            reasons.append(
                f"{len(flagged)} bars exceed {self.price_thresh_bps} bps price discrepancy")
        if integrity_issues:
            reasons.append(f"{len(integrity_issues)} bars failed integrity checks")

        is_passed = not reasons

        if not is_passed:
            msg = f"CROSS-VALIDATION FAILED for '{symbol}': {'; '.join(reasons)}."
            logger.warning(msg)
        else:
            msg = (f"Cross-validation PASSED for '{symbol}': {matched} matched bars, "
                   f"avg delta {avg_delta:.1f} bps.")
            logger.info(msg)

        if volume_flagged:
            logger.warning(
                "Volume anomaly for '%s': %d bars exceed %.1fx volume ratio "
                "(flagged for duplicate-reporting audit, not a verdict failure).",
                symbol, len(volume_flagged), self.volume_spike_ratio_threshold,
            )

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
            volume_flagged_bars=volume_flagged,
            integrity_issues=integrity_issues,
            comparable_bars=len(deltas),
        )
