import logging
import math
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple, Any

logger = logging.getLogger(__name__)

@dataclass
class ParticipationOfVolumePovExecutionConfig:
    enabled: bool = True
    threshold: float = 0.5
    size: int = 100

@dataclass
class POVParentOrder:
    symbol: str
    total_qty: int
    side: str                            # 'BUY', 'SELL'
    target_rate: float = 0.15            # Target 15% participation
    max_rate: float = 0.30               # Cap at 30% max participation
    min_slice_qty: int = 10
    max_slice_qty: int = 1000

@dataclass
class POVExecutionReport:
    symbol: str
    parent_qty: int
    filled_qty: int
    remaining_qty: int
    cum_market_volume: int
    realized_participation_rate: float
    last_slice_qty: int
    status: str                          # 'EXECUTING', 'COMPLETED', 'VOLUME_PAUSED'
    audit_notes: str

class ParticipationOfVolumePovExecutionEngine:
    """
    Participation of Volume (POV) execution algorithm dynamically sizing order slices
    as a target percentage of real-time market volume while enforcing participation caps and slice quantity boundaries.
    """
    def __init__(
        self,
        config: Optional[ParticipationOfVolumePovExecutionConfig] = None,
        parent_order: Optional[POVParentOrder] = None
    ):
        self.config = config or ParticipationOfVolumePovExecutionConfig()
        self.parent_order = parent_order or POVParentOrder("AAPL", 5000, "BUY", 0.15)
        self.filled_qty = 0
        self.cum_market_vol = 0
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

    def process_volume_update(
        self, market_interval_volume: int, last_price: float
    ) -> Tuple[int, POVExecutionReport]:
        """
        Processes real-time market volume update and computes target slice quantity.
        """
        if not self.config.enabled:
            return 0, POVExecutionReport(
                symbol=self.parent_order.symbol,
                parent_qty=self.parent_order.total_qty,
                filled_qty=self.filled_qty,
                remaining_qty=self.parent_order.total_qty - self.filled_qty,
                cum_market_volume=self.cum_market_vol,
                realized_participation_rate=0.0,
                last_slice_qty=0,
                status="ENGINE_DISABLED",
                audit_notes="POV Engine disabled."
            )

        remaining_qty = self.parent_order.total_qty - self.filled_qty
        if remaining_qty <= 0:
            return 0, POVExecutionReport(
                symbol=self.parent_order.symbol,
                parent_qty=self.parent_order.total_qty,
                filled_qty=self.filled_qty,
                remaining_qty=0,
                cum_market_volume=self.cum_market_vol,
                realized_participation_rate=self._calc_realized_rate(),
                last_slice_qty=0,
                status="COMPLETED",
                audit_notes="Order fully filled."
            )

        self.cum_market_vol += market_interval_volume

        # Target Slice Formula: Q_slice = target_rate / (1 - target_rate) * V_market
        rate = max(0.01, min(self.parent_order.max_rate, self.parent_order.target_rate))
        raw_slice = (rate / (1.0 - rate)) * market_interval_volume
        target_slice = int(math.floor(raw_slice))

        # Clamp slice size
        slice_qty = min(target_slice, self.parent_order.max_slice_qty, remaining_qty)

        if slice_qty < self.parent_order.min_slice_qty and remaining_qty >= self.parent_order.min_slice_qty:
            slice_qty = 0  # Accumulate market volume until minimum slice size is met

        self.filled_qty += slice_qty
        new_remaining = self.parent_order.total_qty - self.filled_qty
        realized_rate = self._calc_realized_rate()

        status = "COMPLETED" if new_remaining == 0 else ("VOLUME_PAUSED" if slice_qty == 0 else "EXECUTING")
        notes = (
            f"POV SLICE EXECUTED [{self.parent_order.symbol} {self.parent_order.side} - {status}]: "
            f"Slice Qty = {slice_qty} @ ${last_price:,.2f} (Interval Vol = {market_interval_volume}). "
            f"Filled = {self.filled_qty}/{self.parent_order.total_qty}, Realized Rate = {realized_rate:.2%} "
            f"(Target = {self.parent_order.target_rate:.2%})."
        )

        logger.info(notes)

        report = POVExecutionReport(
            symbol=self.parent_order.symbol,
            parent_qty=self.parent_order.total_qty,
            filled_qty=self.filled_qty,
            remaining_qty=new_remaining,
            cum_market_volume=self.cum_market_vol,
            realized_participation_rate=round(realized_rate, 4),
            last_slice_qty=slice_qty,
            status=status,
            audit_notes=notes
        )

        return slice_qty, report

    def _calc_realized_rate(self) -> float:
        denom = self.cum_market_vol + self.filled_qty
        if denom <= 0:
            return 0.0
        return self.filled_qty / float(denom)
