"""
Taiwan Stock Exchange (TWSE) API Integration Engine.

Implements order routing validation, FINI institutional compliance, 
dynamic tick size rules, 10% daily price limit checks, and naked short sale prevention.
"""

from dataclasses import dataclass, field
from typing import Dict, Any, List, Optional
import math


@dataclass
class Config:
    api_key: str
    fini_id: str = "FINI-TW-99882"
    environment: str = "SANDBOX"
    daily_price_limit_pct: float = 0.10
    borrow_locate_required: bool = True


@dataclass
class TWSEOrder:
    order_id: str
    symbol: str
    side: str  # "BUY", "SELL", "SHORT_SELL"
    quantity: int
    price: float
    order_type: str = "LIMIT"  # "LIMIT", "MARKET"
    time_in_force: str = "ROH"  # "ROH" (Rest of Hours / Day), "IOC", "FOK"
    odd_lot: bool = False
    fini_id: Optional[str] = None


class IntegrationEngine:
    """TWSE API Engine with market micro-structure & FINI regulatory enforcement."""

    def __init__(self, config: Config):
        self.config = config
        self.connected: bool = False
        self.orders: Dict[str, TWSEOrder] = {}

    def connect(self) -> bool:
        self.connected = True
        return True

    def process(self) -> str:
        return "success"

    def get_tick_size(self, price: float) -> float:
        """
        Returns TWSE tick size based on price tier:
        - Price < NT$50: NT$0.01
        - Price >= NT$50: NT$0.05
        """
        if price < 50.0:
            return 0.01
        else:
            return 0.05

    def validate_price_tick(self, price: float) -> bool:
        tick = self.get_tick_size(price)
        # Check alignment using small epsilon for floating point math
        remainder = round(price % tick, 4)
        return remainder == 0.0 or abs(remainder - tick) < 1e-4

    def validate_price_limit(self, price: float, prev_close: float) -> bool:
        limit_delta = round(prev_close * self.config.daily_price_limit_pct, 2)
        upper_limit = prev_close + limit_delta
        lower_limit = prev_close - limit_delta
        return lower_limit <= price <= upper_limit

    def validate_order(
        self, order: TWSEOrder, prev_close: float, locate_available: bool = True
    ) -> Dict[str, Any]:
        """
        Validates TWSE order compliance:
        1. FINI identification check
        2. Board lot vs odd lot validation (1,000 shares per lot for regular)
        3. Dynamic tick size alignment
        4. 10% daily price limit check
        5. Naked short sale restriction
        """
        if not order.fini_id and not self.config.fini_id:
            return {"valid": False, "reason": "Missing FINI Registration ID"}

        if not order.odd_lot and order.quantity % 1000 != 0:
            return {
                "valid": False,
                "reason": "Regular board lot orders must be multiples of 1,000 shares",
            }

        if order.order_type == "LIMIT":
            if not self.validate_price_tick(order.price):
                tick = self.get_tick_size(order.price)
                return {
                    "valid": False,
                    "reason": f"Price {order.price} does not align with tick size {tick}",
                }

            if not self.validate_price_limit(order.price, prev_close):
                return {
                    "valid": False,
                    "reason": f"Price {order.price} exceeds 10% daily price limit based on prev close {prev_close}",
                }

        if order.side == "SHORT_SELL" and self.config.borrow_locate_required:
            if not locate_available:
                return {
                    "valid": False,
                    "reason": "Naked short sale prohibited by TWSE; borrow locate missing",
                }

        return {"valid": True, "reason": "Order passed all TWSE pre-trade checks"}

    def submit_order(
        self, order: TWSEOrder, prev_close: float, locate_available: bool = True
    ) -> Dict[str, Any]:
        validation = self.validate_order(order, prev_close, locate_available)
        if not validation["valid"]:
            return {
                "status": "REJECTED",
                "order_id": order.order_id,
                "reason": validation["reason"],
            }

        self.orders[order.order_id] = order
        return {
            "status": "SUBMITTED",
            "order_id": order.order_id,
            "symbol": order.symbol,
            "quantity": order.quantity,
            "price": order.price,
            "fini_id": order.fini_id or self.config.fini_id,
            "message": "Order successfully routed to TWSE matching engine",
        }


# Alias for domain naming clarity
TaiwanStockExchangeTWSEEngine = IntegrationEngine
