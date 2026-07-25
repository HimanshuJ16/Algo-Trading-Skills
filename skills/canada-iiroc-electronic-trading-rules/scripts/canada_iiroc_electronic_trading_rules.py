import logging
from dataclasses import dataclass
from enum import Enum
from typing import Optional, List

logger = logging.getLogger(__name__)

class OrderSide(Enum):
    BUY = 1
    SELL = 2
    SELL_SHORT = 3

class ViolationCode(Enum):
    FAT_FINGER_SIZE = "V01"
    FAT_FINGER_PRICE = "V02"
    MAX_CAPITAL_EXCEEDED = "V03"
    IMPROPER_SHORT_MARK = "V04"

@dataclass
class ComplianceResult:
    is_compliant: bool
    violations: List[ViolationCode]
    reason: str

@dataclass
class Order:
    order_id: str
    symbol: str
    side: OrderSide
    quantity: int
    price: float
    current_inventory: int
    last_traded_price: float

@dataclass
class RiskLimits:
    max_order_quantity: int = 100_000
    max_order_value_cad: float = 1_000_000.0
    max_price_deviation_pct: float = 0.05  # 5%

class CiroPreTradeRiskEngine:
    """
    CIRO (IIROC) UMIR 7.1 / NI 23-103 Pre-Trade Risk Control Engine.
    Enforces automated fat-finger and regulatory checks before order routing.
    """
    def __init__(self, limits: RiskLimits):
        self.limits = limits

    def validate_order(self, order: Order) -> ComplianceResult:
        violations = []
        reasons = []

        # 1. Fat Finger Size Check
        if order.quantity > self.limits.max_order_quantity:
            violations.append(ViolationCode.FAT_FINGER_SIZE)
            reasons.append(f"Qty {order.quantity} exceeds max {self.limits.max_order_quantity}.")

        # 2. Capital / Value Check
        order_value = order.quantity * order.price
        if order_value > self.limits.max_order_value_cad:
            violations.append(ViolationCode.MAX_CAPITAL_EXCEEDED)
            reasons.append(f"Value CAD {order_value:,.2f} exceeds limit {self.limits.max_order_value_cad:,.2f}.")

        # 3. Fat Finger Price Check (vs LTP)
        if order.last_traded_price > 0:
            deviation = abs(order.price - order.last_traded_price) / order.last_traded_price
            if deviation > self.limits.max_price_deviation_pct:
                violations.append(ViolationCode.FAT_FINGER_PRICE)
                reasons.append(f"Price {order.price} deviates {deviation:.2%} from LTP {order.last_traded_price}.")

        # 4. Short Selling Marking Compliance (UMIR Rule 3.1)
        if order.side == OrderSide.SELL and order.current_inventory < order.quantity:
            violations.append(ViolationCode.IMPROPER_SHORT_MARK)
            reasons.append(f"Sell order for {order.quantity} but inventory is only {order.current_inventory}. Must be marked SELL_SHORT.")

        if violations:
            logger.warning(f"Order {order.order_id} REJECTED by CIRO Risk Engine: {' | '.join(reasons)}")
            return ComplianceResult(is_compliant=False, violations=violations, reason=" | ".join(reasons))

        return ComplianceResult(is_compliant=True, violations=[], reason="Approved")
