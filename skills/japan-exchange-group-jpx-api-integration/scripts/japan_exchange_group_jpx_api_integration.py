import logging
from dataclasses import dataclass, field
from typing import Dict, List, Optional

logger = logging.getLogger(__name__)

@dataclass
class JpxOrderPayload:
    local_code: str                     # e.g. '7203' (Toyota), '6758' (Sony), '9984' (SoftBank)
    side: str                           # 'BUY' or 'SELL'
    price_jpy: float                    # e.g. JPY 2,500
    quantity: int                       # e.g. 500 shares (5 Units)
    reference_price_jpy: float          # Previous closing price for price limit bounds audit

@dataclass
class JpxOrderReport:
    local_code: str
    side: str
    price_jpy: float
    applicable_tick_size_jpy: float     # Dynamic tick size (e.g. JPY 1.0, JPY 5.0, JPY 10.0, JPY 50.0)
    quantity_shares: int
    board_lots_count: int
    is_price_tick_valid: bool
    is_board_lot_valid: bool
    is_price_limit_valid: bool
    status: str                         # 'JPX_ORDER_VALIDATED', 'INVALID_TICK_SIZE', 'INVALID_BOARD_LOT', 'PRICE_LIMIT_EXCEEDED'
    audit_notes: str

class JpxStockExchangeApiEngine:
    """
    Quantitative market gateway engine for Japan Exchange Group (JPX / Tokyo Stock Exchange TSE arrowhead 3.0),
    enforcing 4-digit ticker codes, JPY price tick tiers, and 100-share Board Lots.
    """
    def __init__(self, max_daily_price_limit_pct: float = 20.0):
        self.max_daily_price_limit_pct = max_daily_price_limit_pct

    def validate_tse_local_code(self, code: str) -> str:
        """
        Validates Japanese stock code is 4 digits.
        """
        clean = code.strip()
        if len(clean) != 4 or not clean.isdigit():
            raise ValueError(f"Invalid TSE Local Code '{code}'. Japanese stock codes must be 4 digits (e.g. 7203).")
        return clean

    def get_tse_tick_size(self, price_jpy: float) -> float:
        """
        Calculates dynamic tick size according to TSE arrowhead 3.0 price tiers:
        - Price < JPY 1,000 -> JPY 1.0
        - JPY 1,000 <= Price < JPY 3,000 -> JPY 1.0
        - JPY 3,000 <= Price < JPY 5,000 -> JPY 5.0
        - JPY 5,000 <= Price < JPY 10,000 -> JPY 10.0
        - Price >= JPY 10,000 -> JPY 50.0
        """
        if price_jpy < 1.0:
            raise ValueError(f"Price (JPY {price_jpy}) is below TSE minimum limit of JPY 1.")

        if price_jpy < 3000.0:
            return 1.0
        elif price_jpy < 5000.0:
            return 5.0
        elif price_jpy < 10000.0:
            return 10.0
        else:
            return 50.0

    def validate_and_route_order(self, payload: JpxOrderPayload) -> JpxOrderReport:
        """
        Validates order payload against TSE arrowhead 3.0 rules, JPY tick tiers, and 100-share Board Lots.
        """
        code_clean = self.validate_tse_local_code(payload.local_code)
        side_clean = payload.side.strip().upper()
        tick_size = self.get_tse_tick_size(payload.price_jpy)

        # 1. Audit Price Tick Alignment
        ticks_count = round(payload.price_jpy / tick_size, 6)
        is_tick_valid = abs(ticks_count - round(ticks_count)) < 1e-5

        # 2. Audit Board Lot (1 Unit = 100 Shares)
        is_lot_valid = (payload.quantity > 0) and (payload.quantity % 100 == 0)
        lots_count = payload.quantity // 100 if is_lot_valid else 0

        # 3. Audit Daily Price Limit Bounds
        price_diff_pct = abs((payload.price_jpy - payload.reference_price_jpy) / float(payload.reference_price_jpy)) * 100.0
        is_limit_valid = (price_diff_pct <= self.max_daily_price_limit_pct)

        if not is_tick_valid:
            status = "INVALID_TICK_SIZE"
            notes = f"JPX REJECT [{code_clean}]: Price JPY {payload.price_jpy:,.0f} violates TSE tick size (JPY {tick_size:.1f})."
            logger.warning(notes)
        elif not is_lot_valid:
            status = "INVALID_BOARD_LOT"
            notes = f"JPX REJECT [{code_clean}]: Quantity {payload.quantity} shares is not a multiple of 1 Board Lot (100 shares)."
            logger.warning(notes)
        elif not is_limit_valid:
            status = "PRICE_LIMIT_EXCEEDED"
            notes = f"JPX REJECT [{code_clean}]: Price deviation {price_diff_pct:.1f}% exceeds daily price limit ({self.max_daily_price_limit_pct}%)."
            logger.warning(notes)
        else:
            status = "JPX_ORDER_VALIDATED"
            notes = (
                f"JPX ORDER VALIDATED [{code_clean} - TSE arrowhead]: {side_clean} {payload.quantity:,} shares "
                f"({lots_count:,} Units) @ JPY {payload.price_jpy:,.0f} (Tick Size = JPY {tick_size:.1f})."
            )
            logger.info(notes)

        return JpxOrderReport(
            local_code=code_clean,
            side=side_clean,
            price_jpy=payload.price_jpy,
            applicable_tick_size_jpy=tick_size,
            quantity_shares=payload.quantity,
            board_lots_count=lots_count,
            is_price_tick_valid=is_tick_valid,
            is_board_lot_valid=is_lot_valid,
            is_price_limit_valid=is_limit_valid,
            status=status,
            audit_notes=notes
        )
