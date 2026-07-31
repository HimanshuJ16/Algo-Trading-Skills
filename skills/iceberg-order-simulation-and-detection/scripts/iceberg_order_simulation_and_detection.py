import logging
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)

@dataclass
class TradePrint:
    trade_id: str
    price: float
    quantity: int
    aggressor_side: str                 # 'BUY' or 'SELL'
    timestamp_nanos: int

@dataclass
class Level2DepthSnapshot:
    price: float
    side: str                           # 'BID' or 'ASK'
    displayed_quantity: int
    timestamp_nanos: int

@dataclass
class IcebergDetectionReport:
    symbol: str
    detected_price: float
    iceberg_side: str                   # 'BUY' (Hidden Bid) or 'SELL' (Hidden Ask)
    initial_display_quantity: int
    cumulative_traded_quantity: int
    estimated_hidden_quantity: int
    refill_count: int
    confidence_score: float             # 0.0 to 1.0
    signal_classification: str          # 'BULLISH_HIDDEN_BUY', 'BEARISH_HIDDEN_SELL', 'NO_ICEBERG_DETECTED'
    audit_notes: str

class IcebergDetectorEngine:
    """
    Quantitative market microstructure engine for detecting hidden institutional Iceberg orders
    by tracking trade volume vs visible book depth discrepancies and repeated price-level refills.
    """
    def __init__(
        self,
        symbol: str = "AAPL",
        min_volume_ratio: float = 1.5,   # Traded vol >= 1.5x initial display depth
        min_refill_count: int = 2
    ):
        self.symbol = symbol
        self.min_volume_ratio = min_volume_ratio
        self.min_refill_count = min_refill_count

        # Price Level Trackers: price -> {initial_display, cumulative_traded, refill_count, side}
        self.price_trackers: Dict[float, Dict[str, Any]] = {}

    def process_l2_depth(self, snapshot: Level2DepthSnapshot) -> None:
        """
        Updates resting depth tracker at price level.
        """
        p = snapshot.price
        if p not in self.price_trackers:
            self.price_trackers[p] = {
                "initial_display": snapshot.displayed_quantity,
                "current_display": snapshot.displayed_quantity,
                "cumulative_traded": 0,
                "refill_count": 0,
                "side": snapshot.side
            }
        else:
            tracker = self.price_trackers[p]
            # Check if depth refilled (current display restored after being consumed)
            if snapshot.displayed_quantity > tracker["current_display"] and tracker["cumulative_traded"] > 0:
                tracker["refill_count"] += 1
            tracker["current_display"] = snapshot.displayed_quantity

    def process_trade_print(self, trade: TradePrint) -> Optional[IcebergDetectionReport]:
        """
        Processes trade execution, updates cumulative volume, and audits for iceberg presence.
        """
        p = trade.price
        if p not in self.price_trackers:
            # Register fallback tracker
            self.price_trackers[p] = {
                "initial_display": trade.quantity,
                "current_display": trade.quantity,
                "cumulative_traded": trade.quantity,
                "refill_count": 0,
                "side": "BID" if trade.aggressor_side == "SELL" else "ASK"
            }
        else:
            tracker = self.price_trackers[p]
            tracker["cumulative_traded"] += trade.quantity

        tracker = self.price_trackers[p]
        initial_q = tracker["initial_display"]
        cum_q = tracker["cumulative_traded"]
        refill_c = tracker["refill_count"]

        # Audit Detection Criteria: Cumulative Vol >= 1.5x Initial Depth and Refills >= min_refill_count
        vol_ratio = cum_q / float(max(1, initial_q))

        if vol_ratio >= self.min_volume_ratio and refill_c >= self.min_refill_count:
            estimated_hidden = max(0, cum_q - initial_q)

            # Side Classification:
            # If trades hitting BID (aggressor SELL) -> Hidden Buy Iceberg (BULLISH)
            # If trades hitting ASK (aggressor BUY) -> Hidden Sell Iceberg (BEARISH)
            is_buy_iceberg = (tracker["side"] == "BID" or trade.aggressor_side.upper() == "SELL")
            iceberg_side = "BUY" if is_buy_iceberg else "SELL"
            signal = "BULLISH_HIDDEN_BUY" if is_buy_iceberg else "BEARISH_HIDDEN_SELL"

            confidence = min(1.0, round(0.5 + (vol_ratio * 0.1) + (refill_c * 0.1), 2))

            notes = (
                f"ICEBERG DETECTED [{self.symbol} @ ${p:.2f}]: {signal} ({iceberg_side} Iceberg). "
                f"Initial Depth = {initial_q:,}, Cumulative Traded = {cum_q:,} ({vol_ratio:.1f}x ratio). "
                f"Estimated Hidden Qty = {estimated_hidden:,}, Refills = {refill_c}, Confidence = {confidence*100:.0f}%."
            )
            logger.info(notes)

            return IcebergDetectionReport(
                symbol=self.symbol,
                detected_price=p,
                iceberg_side=iceberg_side,
                initial_display_quantity=initial_q,
                cumulative_traded_quantity=cum_q,
                estimated_hidden_quantity=estimated_hidden,
                refill_count=refill_c,
                confidence_score=confidence,
                signal_classification=signal,
                audit_notes=notes
            )

        return None
