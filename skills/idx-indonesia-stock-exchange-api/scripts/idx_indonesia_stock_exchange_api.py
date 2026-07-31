import logging
from dataclasses import dataclass, field
from typing import Dict, List, Optional

logger = logging.getLogger(__name__)

# Valid IDX Market Board Types: 'RG' (Pasar Reguler), 'TN' (Pasar Tunai), 'NG' (Pasar Negosiasi)
VALID_IDX_BOARDS = {"RG", "TN", "NG"}

@dataclass
class IdxOrderPayload:
    ticker: str                         # e.g. 'BBCA', 'TLKM', 'BBRI', 'GOTO'
    board_type: str                     # 'RG', 'TN', 'NG'
    side: str                           # 'BUY' or 'SELL'
    price: float                        # e.g. Rp 10,000
    quantity: int                       # e.g. 500 shares (5 lots)
    reference_price: float              # Previous closing price for ARB/ARA auto-rejection audit

@dataclass
class IdxOrderReport:
    ticker: str
    board_type: str
    side: str
    price: float
    applicable_fraksi_harga: float      # Tick size (Rp 1, Rp 2, Rp 5, Rp 10, Rp 25)
    quantity_shares: int
    lots_count: int
    is_price_tick_valid: bool
    is_board_lot_valid: bool
    is_auto_rejection_valid: bool
    status: str                         # 'IDX_ORDER_VALIDATED', 'INVALID_TICK_SIZE', 'INVALID_BOARD_LOT', 'AUTO_REJECTION_EXCEEDED'
    audit_notes: str

class IdxStockExchangeApiEngine:
    """
    Quantitative market gateway engine for Indonesia Stock Exchange (IDX / BEI JATS system),
    enforcing 4-letter tickers, IDX Fraksi Harga tick sizes, and 100-share Board Lots.
    """
    def __init__(self, max_auto_rejection_pct: float = 25.0):
        self.max_auto_rejection_pct = max_auto_rejection_pct # Max +/- 25% price deviation limit

    def validate_ticker(self, ticker: str) -> str:
        clean = ticker.strip().upper()
        if len(clean) != 4 or not clean.isalpha():
            raise ValueError(f"Invalid IDX Ticker '{ticker}'. Equity tickers must be 4 uppercase letters (e.g. BBCA).")
        return clean

    def get_idx_fraksi_harga(self, price: float) -> float:
        """
        Calculates dynamic tick size according to IDX Fraksi Harga rules:
        - Price < Rp 200 -> Rp 1
        - Rp 200 <= Price < Rp 500 -> Rp 2
        - Rp 500 <= Price < Rp 2,000 -> Rp 5
        - Rp 2,000 <= Price < Rp 5,000 -> Rp 10
        - Price >= Rp 5,000 -> Rp 25
        """
        if price < 1.0:
            raise ValueError(f"Price (Rp {price}) is below IDX minimum limit of Rp 1.")

        if price < 200.0:
            return 1.0
        elif price < 500.0:
            return 2.0
        elif price < 2000.0:
            return 5.0
        elif price < 5000.0:
            return 10.0
        else:
            return 25.0

    def validate_and_route_order(self, payload: IdxOrderPayload) -> IdxOrderReport:
        """
        Validates order payload against JATS rules, Fraksi Harga tick sizes, and 100-share Board Lot multiples.
        """
        ticker_clean = self.validate_ticker(payload.ticker)
        board_clean = payload.board_type.strip().upper()

        if board_clean not in VALID_IDX_BOARDS:
            raise ValueError(f"Invalid IDX Board Type '{payload.board_type}'. Must be one of {VALID_IDX_BOARDS}.")

        tick_size = self.get_idx_fraksi_harga(payload.price)

        # 1. Audit Price Tick Alignment
        ticks_count = round(payload.price / tick_size, 6)
        is_tick_valid = abs(ticks_count - round(ticks_count)) < 1e-5

        # 2. Audit Board Lot (1 Lot = 100 Shares for RG and TN markets)
        if board_clean in {"RG", "TN"}:
            is_lot_valid = (payload.quantity > 0) and (payload.quantity % 100 == 0)
        else: # Negotiated market NG permits odd lots
            is_lot_valid = payload.quantity > 0

        lots_count = payload.quantity // 100 if is_lot_valid else 0

        # 3. Audit Auto-Rejection (ARB/ARA) Price Bounds
        price_diff_pct = abs((payload.price - payload.reference_price) / float(payload.reference_price)) * 100.0
        is_ar_valid = (price_diff_pct <= self.max_auto_rejection_pct)

        if not is_tick_valid:
            status = "INVALID_TICK_SIZE"
            notes = f"IDX REJECTED [{ticker_clean}]: Price Rp {payload.price:,.0f} violates Fraksi Harga tick size (Rp {tick_size:.0f})."
            logger.warning(notes)
        elif not is_lot_valid:
            status = "INVALID_BOARD_LOT"
            notes = f"IDX REJECTED [{ticker_clean}]: Quantity {payload.quantity} shares is not a multiple of 1 Lot (100 shares)."
            logger.warning(notes)
        elif not is_ar_valid:
            status = "AUTO_REJECTION_EXCEEDED"
            notes = f"IDX REJECTED [{ticker_clean}]: Price deviation {price_diff_pct:.1f}% exceeds ARB/ARA limit ({self.max_auto_rejection_pct}%)."
            logger.warning(notes)
        else:
            status = "IDX_ORDER_VALIDATED"
            notes = (
                f"IDX ORDER VALIDATED [{ticker_clean} - {board_clean} Board]: {payload.side} {payload.quantity:,} shares "
                f"({lots_count:,} Lots) @ Rp {payload.price:,.0f} (Fraksi Harga = Rp {tick_size:.0f})."
            )
            logger.info(notes)

        return IdxOrderReport(
            ticker=ticker_clean,
            board_type=board_clean,
            side=payload.side.upper(),
            price=payload.price,
            applicable_fraksi_harga=tick_size,
            quantity_shares=payload.quantity,
            lots_count=lots_count,
            is_price_tick_valid=is_tick_valid,
            is_board_lot_valid=is_lot_valid,
            is_auto_rejection_valid=is_ar_valid,
            status=status,
            audit_notes=notes
        )
