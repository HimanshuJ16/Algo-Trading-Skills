import math
import logging
from dataclasses import dataclass, field
from typing import Dict, List, Optional

logger = logging.getLogger(__name__)

@dataclass
class FuturesOrderBookState:
    symbol: str
    days_to_expiration: int
    bid_ask_spread_ticks: float
    top_of_book_depth_qty: int
    baseline_average_depth_qty: int
    is_quadruple_witching_week: bool = False

@dataclass
class FuturesExpiryRiskReport:
    symbol: str
    days_to_expiration: int
    is_market_orders_allowed: bool
    size_haircut_factor: float          # e.g. 0.50 for 50% haircut, 1.0 for normal
    adjusted_max_order_qty: int
    is_new_entry_allowed: bool
    is_mandatory_roll_required: bool
    status: str                         # 'NORMAL_EXECUTION', 'EXPIRY_WEEK_RESTRICTED', 'MANDATORY_ROLL_REQUIRED'
    audit_notes: str

class FuturesExpiryRiskHandlerEngine:
    """
    Microstructure execution engine for auditing futures expiry week liquidity fragmentation, wider spreads,
    and Quad-Witching volatility, enforcing position size haircuts and roll mandates.
    """
    def __init__(
        self,
        max_spread_ticks_threshold: float = 2.0,
        min_depth_ratio_threshold: float = 0.30, # < 30% baseline triggers haircut
        mandatory_roll_dbe_cutoff: int = 2        # <= 2 days forces roll
    ):
        self.max_spread_ticks_threshold = max_spread_ticks_threshold
        self.min_depth_ratio_threshold = min_depth_ratio_threshold
        self.mandatory_roll_dbe_cutoff = mandatory_roll_dbe_cutoff

    def audit_expiry_execution_safeguards(
        self,
        order_book: FuturesOrderBookState,
        base_order_qty: int
    ) -> FuturesExpiryRiskReport:
        """
        Audits order book microstructure state during expiry week and returns execution limits & haircuts.
        """
        if base_order_qty <= 0:
            raise ValueError("Base order quantity must be > 0.")

        is_spread_wide = (order_book.bid_ask_spread_ticks > self.max_spread_ticks_threshold)
        
        depth_ratio = (order_book.top_of_book_depth_qty / float(max(1, order_book.baseline_average_depth_qty)))
        is_depth_thinned = (depth_ratio < self.min_depth_ratio_threshold)

        is_dbe_critical = (order_book.days_to_expiration <= self.mandatory_roll_dbe_cutoff)

        # 1. Market order prohibition if wide spread
        market_orders_allowed = not is_spread_wide

        # 2. Size Haircut Factor
        haircut_factor = 1.0
        if is_depth_thinned or order_book.is_quadruple_witching_week:
            haircut_factor = 0.50 # 50% haircut

        adjusted_qty = math.floor(base_order_qty * haircut_factor)

        # 3. New Entry Ban & Mandatory Roll
        new_entry_allowed = not is_dbe_critical
        mandatory_roll = is_dbe_critical

        if mandatory_roll:
            status = "MANDATORY_ROLL_REQUIRED"
            notes = (
                f"FUTURES EXPIRY MANDATORY ROLL [{order_book.symbol}]: DBE = {order_book.days_to_expiration}d <= {self.mandatory_roll_dbe_cutoff}d cutoff. "
                f"New entries BLOCKED. Immediate roll to next-month contract required."
            )
            logger.warning(notes)
        elif is_spread_wide or is_depth_thinned or order_book.is_quadruple_witching_week:
            status = "EXPIRY_WEEK_RESTRICTED"
            notes = (
                f"FUTURES EXPIRY RESTRICTED [{order_book.symbol} - QuadWitching={order_book.is_quadruple_witching_week}]: "
                f"Spread = {order_book.bid_ask_spread_ticks:.1f} ticks (Market Orders {'BLOCKED' if is_spread_wide else 'OK'}), "
                f"Depth = {order_book.top_of_book_depth_qty:,} ({depth_ratio*100:.0f}% baseline). "
                f"Haircut applied: Order Qty reduced from {base_order_qty} -> {adjusted_qty} ({haircut_factor*100:.0f}%)."
            )
            logger.warning(notes)
        else:
            status = "NORMAL_EXECUTION"
            notes = f"FUTURES EXPIRY NORMAL [{order_book.symbol}]: Order book liquid. Full size {base_order_qty} allowed."
            logger.info(notes)

        return FuturesExpiryRiskReport(
            symbol=order_book.symbol,
            days_to_expiration=order_book.days_to_expiration,
            is_market_orders_allowed=market_orders_allowed,
            size_haircut_factor=haircut_factor,
            adjusted_max_order_qty=adjusted_qty,
            is_new_entry_allowed=new_entry_allowed,
            is_mandatory_roll_required=mandatory_roll,
            status=status,
            audit_notes=notes
        )
