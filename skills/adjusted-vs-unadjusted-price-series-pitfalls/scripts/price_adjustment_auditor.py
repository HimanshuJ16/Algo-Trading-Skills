"""
adjusted-vs-unadjusted-price-series-pitfalls: Corporate action discontinuity detector,
adjustment type classifier, and split/dividend ratio applicator.
"""
from dataclasses import dataclass
import logging
from typing import Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)


@dataclass
class CorporateAction:
    date: str
    action_type: str  # SPLIT or DIVIDEND
    ratio: float      # e.g. 2.0 for 2:1 split, or dividend yield


@dataclass
class DiscontinuityEvent:
    date: str
    prev_close: float
    next_open: float
    pct_change: float
    likely_cause: str


@dataclass
class AdjustmentAuditReport:
    symbol: str
    total_bars: int
    discontinuities_found: List[DiscontinuityEvent]
    detected_adjustment_type: str
    is_consistent: bool
    message: str


class PriceAdjustmentAuditor:
    """
    Detects corporate action discontinuities in price series, classifies adjustment type,
    and validates consistency across multi-symbol backtests.
    """

    def __init__(self, discontinuity_threshold_pct: float = 30.0):
        self.threshold_pct = discontinuity_threshold_pct

    def detect_discontinuities(
        self,
        symbol: str,
        closes: List[float],
        dates: List[str],
        known_actions: Optional[List[CorporateAction]] = None,
    ) -> AdjustmentAuditReport:
        """
        Scans close prices for overnight jumps exceeding threshold and classifies them.
        """
        action_map: Dict[str, CorporateAction] = {}
        if known_actions:
            action_map = {a.date: a for a in known_actions}

        discontinuities: List[DiscontinuityEvent] = []

        for i in range(1, len(closes)):
            if closes[i - 1] <= 0:
                continue
            pct_change = ((closes[i] - closes[i - 1]) / closes[i - 1]) * 100.0

            if abs(pct_change) >= self.threshold_pct:
                date = dates[i] if i < len(dates) else f"bar_{i}"
                action = action_map.get(date)

                if action:
                    cause = f"{action.action_type} (ratio={action.ratio})"
                else:
                    cause = "UNKNOWN — possible unadjusted split or data error"

                discontinuities.append(DiscontinuityEvent(
                    date=date,
                    prev_close=closes[i - 1],
                    next_open=closes[i],
                    pct_change=round(pct_change, 2),
                    likely_cause=cause,
                ))

        if discontinuities:
            has_known = any(d.likely_cause.startswith("SPLIT") or d.likely_cause.startswith("DIVIDEND") for d in discontinuities)
            adj_type = "UNADJUSTED" if has_known else "UNKNOWN"
            msg = f"DISCONTINUITY DETECTED for '{symbol}': {len(discontinuities)} overnight jumps >= {self.threshold_pct}%. Series likely {adj_type}."
            logger.warning(msg)
        else:
            adj_type = "ADJUSTED"
            msg = f"No discontinuities detected for '{symbol}'. Series appears ADJUSTED."

        return AdjustmentAuditReport(
            symbol=symbol,
            total_bars=len(closes),
            discontinuities_found=discontinuities,
            detected_adjustment_type=adj_type,
            is_consistent=(len(discontinuities) == 0),
            message=msg,
        )

    def apply_split_adjustment(
        self,
        closes: List[float],
        split_index: int,
        split_ratio: float,
    ) -> List[float]:
        """
        Backward-adjusts all prices before the split date by dividing by the split ratio.
        """
        adjusted = list(closes)
        for i in range(split_index):
            adjusted[i] = round(adjusted[i] / split_ratio, 4)
        return adjusted

    def validate_universe_consistency(
        self,
        reports: List[AdjustmentAuditReport],
    ) -> Tuple[bool, str]:
        """
        Validates all symbols in the backtest universe use the same adjustment type.
        """
        types = set(r.detected_adjustment_type for r in reports)
        if len(types) <= 1:
            return True, f"Universe consistent: all symbols are {types.pop() if types else 'UNKNOWN'}."
        msg = f"MIXED ADJUSTMENT TYPES in universe: {types}. This will corrupt cross-asset signals."
        logger.error(msg)
        return False, msg
