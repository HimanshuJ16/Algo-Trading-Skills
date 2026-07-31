import math
import logging
from dataclasses import dataclass, field
from typing import Dict, List, Optional

logger = logging.getLogger(__name__)

@dataclass
class HkexOrderPayload:
    raw_stock_code: str                 # e.g. '700' or '00700' or '80700'
    side: str                           # 'BUY' or 'SELL'
    order_type: str                     # 'LIMIT', 'ENHANCED_LIMIT', 'SPECIAL_LIMIT'
    price: float                        # e.g. 300.20
    quantity: int                       # e.g. 200
    board_lot_size: int = 100           # e.g. 100 for Tencent 00700
    currency: str = "HKD"               # 'HKD' or 'RMB'

@dataclass
class HkexOrionOrderReport:
    formatted_stock_code: str           # 5-digit zero-padded (e.g. '00700')
    currency: str
    price: float
    applicable_tick_size: float         # e.g. 0.20 for price between $200-$500
    is_price_tick_valid: bool
    quantity: int
    board_lot_size: int
    is_board_lot_multiple: bool
    status: str                         # 'ORDER_VALIDATED', 'INVALID_TICK_SIZE', 'INVALID_BOARD_LOT'
    audit_notes: str

class HkexOrionApiEngine:
    """
    Quantitative market gateway engine for Hong Kong Exchange (HKEX Orion OMD-C/OMD-D API),
    enforcing 5-digit stock codes, HKEX Spread Table tick sizes, and Board Lot sizing.
    """
    def __init__(self):
        pass

    def format_hkex_stock_code(self, raw_code: str) -> str:
        """
        Formats stock code into 5-digit zero-padded HKEX standard string representation (e.g. '700' -> '00700').
        """
        clean_code = str(raw_code).strip()
        if not clean_code.isdigit():
            raise ValueError(f"Invalid HKEX stock code '{raw_code}'. Code must be numeric.")
        return clean_code.zfill(5)

    def get_hkex_spread_table_tick_size(self, price: float) -> float:
        """
        Calculates dynamic tick size according to Second Schedule of HKEX Rules of the Exchange:
        - $0.01 <= P < $0.25 -> $0.001
        - $0.25 <= P < $0.50 -> $0.005
        - $0.50 <= P < $10.00 -> $0.010
        - $10.00 <= P < $20.00 -> $0.020
        - $20.00 <= P < $100.00 -> $0.050
        - $100.00 <= P < $200.00 -> $0.100
        - $200.00 <= P < $500.00 -> $0.200
        - P >= $500.00 -> $0.500
        """
        if price < 0.01:
            raise ValueError(f"Price (${price}) is below HKEX minimum limit of $0.01.")

        if price < 0.25:
            return 0.001
        elif price < 0.50:
            return 0.005
        elif price < 10.00:
            return 0.010
        elif price < 20.00:
            return 0.020
        elif price < 100.00:
            return 0.050
        elif price < 200.00:
            return 0.100
        elif price < 500.00:
            return 0.200
        else:
            return 0.500

    def validate_and_prepare_order(self, payload: HkexOrderPayload) -> HkexOrionOrderReport:
        """
        Validates order payload against HKEX 5-digit code format, Spread Table tick alignment, and Board Lot size.
        """
        formatted_code = self.format_hkex_stock_code(payload.raw_stock_code)
        tick_size = self.get_hkex_spread_table_tick_size(payload.price)

        # Audit price alignment with tick size (using rounding to avoid floating point imprecision)
        ticks_count = round(payload.price / tick_size, 6)
        is_tick_valid = abs(ticks_count - round(ticks_count)) < 1e-5

        # Audit Board Lot multiple
        is_board_lot = (payload.quantity > 0) and (payload.quantity % payload.board_lot_size == 0)

        if not is_tick_valid:
            status = "INVALID_TICK_SIZE"
            notes = (
                f"HKEX ORDER REJECTED [{formatted_code}]: Price ${payload.price:.3f} violates HKEX Spread Table. "
                f"Applicable tick size for price tier is ${tick_size:.3f}."
            )
            logger.warning(notes)
        elif not is_board_lot:
            status = "INVALID_BOARD_LOT"
            notes = (
                f"HKEX ORDER REJECTED [{formatted_code}]: Quantity {payload.quantity} is not a multiple of Board Lot size ({payload.board_lot_size})."
            )
            logger.warning(notes)
        else:
            status = "ORDER_VALIDATED"
            notes = (
                f"HKEX ORION ORDER VALIDATED [{formatted_code} - {payload.currency} Counter]: "
                f"{payload.side} {payload.quantity} shares @ ${payload.price:.3f} (Tick Size = ${tick_size:.3f}, Board Lot = {payload.board_lot_size})."
            )
            logger.info(notes)

        return HkexOrionOrderReport(
            formatted_stock_code=formatted_code,
            currency=payload.currency.upper(),
            price=payload.price,
            applicable_tick_size=tick_size,
            is_price_tick_valid=is_tick_valid,
            quantity=payload.quantity,
            board_lot_size=payload.board_lot_size,
            is_board_lot_multiple=is_board_lot,
            status=status,
            audit_notes=notes
        )
