import logging
from dataclasses import dataclass, field
from typing import Dict, List, Optional

logger = logging.getLogger(__name__)

@dataclass
class JseOrderPayload:
    alpha_code: str                     # e.g. 'NPN' (Naspers), 'PRX' (Prosus), 'AGL' (Anglo American), 'SOL' (Sasol)
    side: str                           # 'BUY' or 'SELL'
    price_zac: float                    # Price in ZAC Cents (e.g. 85,500 ZAC = ZAR 855.00)
    quantity: int                       # Shares quantity
    reference_price_zac: float          # Previous closing price in ZAC Cents

@dataclass
class JseOrderReport:
    alpha_code: str
    side: str
    price_zac: float
    equivalent_price_zar: float
    applicable_tick_size_zac: float     # Tick size in Cents (e.g. 1.0 ZAC, 5.0 ZAC)
    quantity_shares: int
    notional_value_zar: float
    is_price_tick_valid: bool
    is_price_limit_valid: bool
    status: str                         # 'JSE_ORDER_VALIDATED', 'INVALID_ALPHA_CODE', 'INVALID_TICK_SIZE', 'PRICE_LIMIT_EXCEEDED'
    audit_notes: str

class JseSouthAfricaApiEngine:
    """
    Quantitative market gateway engine for Johannesburg Stock Exchange (JSE South Africa Millennium Exchange),
    enforcing 3-letter alpha tickers, ZAC South African Cents pricing, and JSE tick size tiers.
    """
    def __init__(self, max_daily_price_limit_pct: float = 15.0):
        self.max_daily_price_limit_pct = max_daily_price_limit_pct

    def validate_jse_alpha_code(self, code: str) -> str:
        """
        Validates JSE 3-letter alpha ticker format (e.g. 'NPN').
        """
        clean = code.strip().upper()
        if len(clean) != 3 or not clean.isalpha():
            raise ValueError(f"Invalid JSE Alpha Code '{code}'. JSE equity tickers must be 3 uppercase letters (e.g. NPN).")
        return clean

    def get_jse_tick_size_zac(self, price_zac: float) -> float:
        """
        Calculates dynamic tick size in ZAC Cents according to JSE Millennium Exchange rules:
        - Price < 10,000 ZAC (ZAR 100) -> 1.0 ZAC
        - Price >= 10,000 ZAC (ZAR 100) -> 5.0 ZAC
        """
        if price_zac < 1.0:
            raise ValueError(f"Price ({price_zac} ZAC) is below JSE minimum limit of 1 ZAC.")

        if price_zac < 10000.0:
            return 1.0
        else:
            return 5.0

    def validate_and_route_order(self, payload: JseOrderPayload) -> JseOrderReport:
        """
        Validates order payload against JSE Millennium Exchange rules, ZAC Cents pricing, and tick size tiers.
        """
        code_clean = self.validate_jse_alpha_code(payload.alpha_code)
        side_clean = payload.side.strip().upper()

        tick_size_zac = self.get_jse_tick_size_zac(payload.price_zac)

        # 1. Audit Price Tick Alignment in ZAC Cents
        ticks_count = round(payload.price_zac / tick_size_zac, 6)
        is_tick_valid = abs(ticks_count - round(ticks_count)) < 1e-5

        # 2. Convert ZAC Cents to ZAR Rand Notional
        equiv_zar = round(payload.price_zac / 100.0, 4)
        notional_zar = round((payload.price_zac * payload.quantity) / 100.0, 2)

        # 3. Audit Daily Price Limits
        price_diff_pct = abs((payload.price_zac - payload.reference_price_zac) / float(payload.reference_price_zac)) * 100.0
        is_limit_valid = (price_diff_pct <= self.max_daily_price_limit_pct)

        if not is_tick_valid:
            status = "INVALID_TICK_SIZE"
            notes = f"JSE REJECT [{code_clean}]: Price {payload.price_zac:,.0f} ZAC violates JSE tick size ({tick_size_zac:.1f} ZAC)."
            logger.warning(notes)
        elif not is_limit_valid:
            status = "PRICE_LIMIT_EXCEEDED"
            notes = f"JSE REJECT [{code_clean}]: Price deviation {price_diff_pct:.1f}% exceeds JSE daily limit ({self.max_daily_price_limit_pct}%)."
            logger.warning(notes)
        else:
            status = "JSE_ORDER_VALIDATED"
            notes = (
                f"JSE ORDER VALIDATED [{code_clean} - Millennium Exchange]: {side_clean} {payload.quantity:,} shares "
                f"@ {payload.price_zac:,.0f} ZAC (ZAR {equiv_zar:,.2f}). Total Notional = ZAR {notional_zar:,.2f} "
                f"(Tick Size = {tick_size_zac:.1f} ZAC)."
            )
            logger.info(notes)

        return JseOrderReport(
            alpha_code=code_clean,
            side=side_clean,
            price_zac=payload.price_zac,
            equivalent_price_zar=equiv_zar,
            applicable_tick_size_zac=tick_size_zac,
            quantity_shares=payload.quantity,
            notional_value_zar=notional_zar,
            is_price_tick_valid=is_tick_valid,
            is_price_limit_valid=is_limit_valid,
            status=status,
            audit_notes=notes
        )
