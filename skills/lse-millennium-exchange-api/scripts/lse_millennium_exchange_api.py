import logging
from dataclasses import dataclass, field
from typing import Dict, List, Optional

logger = logging.getLogger(__name__)

@dataclass
class LseOrderPayload:
    tidm: str                           # e.g. 'SHEL' (Shell), 'AZN' (AstraZeneca), 'HSBA' (HSBC)
    side: str                           # 'BUY' or 'SELL'
    price_gbx: float                    # Price in GBX / Pence (e.g. 2650.0 GBX = £26.50)
    quantity: int                       # Number of shares (e.g. 1000)
    currency: str = "GBX"               # Must be 'GBX' (Pence)

@dataclass
class LseOrderReport:
    tidm: str
    side: str
    price_gbx: float
    applicable_tick_size_gbx: float     # Dynamic tick size in GBX
    quantity_shares: int
    total_notional_gbp: float           # Equivalent Notional in British Pounds (£)
    is_price_tick_valid: bool
    status: str                         # 'LSE_ORDER_VALIDATED', 'INVALID_TIDM', 'INVALID_CURRENCY', 'INVALID_TICK_SIZE'
    audit_notes: str

class LseMillenniumExchangeApiEngine:
    """
    Quantitative market gateway engine for the London Stock Exchange (LSE Millennium Exchange platform),
    enforcing TIDM ticker codes, GBX (Pence) currency notation, and MiFID II RTS 28 tick size schedules.
    """
    def __init__(self):
        pass

    def validate_lse_tidm(self, tidm: str) -> str:
        """
        Validates LSE Tradable Instrument Display Mnemonic (TIDM) code (2-4 uppercase letters).
        """
        clean = tidm.strip().upper()
        if not clean.isalpha() or not (2 <= len(clean) <= 5):
            raise ValueError(f"Invalid LSE TIDM ticker '{tidm}'. TIDM symbols must be 2-5 uppercase letters (e.g. SHEL, AZN).")
        return clean

    def get_lse_tick_size_gbx(self, price_gbx: float) -> float:
        """
        Calculates dynamic tick size in GBX according to LSE MiFID II RTS 28 price tiers:
        - Price < 10 GBX -> 0.01 GBX
        - 10 <= Price < 50 GBX -> 0.05 GBX
        - 50 <= Price < 100 GBX -> 0.10 GBX
        - 100 <= Price < 500 GBX -> 0.20 GBX
        - 500 <= Price < 1000 GBX -> 0.50 GBX
        - 1000 <= Price < 5000 GBX -> 1.00 GBX
        - Price >= 5000 GBX -> 2.00 GBX
        """
        if price_gbx <= 0.0:
            raise ValueError(f"Price (GBX {price_gbx}) must be strictly positive.")

        if price_gbx < 10.0:
            return 0.01
        elif price_gbx < 50.0:
            return 0.05
        elif price_gbx < 100.0:
            return 0.10
        elif price_gbx < 500.0:
            return 0.20
        elif price_gbx < 1000.0:
            return 0.50
        elif price_gbx < 5000.0:
            return 1.00
        else:
            return 2.00

    def validate_and_route_order(self, payload: LseOrderPayload) -> LseOrderReport:
        """
        Validates order payload against LSE Millennium Exchange rules, GBX pence notation, and RTS 28 tick sizes.
        """
        try:
            tidm_clean = self.validate_lse_tidm(payload.tidm)
        except ValueError as e:
            notes = f"LSE REJECT: {str(e)}"
            logger.error(notes)
            return LseOrderReport(
                tidm=payload.tidm, side=payload.side, price_gbx=payload.price_gbx,
                applicable_tick_size_gbx=0.0, quantity_shares=payload.quantity, total_notional_gbp=0.0,
                is_price_tick_valid=False, status="INVALID_TIDM", audit_notes=notes
            )

        curr_clean = payload.currency.strip().upper()
        if curr_clean != "GBX":
            notes = (
                f"LSE REJECT [{tidm_clean}]: Currency '{payload.currency}' rejected. "
                f"LSE Millennium Exchange mandates GBX (Pence) currency notation!"
            )
            logger.error(notes)
            return LseOrderReport(
                tidm=tidm_clean, side=payload.side, price_gbx=payload.price_gbx,
                applicable_tick_size_gbx=0.0, quantity_shares=payload.quantity, total_notional_gbp=0.0,
                is_price_tick_valid=False, status="INVALID_CURRENCY", audit_notes=notes
            )

        side_clean = payload.side.strip().upper()
        tick_size_gbx = self.get_lse_tick_size_gbx(payload.price_gbx)

        # 1. Audit Price Tick Alignment
        ticks_count = round(payload.price_gbx / tick_size_gbx, 6)
        is_tick_valid = abs(ticks_count - round(ticks_count)) < 1e-5

        # 2. Calculate Equivalent GBP (£) Notional Value
        notional_gbp = round((payload.price_gbx * payload.quantity) / 100.0, 2)

        if not is_tick_valid:
            status = "INVALID_TICK_SIZE"
            notes = (
                f"LSE REJECT [{tidm_clean}]: Price {payload.price_gbx:.2f} GBX violates "
                f"LSE tick size ({tick_size_gbx:.2f} GBX)."
            )
            logger.warning(notes)
        else:
            status = "LSE_ORDER_VALIDATED"
            notes = (
                f"LSE ORDER VALIDATED [{tidm_clean} - Millennium]: {side_clean} {payload.quantity:,} shares "
                f"@ {payload.price_gbx:,.2f} GBX (Tick Size = {tick_size_gbx:.2f} GBX). "
                f"Total Notional = £{notional_gbp:,.2f} GBP."
            )
            logger.info(notes)

        return LseOrderReport(
            tidm=tidm_clean,
            side=side_clean,
            price_gbx=payload.price_gbx,
            applicable_tick_size_gbx=tick_size_gbx,
            quantity_shares=payload.quantity,
            total_notional_gbp=notional_gbp,
            is_price_tick_valid=is_tick_valid,
            status=status,
            audit_notes=notes
        )
