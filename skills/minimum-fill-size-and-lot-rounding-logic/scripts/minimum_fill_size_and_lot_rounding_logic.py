import math
import logging
from dataclasses import dataclass, field
from typing import Dict, List, Optional

logger = logging.getLogger(__name__)

@dataclass
class OrderRoundingConfig:
    symbol: str
    lot_size: float = 100.0              # Board lot size (e.g. 100 shares)
    min_qty: float = 100.0               # Minimum fill quantity (MinQty / FIX Tag 110)
    rounding_mode: str = "FLOOR"         # 'FLOOR', 'CEIL', 'ROUND_NEAREST'
    allow_odd_lots: bool = False         # Allow odd-lot executions if True

@dataclass
class RawOrderRequest:
    order_id: str
    symbol: str
    side: str                            # 'BUY' or 'SELL'
    raw_quantity: float                  # Unrounded target quantity from strategy
    limit_price: float
    available_liquidity_depth: float = 10000.0 # Depth available at limit price

@dataclass
class OrderRoundingReport:
    order_id: str
    symbol: str
    raw_quantity: float
    rounded_quantity: float
    fix_tag_110_min_qty: float
    fix_tag_1089_match_increment: float
    is_odd_lot: bool
    is_compliant: bool
    status: str                          # 'LOT_ROUNDING_SUCCESS', 'ODD_LOT_ADJUSTED_TO_ROUND_LOT', 'ORDER_REJECTED_BELOW_MIN_QTY', 'MIN_QTY_DEPTH_UNSATISFIED'
    audit_notes: str

class MinimumFillSizeAndLotRoundingEngine:
    """
    Execution order lot rounding and minimum fill size engine enforcing board lot step sizes,
    FIX Tag 110 (MinQty), FIX Tag 1089 (MatchIncrement), and odd-lot penalty prevention.
    """
    def __init__(self):
        pass

    def apply_lot_rounding_and_min_qty_rules(
        self,
        config: OrderRoundingConfig,
        order: RawOrderRequest
    ) -> OrderRoundingReport:
        """
        Rounds order quantity to board lot multiples, audits FIX Tag 110 (MinQty) and Tag 1089 (MatchIncrement).
        """
        if config.lot_size <= 0.0 or config.min_qty <= 0.0:
            raise ValueError("Lot size and minimum quantity must be strictly positive.")
        if order.raw_quantity <= 0.0:
            raise ValueError("Raw order quantity must be positive.")

        raw_q = order.raw_quantity
        lot_s = config.lot_size

        # 1. Rounding Mode Calculation
        if config.rounding_mode == "FLOOR":
            rounded_q = math.floor(raw_q / lot_s) * lot_s
        elif config.rounding_mode == "CEIL":
            rounded_q = math.ceil(raw_q / lot_s) * lot_s
        elif config.rounding_mode == "ROUND_NEAREST":
            rounded_q = round(raw_q / lot_s) * lot_s
        else:
            raise ValueError(f"Unsupported rounding mode '{config.rounding_mode}'.")

        rounded_q = round(rounded_q, 4)
        is_odd_lot = (raw_q % lot_s != 0)

        # 2. Minimum Quantity (MinQty / FIX Tag 110) Audit
        if rounded_q < config.min_qty:
            status = "ORDER_REJECTED_BELOW_MIN_QTY"
            notes = (
                f"ORDER REJECTED [{order.symbol}]: Rounded quantity {rounded_q:.2f} "
                f"is below minimum fill size ({config.min_qty:.2f} shares). Order canceled."
            )
            logger.warning(notes)
            return OrderRoundingReport(
                order_id=order.order_id,
                symbol=order.symbol,
                raw_quantity=raw_q,
                rounded_quantity=0.0,
                fix_tag_110_min_qty=config.min_qty,
                fix_tag_1089_match_increment=config.lot_size,
                is_odd_lot=is_odd_lot,
                is_compliant=False,
                status=status,
                audit_notes=notes
            )

        # 3. Liquidity Depth vs MinQty Audit
        if order.available_liquidity_depth < config.min_qty:
            status = "MIN_QTY_DEPTH_UNSATISFIED"
            notes = (
                f"MIN QTY WARNING [{order.symbol}]: Order MinQty ({config.min_qty:.2f}) "
                f"exceeds available order book depth ({order.available_liquidity_depth:.2f}). Fill unlikely."
            )
            logger.warning(notes)

        # 4. Odd-Lot Policy Audit
        if is_odd_lot and not config.allow_odd_lots:
            status = "ODD_LOT_ADJUSTED_TO_ROUND_LOT"
            notes = (
                f"LOT ROUNDING [{order.symbol}]: Raw quantity {raw_q:.2f} (Odd Lot) adjusted "
                f"to Board Lot {rounded_q:.2f} under mode '{config.rounding_mode}' (Lot Size = {lot_s:.0f})."
            )
            logger.info(notes)
        else:
            status = "LOT_ROUNDING_SUCCESS"
            notes = f"LOT ROUNDING OK [{order.symbol}]: Order quantity {rounded_q:.2f} meets board lot ({lot_s:.0f}) & MinQty ({config.min_qty:.0f})."
            logger.info(notes)

        return OrderRoundingReport(
            order_id=order.order_id,
            symbol=order.symbol,
            raw_quantity=raw_q,
            rounded_quantity=rounded_q,
            fix_tag_110_min_qty=config.min_qty,
            fix_tag_1089_match_increment=config.lot_size,
            is_odd_lot=is_odd_lot,
            is_compliant=True,
            status=status,
            audit_notes=notes
        )
