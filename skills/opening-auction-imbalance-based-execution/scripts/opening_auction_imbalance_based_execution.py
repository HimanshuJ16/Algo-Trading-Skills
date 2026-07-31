import logging
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)

@dataclass
class OpeningAuctionImbalanceBasedExecutionConfig:
    enabled: bool = True
    threshold: float = 0.5               # Legacy threshold / price parameter
    size: int = 100                      # Default order quantity
    imbalance_ratio_threshold: float = 0.20 # 20% imbalance ratio trigger
    min_imbalance_qty: int = 10000       # Minimum imbalance volume trigger
    cutoff_seconds_to_open: float = 120.0 # 2-minute cutoff before market open (09:28 EST)

@dataclass
class AuctionImbalanceData:
    symbol: str
    paired_qty: int
    imbalance_qty: int
    imbalance_side: str                  # 'B' (Buy Imbalance), 'S' (Sell Imbalance), 'N' (No Imbalance)
    far_price: float
    near_price: float
    ref_price: float
    seconds_to_open: float

@dataclass
class AuctionExecutionReport:
    symbol: str
    imbalance_ratio: float
    order_generated: Optional[Dict[str, Any]]
    status: str                          # 'ORDER_GENERATED', 'NO_IMBALANCE_TRIGGER', 'CUTOFF_EXCEEDED', 'ENGINE_DISABLED'
    audit_notes: str

class OpeningAuctionImbalanceBasedExecutionEngine:
    """
    Opening auction imbalance execution engine evaluating Nasdaq NOII and NYSE opening cross feeds,
    computing imbalance ratios, enforcing cutoff windows, and routing contra-side MOO/LOO orders.
    """
    def __init__(self, config: OpeningAuctionImbalanceBasedExecutionConfig):
        self.config = config
        self.state = "INITIALIZED"
        self.orders: List[Dict[str, Any]] = []

    def evaluate(self, market_data: Dict[str, Any]) -> List[Dict[str, Any]]:
        """
        Legacy simple evaluation method for backward compatibility.
        """
        if not self.config.enabled:
            return []

        if market_data.get("price", 0) > self.config.threshold:
            order = {
                "symbol": market_data.get("symbol", "UNKNOWN"),
                "qty": self.config.size,
                "type": "LIMIT",
                "price": market_data.get("price", 0)
            }
            self.orders.append(order)
            return [order]
        return []

    def process_auction_imbalance(
        self, imbalance: AuctionImbalanceData
    ) -> AuctionExecutionReport:
        """
        Processes real-time opening auction imbalance feed, computes imbalance ratio,
        checks exchange cutoff limits, and generates contra-side MOO/LOO orders.
        """
        if not self.config.enabled:
            return AuctionExecutionReport(
                symbol=imbalance.symbol,
                imbalance_ratio=0.0,
                order_generated=None,
                status="ENGINE_DISABLED",
                audit_notes="Execution engine is currently disabled."
            )

        # 1. Enforce Exchange Cutoff Window Lockout
        if imbalance.seconds_to_open < self.config.cutoff_seconds_to_open:
            notes = (
                f"AUCTION CUTOFF EXCEEDED: {imbalance.seconds_to_open:.1f}s to open is less than "
                f"cutoff limit {self.config.cutoff_seconds_to_open:.1f}s."
            )
            logger.warning(notes)
            return AuctionExecutionReport(
                symbol=imbalance.symbol,
                imbalance_ratio=0.0,
                order_generated=None,
                status="CUTOFF_EXCEEDED",
                audit_notes=notes
            )

        # 2. Compute Imbalance Ratio
        total_vol = imbalance.paired_qty + imbalance.imbalance_qty
        if total_vol <= 0:
            imb_ratio = 0.0
        else:
            imb_ratio = imbalance.imbalance_qty / float(total_vol)

        # 3. Check Imbalance Thresholds
        is_ratio_ok = imb_ratio >= self.config.imbalance_ratio_threshold
        is_qty_ok = imbalance.imbalance_qty >= self.config.min_imbalance_qty

        if not (is_ratio_ok and is_qty_ok) or imbalance.imbalance_side not in ("B", "S"):
            notes = (
                f"NO IMBALANCE TRIGGER [{imbalance.symbol}]: Ratio = {imb_ratio:.2%} "
                f"(min {self.config.imbalance_ratio_threshold:.2%}), Qty = {imbalance.imbalance_qty} "
                f"(min {self.config.min_imbalance_qty})."
            )
            logger.info(notes)
            return AuctionExecutionReport(
                symbol=imbalance.symbol,
                imbalance_ratio=round(imb_ratio, 4),
                order_generated=None,
                status="NO_IMBALANCE_TRIGGER",
                audit_notes=notes
            )

        # 4. Generate Contra-Side MOO Order
        # If Buy Imbalance ('B') -> SELL MOO to provide contra-side liquidity
        # If Sell Imbalance ('S') -> BUY MOO
        contra_side = "SELL" if imbalance.imbalance_side == "B" else "BUY"
        order_qty = min(self.config.size, int(imbalance.imbalance_qty * 0.10))
        order_qty = max(order_qty, 100)

        order_dict = {
            "symbol": imbalance.symbol,
            "side": contra_side,
            "qty": order_qty,
            "type": "MOO",                     # Market-On-Open
            "ref_price": imbalance.ref_price,
            "near_price": imbalance.near_price,
            "far_price": imbalance.far_price
        }
        self.orders.append(order_dict)

        notes = (
            f"AUCTION ORDER GENERATED [{imbalance.symbol}]: {contra_side} {order_qty} MOO "
            f"against {imbalance.imbalance_side} Imbalance ({imb_ratio:.2%} ratio, {imbalance.imbalance_qty} shares)."
        )
        logger.info(notes)

        return AuctionExecutionReport(
            symbol=imbalance.symbol,
            imbalance_ratio=round(imb_ratio, 4),
            order_generated=order_dict,
            status="ORDER_GENERATED",
            audit_notes=notes
        )
