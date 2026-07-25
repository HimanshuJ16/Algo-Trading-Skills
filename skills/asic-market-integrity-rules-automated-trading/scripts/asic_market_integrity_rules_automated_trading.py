"""
asic-market-integrity-rules-automated-trading:
Enforces ASIC Automated Order Processing (AOP) pre-trade filters and kill switches.
"""
import dataclasses
import logging
from typing import Optional

logger = logging.getLogger(__name__)


@dataclasses.dataclass
class AsicMarketIntegrityConfig:
    max_order_value_aud: float
    max_order_volume: int
    max_price_deviation_pct: float  # e.g., 0.05 for 5%


@dataclasses.dataclass
class AopOrderRequest:
    symbol: str
    price: float
    qty: int
    reference_price: float  # Last traded price or mid-price


@dataclasses.dataclass
class ComplianceResult:
    is_compliant: bool
    reason: str


class AsicKillSwitchManager:
    """Manages the mandatory ASIC AOP kill switch."""
    def __init__(self):
        self._is_triggered = False

    def trigger_kill_switch(self):
        self._is_triggered = True
        logger.critical("ASIC AOP KILL SWITCH TRIGGERED. All trading halted.")

    def reset_kill_switch(self):
        self._is_triggered = False
        logger.warning("ASIC AOP Kill Switch reset. Trading resumed.")

    @property
    def is_halted(self) -> bool:
        return self._is_triggered


class AsicAopPreTradeFilter:
    """
    Enforces ASIC pre-trade filters before an order reaches the market.
    """
    def __init__(self, config: AsicMarketIntegrityConfig, kill_switch: AsicKillSwitchManager):
        self.config = config
        self.kill_switch = kill_switch

    def run_checks(self, order: AopOrderRequest) -> ComplianceResult:
        # 1. Kill Switch Check (Highest Priority)
        if self.kill_switch.is_halted:
            return ComplianceResult(False, "REJECTED: ASIC AOP Kill Switch is currently active.")

        # 2. Basic Sanity Checks
        if order.qty <= 0 or order.price <= 0:
            return ComplianceResult(False, "REJECTED: Invalid order quantity or price.")

        # 3. Maximum Volume Check
        if order.qty > self.config.max_order_volume:
            return ComplianceResult(False, f"REJECTED: Order volume ({order.qty}) exceeds AOP limit ({self.config.max_order_volume}).")

        # 4. Maximum Value Check
        order_value = order.price * order.qty
        if order_value > self.config.max_order_value_aud:
            return ComplianceResult(False, f"REJECTED: Order value (${order_value:,.2f}) exceeds AOP limit (${self.config.max_order_value_aud:,.2f}).")

        # 5. Price Deviation Check (Fat Finger / Market Integrity)
        deviation = abs(order.price - order.reference_price) / order.reference_price
        if deviation > self.config.max_price_deviation_pct:
            return ComplianceResult(False, f"REJECTED: Price deviation ({deviation:.1%}) exceeds AOP limit ({self.config.max_price_deviation_pct:.1%}).")

        logger.info(f"ASIC Pre-Trade Filter Passed: {order.symbol} {order.qty} @ {order.price}")
        return ComplianceResult(True, "APPROVED: Order passed all ASIC AOP pre-trade filters.")
