"""
broker-order-type-capability-matrix: Capability matrix registry for native broker order types
and software-emulated order synthesizer fallback.
"""
from dataclasses import dataclass, field
import logging
from enum import Enum
from typing import Any, Dict, List, Optional, Set

logger = logging.getLogger(__name__)


class OrderType(Enum):
    MARKET = "MARKET"
    LIMIT = "LIMIT"
    STOP_LOSS = "STOP_LOSS"
    STOP_LIMIT = "STOP_LIMIT"
    TRAILING_STOP = "TRAILING_STOP"
    BRACKET = "BRACKET"
    OCO = "OCO"
    ICEBERG = "ICEBERG"


@dataclass
class BrokerCapabilities:
    broker_name: str
    native_order_types: Set[OrderType]
    supports_bracket: bool
    supports_oco: bool
    supports_iceberg: bool


@dataclass
class SynthesizedOrderPlan:
    is_native: bool
    primary_order_type: OrderType
    emulated_legs: List[Dict[str, Any]]
    description: str


# Default capability matrix for major integrated brokers
DEFAULT_CAPABILITIES: Dict[str, BrokerCapabilities] = {
    "ibkr": BrokerCapabilities(
        broker_name="ibkr",
        native_order_types={
            OrderType.MARKET, OrderType.LIMIT, OrderType.STOP_LOSS,
            OrderType.STOP_LIMIT, OrderType.TRAILING_STOP, OrderType.BRACKET,
            OrderType.OCO, OrderType.ICEBERG,
        },
        supports_bracket=True,
        supports_oco=True,
        supports_iceberg=True,
    ),
    "alpaca": BrokerCapabilities(
        broker_name="alpaca",
        native_order_types={
            OrderType.MARKET, OrderType.LIMIT, OrderType.STOP_LOSS,
            OrderType.STOP_LIMIT, OrderType.TRAILING_STOP, OrderType.BRACKET, OrderType.OCO,
        },
        supports_bracket=True,
        supports_oco=True,
        supports_iceberg=False,
    ),
    "zerodha": BrokerCapabilities(
        broker_name="zerodha",
        native_order_types={
            OrderType.MARKET, OrderType.LIMIT, OrderType.STOP_LOSS, OrderType.STOP_LIMIT,
        },
        supports_bracket=False,
        supports_oco=False,
        supports_iceberg=False,
    ),
    "binance": BrokerCapabilities(
        broker_name="binance",
        native_order_types={
            OrderType.MARKET, OrderType.LIMIT, OrderType.STOP_LOSS, OrderType.STOP_LIMIT, OrderType.OCO,
        },
        supports_bracket=False,
        supports_oco=True,
        supports_iceberg=False,
    ),
}


class BrokerOrderCapabilityMatrix:
    """
    Registry for native broker order type capabilities with software-emulated
    order synthesizer fallback.
    """

    def __init__(self, custom_matrix: Optional[Dict[str, BrokerCapabilities]] = None):
        self.matrix = custom_matrix or DEFAULT_CAPABILITIES

    def register_broker(self, capabilities: BrokerCapabilities) -> None:
        """Registers or overrides a broker capability profile."""
        self.matrix[capabilities.broker_name.lower()] = capabilities

    def supports_native(self, broker_name: str, order_type: OrderType) -> bool:
        """Checks if a broker natively supports a specific order type."""
        b_key = broker_name.lower()
        if b_key not in self.matrix:
            return False
        return order_type in self.matrix[b_key].native_order_types

    def plan_order_execution(
        self,
        broker_name: str,
        requested_order_type: OrderType,
        symbol: str,
        action: str,
        quantity: float,
        price: Optional[float] = None,
        stop_loss_price: Optional[float] = None,
        take_profit_price: Optional[float] = None,
    ) -> SynthesizedOrderPlan:
        """
        Plans order execution: uses native order if supported, else synthesizes
        emulated order legs.
        """
        b_key = broker_name.lower()
        if self.supports_native(b_key, requested_order_type):
            return SynthesizedOrderPlan(
                is_native=True,
                primary_order_type=requested_order_type,
                emulated_legs=[],
                description=f"Native {requested_order_type.value} order supported by {broker_name}.",
            )

        # Software Emulation for Bracket / OCO orders
        if requested_order_type == OrderType.BRACKET:
            emulated_legs = []
            if stop_loss_price:
                emulated_legs.append({
                    "leg_type": "STOP_LOSS",
                    "trigger_price": stop_loss_price,
                    "action": "SELL" if action.upper() == "BUY" else "BUY",
                })
            if take_profit_price:
                emulated_legs.append({
                    "leg_type": "TAKE_PROFIT",
                    "trigger_price": take_profit_price,
                    "action": "SELL" if action.upper() == "BUY" else "BUY",
                })

            logger.info(f"Synthesized software Bracket order for {broker_name}: primary={action} {quantity} {symbol}")
            return SynthesizedOrderPlan(
                is_native=False,
                primary_order_type=OrderType.LIMIT if price else OrderType.MARKET,
                emulated_legs=emulated_legs,
                description=f"Software-emulated BRACKET order for {broker_name} (2 local trigger legs).",
            )

        if requested_order_type == OrderType.ICEBERG:
            logger.info(f"Synthesized software Iceberg order for {broker_name}")
            return SynthesizedOrderPlan(
                is_native=False,
                primary_order_type=OrderType.LIMIT,
                emulated_legs=[{"leg_type": "SLICE_FEEDER", "slice_qty": quantity / 5.0}],
                description=f"Software-emulated ICEBERG order for {broker_name} (5 slices).",
            )

        raise ValueError(f"Unsupported order type {requested_order_type.value} for broker '{broker_name}'.")
