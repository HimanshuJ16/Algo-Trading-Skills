import math
import logging
from dataclasses import dataclass, field
from typing import List
from enum import Enum

logger = logging.getLogger(__name__)

class Side(Enum):
    BUY = '1'
    SELL = '2'

@dataclass
class OptionLeg:
    symbol: str
    ratio: int
    side: Side
    security_type: str = "OPT" # OPT or CS (Common Stock)

class CboeComplexOrderEngine:
    """
    Engine to build and format Cboe FIX New Order Multileg (MsgType=AB)
    Handles ratio normalization for the Complex Order Book (COB).
    """
    def __init__(self, cl_ord_id: str, symbol: str, total_quantity: int, net_price: float):
        self.cl_ord_id = cl_ord_id
        self.symbol = symbol # Base symbol, e.g., SPX
        self.total_quantity = total_quantity
        self.net_price = net_price
        self.legs: List[OptionLeg] = []

    def add_leg(self, leg: OptionLeg):
        if len(self.legs) >= 16:
            raise ValueError("Cboe limit is 16 legs per complex order.")
        self.legs.append(leg)

    def _normalize_ratios(self) -> (int, List[OptionLeg]):
        """
        Reduces leg ratios to simplest form and scales total order quantity.
        e.g., Buy 100 Leg1, Sell 200 Leg2 -> Ratio 1:2, Qty=100.
        """
        if not self.legs:
            return self.total_quantity, []

        # Find GCD of all leg ratios
        ratios = [leg.ratio for leg in self.legs]
        current_gcd = ratios[0]
        for r in ratios[1:]:
            current_gcd = math.gcd(current_gcd, r)

        if current_gcd <= 0:
            raise ValueError("Leg ratios must be positive.")

        # If GCD > 1, we must reduce ratios and scale total quantity
        normalized_qty = self.total_quantity * current_gcd
        
        normalized_legs = []
        for leg in self.legs:
            normalized_legs.append(OptionLeg(
                symbol=leg.symbol,
                ratio=int(leg.ratio / current_gcd),
                side=leg.side,
                security_type=leg.security_type
            ))
            
        return normalized_qty, normalized_legs

    def build_fix_message(self) -> str:
        """
        Constructs a minimal FIX New Order Multileg message (MsgType=AB).
        """
        if not self.legs:
            raise ValueError("Cannot build complex order with 0 legs.")
            
        norm_qty, norm_legs = self._normalize_ratios()
        
        # 35=AB (MsgType), 11=ClOrdID, 55=Symbol, 38=OrderQty, 44=Price, 555=NoLegs
        fix_parts = [
            "35=AB",
            f"11={self.cl_ord_id}",
            f"55={self.symbol}",
            f"38={norm_qty}",
            f"44={self.net_price}",
            f"555={len(norm_legs)}"
        ]
        
        # Add repeating group for legs
        for leg in norm_legs:
            fix_parts.extend([
                f"600={leg.symbol}",
                f"609={leg.security_type}",
                f"623={leg.ratio}",
                f"624={leg.side.value}"
            ])
            
        # SOH delimiter (using pipe '|' for display/testing)
        return "|".join(fix_parts) + "|"
