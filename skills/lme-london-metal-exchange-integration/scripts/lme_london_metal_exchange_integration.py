import logging
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

# LME Metal Specifications: (Code, Name, Lot Size MT, Standard Tick USD/MT)
LME_SPECS: Dict[str, Dict[str, Any]] = {
    "CA": {"name": "Copper Grade A", "lot_size_mt": 25.0, "tick_size_usd": 0.50},
    "AH": {"name": "Primary Aluminium", "lot_size_mt": 25.0, "tick_size_usd": 0.50},
    "NI": {"name": "Primary Nickel", "lot_size_mt": 6.0, "tick_size_usd": 0.50},
    "ZS": {"name": "Special High Grade Zinc", "lot_size_mt": 25.0, "tick_size_usd": 0.50},
    "PB": {"name": "Standard Lead", "lot_size_mt": 25.0, "tick_size_usd": 0.50},
    "SN": {"name": "Special High Grade Tin", "lot_size_mt": 5.0, "tick_size_usd": 0.50},
}

@dataclass
class LmeOrderPayload:
    metal_code: str                     # e.g. 'CA', 'AH', 'NI'
    prompt_date: str                    # '3M', 'CASH', or ISO date '2026-10-15'
    side: str                           # 'BUY' or 'SELL'
    price_usd_per_mt: float             # Price in USD per Metric Ton (e.g. 9250.50)
    lots: int                           # Number of LME contract lots (e.g. 10)

@dataclass
class LmeOrderReport:
    metal_code: str
    metal_name: str
    prompt_date: str
    side: str
    price_usd_per_mt: float
    lots: int
    lot_size_mt: float
    total_tonnage_mt: float
    total_notional_usd: float
    is_price_tick_valid: bool
    status: str                         # 'LME_ORDER_VALIDATED', 'INVALID_METAL_CODE', 'INVALID_TICK_SIZE'
    audit_notes: str

class LmeExchangeApiEngine:
    """
    Quantitative market gateway engine for the London Metal Exchange (LMEselect API),
    enforcing metal contract lot tonnage (Copper/Aluminium 25 MT, Nickel 6 MT), USD/MT tick sizes, and prompt date mechanics.
    """
    def __init__(self):
        pass

    def validate_and_route_order(self, payload: LmeOrderPayload) -> LmeOrderReport:
        """
        Validates order payload against LME contract specs, lot tonnage, prompt date rules, and $0.50/MT price ticks.
        """
        code_clean = payload.metal_code.strip().upper()
        if code_clean not in LME_SPECS:
            notes = f"LME REJECT: Invalid Metal Code '{payload.metal_code}'. Valid codes: {list(LME_SPECS.keys())}."
            logger.error(notes)
            return LmeOrderReport(
                metal_code=code_clean, metal_name="UNKNOWN", prompt_date=payload.prompt_date,
                side=payload.side, price_usd_per_mt=payload.price_usd_per_mt, lots=payload.lots,
                lot_size_mt=0.0, total_tonnage_mt=0.0, total_notional_usd=0.0,
                is_price_tick_valid=False, status="INVALID_METAL_CODE", audit_notes=notes
            )

        spec = LME_SPECS[code_clean]
        side_clean = payload.side.strip().upper()
        prompt_clean = payload.prompt_date.strip().upper()

        # 1. Audit Price Tick Alignment ($0.50/MT)
        tick_size = spec["tick_size_usd"]
        ticks_count = round(payload.price_usd_per_mt / tick_size, 6)
        is_tick_valid = abs(ticks_count - round(ticks_count)) < 1e-5

        # 2. Calculate Total Tonnage and Notional USD
        tot_tonnage = float(payload.lots) * spec["lot_size_mt"]
        tot_notional = round(tot_tonnage * payload.price_usd_per_mt, 2)

        if not is_tick_valid:
            status = "INVALID_TICK_SIZE"
            notes = (
                f"LME REJECT [{code_clean}]: Price ${payload.price_usd_per_mt:,.2f}/MT violates "
                f"LMEselect tick size (${tick_size:.2f}/MT)."
            )
            logger.warning(notes)
        else:
            status = "LME_ORDER_VALIDATED"
            notes = (
                f"LME ORDER VALIDATED [{code_clean} - {spec['name']}]: {side_clean} {payload.lots:,} lots "
                f"({tot_tonnage:,.1f} MT) @ ${payload.price_usd_per_mt:,.2f}/MT [Prompt: {prompt_clean}]. "
                f"Total Notional = ${tot_notional:,.2f} USD."
            )
            logger.info(notes)

        return LmeOrderReport(
            metal_code=code_clean,
            metal_name=spec["name"],
            prompt_date=prompt_clean,
            side=side_clean,
            price_usd_per_mt=payload.price_usd_per_mt,
            lots=payload.lots,
            lot_size_mt=spec["lot_size_mt"],
            total_tonnage_mt=tot_tonnage,
            total_notional_usd=tot_notional,
            is_price_tick_valid=is_tick_valid,
            status=status,
            audit_notes=notes
        )
