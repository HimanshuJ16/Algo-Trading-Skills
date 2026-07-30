import math
import logging
from decimal import Decimal, ROUND_HALF_UP
from dataclasses import dataclass
from typing import Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)

@dataclass
class PriceBandTickRule:
    min_price: float
    max_price: float                    # float('inf') for upper bound
    tick_size: float

@dataclass
class TickRegimeAuditReport:
    venue_id: str
    symbol: str
    proposed_price: float
    active_tick_size: float
    aligned_price: float
    is_on_tick: bool
    status: str                         # 'TICK_COMPLIANT', 'OFF_TICK_ALIGNED', 'OFF_TICK_REJECTED'
    audit_notes: str

class ExchangeTickSizeRegimeEngine:
    """
    Quantitative market data engine for tracking dynamic exchange tick size regimes (US SEC Rule 612, EU MiFID II RTS 11, DFM 2026 AED),
    aligning prices to valid tick steps, and auditing order tick compliance.
    """
    def __init__(self):
        self.regimes: Dict[str, List[PriceBandTickRule]] = {
            "US_EQUITIES": [
                PriceBandTickRule(0.0, 1.0, 0.0001),
                PriceBandTickRule(1.0, float("inf"), 0.01)
            ],
            "EU_XETRA": [
                PriceBandTickRule(0.0, 10.0, 0.001),
                PriceBandTickRule(10.0, 50.0, 0.005),
                PriceBandTickRule(50.0, float("inf"), 0.01)
            ],
            "DFM_DUBAI": [
                PriceBandTickRule(0.0, 1.0, 0.001),
                PriceBandTickRule(1.0, 10.0, 0.01),
                PriceBandTickRule(10.0, 50.0, 0.02),
                PriceBandTickRule(50.0, float("inf"), 0.05)
            ]
        }

    def get_active_tick_size(self, venue_id: str, price: float) -> float:
        """
        Looks up dynamic tick size based on venue regime and price band.
        """
        rules = self.regimes.get(venue_id.upper())
        if not rules:
            return 0.01 # Fallback default tick

        for rule in rules:
            if rule.min_price <= price < rule.max_price:
                return rule.tick_size

        return rules[-1].tick_size

    def align_price_to_tick(self, price: float, tick_size: float) -> float:
        if tick_size <= 0:
            return price
        
        # Use Decimal with ROUND_HALF_UP to avoid Banker's rounding ambiguities
        d_price = Decimal(str(price))
        d_tick = Decimal(str(tick_size))
        
        steps = (d_price / d_tick).quantize(Decimal("1"), rounding=ROUND_HALF_UP)
        aligned = steps * d_tick
        return float(aligned)

    def audit_order_tick_compliance(self, venue_id: str, symbol: str, proposed_price: float, auto_align: bool = True) -> TickRegimeAuditReport:
        """
        Audits proposed order price against active venue tick regime.
        """
        tick_size = self.get_active_tick_size(venue_id, proposed_price)
        aligned = self.align_price_to_tick(proposed_price, tick_size)

        diff = abs(proposed_price - aligned)
        is_on_tick = (diff < 1e-6)

        if is_on_tick:
            status = "TICK_COMPLIANT"
            notes = f"TICK COMPLIANT [{symbol} @ {venue_id}]: Price {proposed_price:.4f} is valid for tick size {tick_size}."
            logger.info(notes)
        elif auto_align:
            status = "OFF_TICK_ALIGNED"
            notes = f"OFF-TICK ALIGNED [{symbol} @ {venue_id}]: Proposed price {proposed_price:.4f} aligned to valid tick {aligned:.4f} (Tick={tick_size})."
            logger.warning(notes)
        else:
            status = "OFF_TICK_REJECTED"
            notes = f"OFF-TICK REJECTED [{symbol} @ {venue_id}]: Proposed price {proposed_price:.4f} violates tick size {tick_size}."
            logger.error(notes)

        return TickRegimeAuditReport(
            venue_id=venue_id,
            symbol=symbol,
            proposed_price=proposed_price,
            active_tick_size=tick_size,
            aligned_price=aligned,
            is_on_tick=is_on_tick,
            status=status,
            audit_notes=notes
        )
