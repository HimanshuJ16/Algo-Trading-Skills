import logging
from dataclasses import dataclass, field
from typing import Dict, List, Optional

logger = logging.getLogger(__name__)

@dataclass
class FuturesContractState:
    symbol: str
    expiration_date_iso: str
    days_to_expiration: int
    daily_volume: int
    open_interest: int
    last_price: float

@dataclass
class CalendarSpreadOrder:
    spread_symbol: str                  # e.g. 'ESH6-ESM6'
    front_leg_symbol: str
    next_leg_symbol: str
    front_leg_action: str               # 'SELL' for long roll, 'BUY' for short roll
    next_leg_action: str                # 'BUY' for long roll, 'SELL' for short roll
    quantity: int
    spread_price_diff: float            # P_next - P_front
    term_structure: str                 # 'CONTANGO' (P_next > P_front) or 'BACKWARDATION'

@dataclass
class FuturesRollAuditReport:
    root_symbol: str                    # e.g. 'ES'
    front_contract: FuturesContractState
    next_contract: FuturesContractState
    is_roll_triggered: bool
    trigger_reason: str                 # 'VOLUME_CROSSOVER', 'DAYS_TO_EXPIRATION_THRESHOLD', 'NONE'
    calendar_spread_order: Optional[CalendarSpreadOrder]
    status: str                         # 'ROLL_SIGNAL_ACTIVE', 'HOLD_FRONT_CONTRACT'
    audit_notes: str

class FuturesContractRollEngine:
    """
    Quantitative execution engine for automating futures contract roll decisions via volume/open interest crossover
    or days-to-expiration rules, constructing atomic calendar spread orders.
    """
    def __init__(
        self,
        min_days_to_expiration: int = 5,
        enable_volume_crossover: bool = True
    ):
        self.min_days_to_expiration = min_days_to_expiration
        self.enable_volume_crossover = enable_volume_crossover

    def evaluate_and_build_roll_order(
        self,
        root_symbol: str,
        position_side: str,             # 'LONG' or 'SHORT'
        position_qty: int,
        front_contract: FuturesContractState,
        next_contract: FuturesContractState
    ) -> FuturesRollAuditReport:
        """
        Audits contract liquidity & expiration to trigger roll signals and build atomic calendar spread orders.
        """
        if position_qty <= 0:
            raise ValueError("Position quantity must be > 0.")
        if position_side.upper() not in ("LONG", "SHORT"):
            raise ValueError("Position side must be 'LONG' or 'SHORT'.")

        is_volume_crossover = self.enable_volume_crossover and (next_contract.daily_volume > front_contract.daily_volume)
        is_dbe_reached = (front_contract.days_to_expiration <= self.min_days_to_expiration)

        is_roll_triggered = is_volume_crossover or is_dbe_reached

        trigger_reason = "NONE"
        if is_volume_crossover and is_dbe_reached:
            trigger_reason = "VOLUME_CROSSOVER_AND_DBE"
        elif is_volume_crossover:
            trigger_reason = "VOLUME_CROSSOVER"
        elif is_dbe_reached:
            trigger_reason = "DAYS_TO_EXPIRATION_THRESHOLD"

        if not is_roll_triggered:
            notes = (
                f"FUTURES ROLL HOLD [{root_symbol}]: Front contract {front_contract.symbol} is active. "
                f"DBE={front_contract.days_to_expiration}d > {self.min_days_to_expiration}d threshold, "
                f"Front Vol ({front_contract.daily_volume:,}) > Next Vol ({next_contract.daily_volume:,})."
            )
            logger.info(notes)
            return FuturesRollAuditReport(
                root_symbol=root_symbol,
                front_contract=front_contract,
                next_contract=next_contract,
                is_roll_triggered=False,
                trigger_reason="NONE",
                calendar_spread_order=None,
                status="HOLD_FRONT_CONTRACT",
                audit_notes=notes
            )

        # Build Atomic Calendar Spread Order
        spread_diff = round(next_contract.last_price - front_contract.last_price, 4)
        term_struct = "CONTANGO" if spread_diff > 0 else ("BACKWARDATION" if spread_diff < 0 else "FLAT")

        side_clean = position_side.upper()
        if side_clean == "LONG":
            front_act, next_act = "SELL", "BUY"
        else:
            front_act, next_act = "BUY", "SELL"

        spread_symbol = f"{front_contract.symbol}-{next_contract.symbol}"

        spread_order = CalendarSpreadOrder(
            spread_symbol=spread_symbol,
            front_leg_symbol=front_contract.symbol,
            next_leg_symbol=next_contract.symbol,
            front_leg_action=front_act,
            next_leg_action=next_act,
            quantity=position_qty,
            spread_price_diff=spread_diff,
            term_structure=term_struct
        )

        notes = (
            f"FUTURES ROLL SIGNAL ACTIVE [{root_symbol} - Trigger: {trigger_reason}]: "
            f"Rolling {side_clean} {position_qty} contracts from {front_contract.symbol} to {next_contract.symbol}. "
            f"Term Structure: {term_struct} (Spread Basis = {spread_diff:+.4f}). "
            f"Spread Order: {front_act} {front_contract.symbol} / {next_act} {next_contract.symbol}."
        )
        logger.info(notes)

        return FuturesRollAuditReport(
            root_symbol=root_symbol,
            front_contract=front_contract,
            next_contract=next_contract,
            is_roll_triggered=True,
            trigger_reason=trigger_reason,
            calendar_spread_order=spread_order,
            status="ROLL_SIGNAL_ACTIVE",
            audit_notes=notes
        )
