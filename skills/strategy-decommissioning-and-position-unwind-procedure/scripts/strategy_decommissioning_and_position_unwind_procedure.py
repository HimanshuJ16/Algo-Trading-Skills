import logging
import time
from dataclasses import dataclass, field
from enum import Enum
from typing import Dict, List, Optional

logger = logging.getLogger(__name__)

@dataclass
class Config:
    """Legacy Config container for backward compatibility."""
    enabled: bool = True

class Engine:
    """Legacy Engine class for backward compatibility."""
    def __init__(self, config: Config):
        self.config = config

    def run(self) -> bool:
        return self.config.enabled

class DecommissionState(str, Enum):
    ACTIVE = "ACTIVE"
    DECOMMISSION_INITIATED = "DECOMMISSION_INITIATED"
    ORDER_ENTRY_BLOCKED = "ORDER_ENTRY_BLOCKED"
    UNWIND_IN_PROGRESS = "UNWIND_IN_PROGRESS"
    FULLY_UNWOUND = "FULLY_UNWOUND"
    DECOMMISSION_COMPLETE = "DECOMMISSION_COMPLETE"

@dataclass
class StrategyPosition:
    symbol: str
    quantity: float                        # Positive for Long, Negative for Short
    market_price: float
    avg_daily_volume: float
    max_adv_slice_pct: float = 10.0        # Max 10% ADV per liquidation slice

@dataclass
class LiquidationSliceOrder:
    slice_id: str
    symbol: str
    side: str                              # 'SELL' for long unwind, 'BUY' for short unwind
    slice_quantity: float
    remaining_position_quantity: float
    target_price: float

@dataclass
class UnwindProcedureReport:
    strategy_id: str
    state: DecommissionState
    initial_total_notional_usd: float
    remaining_notional_usd: float
    liquidated_notional_usd: float
    total_realized_pnl_usd: float
    slices_generated: List[LiquidationSliceOrder]
    new_entries_allowed: bool
    audit_notes: str

class StrategyDecommissioningEngine:
    """
    Production-grade Strategy Decommissioning & Position Unwind Engine managing
    hard entry signal blocks, orderly VWAP/TWAP position liquidation slicing, and capital return to treasury.
    """
    def __init__(self, strategy_id: str):
        self.strategy_id = strategy_id
        self.state = DecommissionState.ACTIVE
        self.positions: Dict[str, StrategyPosition] = {}
        self.liquidated_pnl_usd = 0.0

    def load_positions(self, positions: List[StrategyPosition]):
        for p in positions:
            self.positions[p.symbol] = p

    def initiate_decommissioning(self, reason: str = "Performance Decay") -> UnwindProcedureReport:
        """
        Initiates the decommissioning workflow:
        1. Hard-blocks all new entry orders.
        2. Transitions state to DECOMMISSION_INITIATED and ORDER_ENTRY_BLOCKED.
        """
        self.state = DecommissionState.ORDER_ENTRY_BLOCKED
        notes = f"DECOMMISSION INITIATED [{self.strategy_id}]: New entry orders BLOCKED. Reason: {reason}."
        logger.warning(notes)

        return self._generate_unwind_report([], notes)

    def generate_unwind_liquidation_slices(self) -> UnwindProcedureReport:
        """
        Generates orderly liquidation slices for active positions constrained by ADV participation caps.
        """
        if self.state == DecommissionState.ACTIVE:
            raise RuntimeError("Cannot generate liquidation slices while strategy is in ACTIVE state. Call initiate_decommissioning first.")

        self.state = DecommissionState.UNWIND_IN_PROGRESS
        slices: List[LiquidationSliceOrder] = []
        slice_idx = 1

        for symbol, pos in list(self.positions.items()):
            if abs(pos.quantity) <= 1e-6:
                continue

            is_long = pos.quantity > 0
            side = "SELL" if is_long else "BUY"
            abs_qty = abs(pos.quantity)

            # Slicing limit: Min of remaining quantity or max ADV slice limit
            max_slice_qty = (pos.max_adv_slice_pct / 100.0) * pos.avg_daily_volume
            slice_qty = min(abs_qty, max_slice_qty)
            rem_qty = abs_qty - slice_qty

            slices.append(LiquidationSliceOrder(
                slice_id=f"SLICE_{self.strategy_id}_{symbol}_{slice_idx}",
                symbol=symbol,
                side=side,
                slice_quantity=slice_qty,
                remaining_position_quantity=rem_qty * (1 if is_long else -1),
                target_price=pos.market_price
            ))
            slice_idx += 1

        if not slices:
            self.state = DecommissionState.FULLY_UNWOUND
            notes = f"UNWIND COMPLETE [{self.strategy_id}]: All positions fully liquidated. Ready for treasury capital return."
        else:
            notes = f"UNWIND IN PROGRESS [{self.strategy_id}]: Generated {len(slices)} liquidation slices."

        logger.info(notes)
        return self._generate_unwind_report(slices, notes)

    def record_slice_execution(self, slice_id: str, symbol: str, executed_qty: float, executed_price: float, realized_pnl: float):
        """Records executed slice fills and updates position inventory."""
        pos = self.positions.get(symbol)
        if pos:
            if pos.quantity > 0:
                pos.quantity = max(0.0, pos.quantity - executed_qty)
            else:
                pos.quantity = min(0.0, pos.quantity + executed_qty)

            self.liquidated_pnl_usd += realized_pnl

            if abs(pos.quantity) <= 1e-6:
                del self.positions[symbol]

        # Check if fully unwound
        if not self.positions:
            self.state = DecommissionState.FULLY_UNWOUND
            logger.info(f"STRATEGY FULLY UNWOUND [{self.strategy_id}]: All positions 0. Realized PnL = ${self.liquidated_pnl_usd:,.2f}.")

    def _generate_unwind_report(self, slices: List[LiquidationSliceOrder], notes: str) -> UnwindProcedureReport:
        rem_notional = sum(abs(p.quantity) * p.market_price for p in self.positions.values())
        initial_notional = rem_notional + sum(s.slice_quantity * s.target_price for s in slices)

        return UnwindProcedureReport(
            strategy_id=self.strategy_id,
            state=self.state,
            initial_total_notional_usd=round(initial_notional, 2),
            remaining_notional_usd=round(rem_notional, 2),
            liquidated_notional_usd=round(initial_notional - rem_notional, 2),
            total_realized_pnl_usd=round(self.liquidated_pnl_usd, 2),
            slices_generated=slices,
            new_entries_allowed=False,
            audit_notes=notes
        )
