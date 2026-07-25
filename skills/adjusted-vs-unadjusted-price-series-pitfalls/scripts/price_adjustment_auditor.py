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
    ratio: float      # e.g. 2.0 for 2:1 split, or dividend amount


@dataclass
class DiscontinuityEvent:
    date: str
    prev_close: float
    next_open: float
    pct_change: float
    likely_cause: str
    volume_consistent: bool


@dataclass
class AdjustmentAuditReport:
    symbol: str
    total_bars: int
    discontinuities_found: List[DiscontinuityEvent]
    detected_adjustment_type: str
    has_look_ahead_bias_risk: bool
    is_consistent: bool
    message: str


class PriceAdjustmentAuditor:
    """
    Detects corporate action discontinuities in price series, classifies adjustment type,
    ensures volume scaling matches price splits, and flags look-ahead bias risks.
    """

    def __init__(self, discontinuity_threshold_pct: float = 30.0):
        self.threshold_pct = discontinuity_threshold_pct

    def detect_discontinuities(
        self,
        symbol: str,
        closes: List[float],
        volumes: List[float],
        dates: List[str],
        known_actions: Optional[List[CorporateAction]] = None,
    ) -> AdjustmentAuditReport:
        
        if len(closes) != len(volumes) or len(closes) != len(dates):
            raise ValueError("Closes, volumes, and dates lists must be of equal length.")

        action_map: Dict[str, CorporateAction] = {}
        if known_actions:
            action_map = {a.date: a for a in known_actions}

        discontinuities: List[DiscontinuityEvent] = []
        has_look_ahead_bias = False

        for i in range(1, len(closes)):
            if closes[i - 1] <= 0:
                continue
            
            pct_change = ((closes[i] - closes[i - 1]) / closes[i - 1]) * 100.0

            if abs(pct_change) >= self.threshold_pct:
                date = dates[i]
                action = action_map.get(date)
                
                # Check if volume scaled inversely to price drop
                vol_ratio = volumes[i] / volumes[i-1] if volumes[i-1] > 0 else 1.0
                price_ratio = closes[i-1] / closes[i] if closes[i] > 0 else 1.0
                
                # A split should cause volume to spike by the exact same ratio the price dropped
                vol_consistent = abs(vol_ratio - price_ratio) < 0.5 # 50% tolerance for natural trading variation

                if action:
                    cause = f"{action.action_type} (ratio={action.ratio})"
                    if action.action_type == "DIVIDEND":
                        # If a massive price drop matches a dividend, the series is likely 
                        # backward-adjusted for dividends, introducing look-ahead bias.
                        has_look_ahead_bias = True
                else:
                    cause = "UNKNOWN — possible unadjusted split or data error"

                discontinuities.append(DiscontinuityEvent(
                    date=date,
                    prev_close=closes[i - 1],
                    next_open=closes[i],
                    pct_change=round(pct_change, 2),
                    likely_cause=cause,
                    volume_consistent=vol_consistent
                ))

        if discontinuities:
            has_known = any(d.likely_cause.startswith("SPLIT") or d.likely_cause.startswith("DIVIDEND") for d in discontinuities)
            adj_type = "UNADJUSTED" if has_known else "UNKNOWN"
            msg = f"DISCONTINUITY DETECTED for '{symbol}': {len(discontinuities)} jumps >= {self.threshold_pct}%. Series likely {adj_type}."
            logger.warning(msg)
        else:
            adj_type = "ADJUSTED"
            msg = f"No discontinuities detected for '{symbol}'. Series appears fully ADJUSTED."

        return AdjustmentAuditReport(
            symbol=symbol,
            total_bars=len(closes),
            discontinuities_found=discontinuities,
            detected_adjustment_type=adj_type,
            has_look_ahead_bias_risk=has_look_ahead_bias,
            is_consistent=(len(discontinuities) == 0),
            message=msg,
        )

    def apply_split_adjustment(
        self,
        closes: List[float],
        volumes: List[float],
        split_index: int,
        split_ratio: float,
    ) -> Tuple[List[float], List[float]]:
        """
        Backward-adjusts prices (divides by ratio) and volumes (multiplies by ratio) 
        before the split date to maintain continuous notional value.
        """
        adj_closes = list(closes)
        adj_volumes = list(volumes)
        
        for i in range(split_index):
            adj_closes[i] = round(adj_closes[i] / split_ratio, 4)
            adj_volumes[i] = round(adj_volumes[i] * split_ratio, 4)
            
        return adj_closes, adj_volumes

    def validate_universe_consistency(
        self,
        reports: List[AdjustmentAuditReport],
    ) -> Tuple[bool, str]:
        types = set(r.detected_adjustment_type for r in reports)
        if len(types) <= 1:
            return True, f"Universe consistent: all symbols are {types.pop() if types else 'UNKNOWN'}."
        msg = f"MIXED ADJUSTMENT TYPES in universe: {types}. This will corrupt cross-asset signals."
        logger.error(msg)
        return False, msg
