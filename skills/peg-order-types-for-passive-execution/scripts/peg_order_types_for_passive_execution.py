import logging
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple, Any

logger = logging.getLogger(__name__)

@dataclass
class PegOrderTypesForPassiveExecutionConfig:
    enabled: bool = True
    threshold: float = 0.5
    size: int = 100

@dataclass
class PegOrder:
    order_id: str
    symbol: str
    side: str                            # 'BUY', 'SELL'
    peg_type: str                        # 'PRIMARY', 'MIDPOINT', 'MARKET'
    offset: float = 0.0                  # Positive/negative offset
    limit_cap: Optional[float] = None    # Maximum cap for Buy, Minimum cap for Sell
    quantity: float = 100.0

@dataclass
class NBBOQuote:
    symbol: str
    best_bid: float
    best_ask: float

@dataclass
class PegOrderReport:
    order_id: str
    symbol: str
    side: str
    peg_type: str
    reference_price: float
    calculated_price: float
    effective_limit_price: float
    is_cap_active: bool
    status: str                          # 'REPRICED', 'CAP_BREACH_CLAMPED', 'INVALID_NBBO'
    audit_notes: str

class PegOrderTypesForPassiveExecutionEngine:
    """
    Pegged order types engine dynamically repositioning limit orders relative to Primary, Midpoint,
    and Market NBBO quotes with discretionary offsets and limit price caps.
    """
    def __init__(self, config: Optional[PegOrderTypesForPassiveExecutionConfig] = None):
        self.config = config or PegOrderTypesForPassiveExecutionConfig()
        self.state = "INITIALIZED"
        self.orders: List[Dict[str, Any]] = []

    def evaluate(self, market_data: Dict[str, Any]) -> List[Dict[str, Any]]:
        """
        Legacy evaluation method for backward compatibility.
        """
        if not self.config.enabled:
            return []

        if market_data.get("price", 0) > self.config.threshold:
            order = {"symbol": market_data.get("symbol", "UNKNOWN"), "qty": self.config.size, "type": "LIMIT"}
            self.orders.append(order)
            return [order]
        return []

    def calculate_pegged_price(
        self, order: PegOrder, nbbo: NBBOQuote
    ) -> PegOrderReport:
        """
        Calculates dynamic pegged limit price based on NBBO quote, offset, and limit cap.
        """
        if nbbo.best_bid <= 0 or nbbo.best_ask <= 0 or nbbo.best_bid >= nbbo.best_ask:
            return PegOrderReport(
                order_id=order.order_id,
                symbol=order.symbol,
                side=order.side,
                peg_type=order.peg_type,
                reference_price=0.0,
                calculated_price=0.0,
                effective_limit_price=0.0,
                is_cap_active=False,
                status="INVALID_NBBO",
                audit_notes=f"Invalid NBBO quotes: Bid=${nbbo.best_bid}, Ask=${nbbo.best_ask}"
            )

        side_upper = order.side.upper()
        peg_upper = order.peg_type.upper()

        # 1. Determine Reference Price
        if peg_upper == "PRIMARY":
            ref_price = nbbo.best_bid if side_upper == "BUY" else nbbo.best_ask
        elif peg_upper == "MIDPOINT":
            ref_price = (nbbo.best_bid + nbbo.best_ask) / 2.0
        elif peg_upper == "MARKET":
            ref_price = nbbo.best_ask if side_upper == "BUY" else nbbo.best_bid
        else:
            raise ValueError(f"Unknown peg type: {order.peg_type}")

        # 2. Apply Discretionary Offset
        if side_upper == "BUY":
            raw_price = ref_price + order.offset
        else:
            raw_price = ref_price - order.offset

        # 3. Enforce Limit Price Cap
        is_cap_active = False
        effective_price = raw_price

        if order.limit_cap is not None:
            if side_upper == "BUY" and raw_price > order.limit_cap:
                effective_price = order.limit_cap
                is_cap_active = True
            elif side_upper == "SELL" and raw_price < order.limit_cap:
                effective_price = order.limit_cap
                is_cap_active = True

        status = "CAP_BREACH_CLAMPED" if is_cap_active else "REPRICED"
        notes = (
            f"PEG ORDER REPRICED [{order.order_id} - {status}]: {peg_upper} {side_upper} on {order.symbol}. "
            f"Ref Price = ${ref_price:,.4f}, Raw Price = ${raw_price:,.4f}, "
            f"Effective Price = ${effective_price:,.4f} (Cap = {order.limit_cap})."
        )
        logger.info(notes)

        return PegOrderReport(
            order_id=order.order_id,
            symbol=order.symbol,
            side=side_upper,
            peg_type=peg_upper,
            reference_price=round(ref_price, 4),
            calculated_price=round(raw_price, 4),
            effective_limit_price=round(effective_price, 4),
            is_cap_active=is_cap_active,
            status=status,
            audit_notes=notes
        )
