import logging
from dataclasses import dataclass, field
from typing import Dict, List, Optional

logger = logging.getLogger(__name__)

@dataclass
class KrxOrderPayload:
    local_code: str                     # e.g. '005930' (Samsung Electronics), '000660' (SK Hynix)
    side: str                           # 'BUY' or 'SELL'
    price_krw: float                    # Price in KRW (e.g. KRW 150,000)
    quantity: int                       # Single share lot sizing (e.g. 10 shares)
    reference_price_krw: float          # Previous closing price for +/- 30% daily price limit audit

@dataclass
class KrxOrderReport:
    local_code: str
    side: str
    price_krw: float
    applicable_tick_size_krw: float     # Dynamic tick size (e.g. KRW 1, 5, 10, 50, 100, 500, 1000)
    quantity_shares: int
    is_price_tick_valid: bool
    is_price_limit_valid: bool
    status: str                         # 'KRX_ORDER_VALIDATED', 'INVALID_STOCK_CODE', 'INVALID_TICK_SIZE', 'PRICE_LIMIT_EXCEEDED'
    audit_notes: str

class KoreaExchangeKrxApiEngine:
    """
    Quantitative market gateway engine for Korea Exchange (KRX KOSPI / KOSDAQ EXTURE+ engine),
    enforcing 6-digit stock codes, KRW price tick tiers, and +/- 30% daily price expansion limits.
    """
    def __init__(self, max_daily_price_limit_pct: float = 30.0):
        self.max_daily_price_limit_pct = max_daily_price_limit_pct

    def validate_krx_local_code(self, code: str) -> str:
        """
        Validates South Korean stock code is 6 digits with leading zero padding (e.g. '005930').
        """
        clean = code.strip()
        if clean.isdigit():
            clean = clean.zfill(6)
        if len(clean) != 6 or not clean.isdigit():
            raise ValueError(f"Invalid KRX Local Code '{code}'. South Korean stock codes must be 6 digits (e.g. 005930).")
        return clean

    def get_krx_tick_size_krw(self, price_krw: float) -> float:
        """
        Calculates dynamic tick size in KRW according to KRX KOSPI EXTURE+ price tiers:
        - Price < KRW 1,000 -> KRW 1
        - KRW 1,000 <= Price < KRW 5,000 -> KRW 5
        - KRW 5,000 <= Price < KRW 10,000 -> KRW 10
        - KRW 10,000 <= Price < KRW 50,000 -> KRW 50
        - KRW 50,000 <= Price < KRW 100,000 -> KRW 100
        - KRW 100,000 <= Price < KRW 500,000 -> KRW 500
        - Price >= KRW 500,000 -> KRW 1,000
        """
        if price_krw < 1.0:
            raise ValueError(f"Price (KRW {price_krw}) is below KRX minimum limit of KRW 1.")

        if price_krw < 1000.0:
            return 1.0
        elif price_krw < 5000.0:
            return 5.0
        elif price_krw < 10000.0:
            return 10.0
        elif price_krw < 50000.0:
            return 50.0
        elif price_krw < 100000.0:
            return 100.0
        elif price_krw < 500000.0:
            return 500.0
        else:
            return 1000.0

    def validate_and_route_order(self, payload: KrxOrderPayload) -> KrxOrderReport:
        """
        Validates order payload against KRX EXTURE+ rules, KRW price tick tiers, and +/- 30% daily price limits.
        """
        code_clean = self.validate_krx_local_code(payload.local_code)
        side_clean = payload.side.strip().upper()

        tick_size_krw = self.get_krx_tick_size_krw(payload.price_krw)

        # 1. Audit Price Tick Alignment
        ticks_count = round(payload.price_krw / tick_size_krw, 6)
        is_tick_valid = abs(ticks_count - round(ticks_count)) < 1e-5

        # 2. Audit Daily Price Limits (+/- 30%)
        price_diff_pct = abs((payload.price_krw - payload.reference_price_krw) / float(payload.reference_price_krw)) * 100.0
        is_limit_valid = (price_diff_pct <= self.max_daily_price_limit_pct)

        if not is_tick_valid:
            status = "INVALID_TICK_SIZE"
            notes = f"KRX REJECT [{code_clean}]: Price KRW {payload.price_krw:,.0f} violates KRX tick size (KRW {tick_size_krw:.0f})."
            logger.warning(notes)
        elif not is_limit_valid:
            status = "PRICE_LIMIT_EXCEEDED"
            notes = f"KRX REJECT [{code_clean}]: Price deviation {price_diff_pct:.1f}% exceeds KRX daily limit ({self.max_daily_price_limit_pct}%)."
            logger.warning(notes)
        else:
            status = "KRX_ORDER_VALIDATED"
            notes = (
                f"KRX ORDER VALIDATED [{code_clean} - EXTURE+]: {side_clean} {payload.quantity:,} shares "
                f"@ KRW {payload.price_krw:,.0f} (Tick Size = KRW {tick_size_krw:.0f})."
            )
            logger.info(notes)

        return KrxOrderReport(
            local_code=code_clean,
            side=side_clean,
            price_krw=payload.price_krw,
            applicable_tick_size_krw=tick_size_krw,
            quantity_shares=payload.quantity,
            is_price_tick_valid=is_tick_valid,
            is_price_limit_valid=is_limit_valid,
            status=status,
            audit_notes=notes
        )
