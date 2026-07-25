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
    PEGGED = "PEGGED"
    MOC = "MOC"
    TWAP = "TWAP"
    VWAP = "VWAP"


@dataclass
class BrokerCapabilities:
    broker_name: str
    native_order_types: Set[OrderType]
    supports_fractional: bool = False
    supports_bracket: bool = False
    supports_oco: bool = False
    supports_iceberg: bool = False
    supports_pegged: bool = False
    supports_algorithmic: bool = False


@dataclass
class EmulatedLeg:
    leg_type: str
    action: str
    quantity: float
    trigger_price: Optional[float] = None
    limit_price: Optional[float] = None
    slice_qty: Optional[float] = None
    interval_seconds: Optional[int] = None
    metadata: Dict[str, Any] = field(default_factory=dict)


@dataclass
class SynthesizedOrderPlan:
    is_native: bool
    primary_order_type: OrderType
    primary_action: str
    primary_quantity: float
    emulated_legs: List[EmulatedLeg]
    description: str


# Default capability matrix for major integrated brokers
DEFAULT_CAPABILITIES: Dict[str, BrokerCapabilities] = {
    "ibkr": BrokerCapabilities(
        broker_name="ibkr",
        native_order_types={
            OrderType.MARKET, OrderType.LIMIT, OrderType.STOP_LOSS,
            OrderType.STOP_LIMIT, OrderType.TRAILING_STOP, OrderType.BRACKET,
            OrderType.OCO, OrderType.ICEBERG, OrderType.PEGGED, OrderType.MOC,
            OrderType.TWAP, OrderType.VWAP,
        },
        supports_fractional=True,
        supports_bracket=True,
        supports_oco=True,
        supports_iceberg=True,
        supports_pegged=True,
        supports_algorithmic=True,
    ),
    "alpaca": BrokerCapabilities(
        broker_name="alpaca",
        native_order_types={
            OrderType.MARKET, OrderType.LIMIT, OrderType.STOP_LOSS,
            OrderType.STOP_LIMIT, OrderType.TRAILING_STOP, OrderType.BRACKET, OrderType.OCO,
            OrderType.MOC,
        },
        supports_fractional=True,
        supports_bracket=True,
        supports_oco=True,
        supports_iceberg=False,
        supports_pegged=False,
        supports_algorithmic=False,
    ),
    "zerodha": BrokerCapabilities(
        broker_name="zerodha",
        native_order_types={
            OrderType.MARKET, OrderType.LIMIT, OrderType.STOP_LOSS, OrderType.STOP_LIMIT, OrderType.ICEBERG,
        },
        supports_fractional=False,
        supports_bracket=False,  
        supports_oco=False,
        supports_iceberg=True,
        supports_pegged=False,
        supports_algorithmic=False,
    ),
    "binance": BrokerCapabilities(
        broker_name="binance",
        native_order_types={
            OrderType.MARKET, OrderType.LIMIT, OrderType.STOP_LOSS, OrderType.STOP_LIMIT, OrderType.OCO, OrderType.TWAP, OrderType.ICEBERG,
        },
        supports_fractional=True,
        supports_bracket=False,
        supports_oco=True,
        supports_iceberg=True,
        supports_pegged=False,
        supports_algorithmic=True,
    ),
}

class BrokerOrderCapabilityMatrix:
    """
    Registry for native broker order type capabilities with software-emulated
    order synthesizer fallback for advanced execution algos and multi-leg orders.
    """

    def __init__(self, custom_matrix: Optional[Dict[str, BrokerCapabilities]] = None):
        self.matrix = custom_matrix or DEFAULT_CAPABILITIES.copy()

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
        iceberg_slices: Optional[int] = None,
        twap_duration_minutes: Optional[int] = None,
    ) -> SynthesizedOrderPlan:
        """
        Plans order execution: uses native order if supported, else synthesizes
        emulated order legs to be managed by a local Execution Management System (EMS).
        """
        b_key = broker_name.lower()
        if b_key not in self.matrix:
            raise ValueError(f"Broker '{broker_name}' is not registered in the capability matrix.")
        
        action = action.upper()
        if action not in ("BUY", "SELL"):
            raise ValueError(f"Action must be BUY or SELL, got {action}")

        inverse_action = "SELL" if action == "BUY" else "BUY"

        # 1. Native Routing (If Supported)
        if self.supports_native(b_key, requested_order_type):
            return SynthesizedOrderPlan(
                is_native=True,
                primary_order_type=requested_order_type,
                primary_action=action,
                primary_quantity=quantity,
                emulated_legs=[],
                description=f"Native {requested_order_type.value} order routed to {broker_name}.",
            )

        # 2. Software Emulation Synthesizers
        emulated_legs = []
        
        if requested_order_type == OrderType.BRACKET:
            if not stop_loss_price and not take_profit_price:
                raise ValueError("BRACKET requires at least one of stop_loss_price or take_profit_price.")
            
            if stop_loss_price:
                emulated_legs.append(EmulatedLeg(
                    leg_type="STOP_LOSS",
                    action=inverse_action,
                    quantity=quantity,
                    trigger_price=stop_loss_price
                ))
            if take_profit_price:
                emulated_legs.append(EmulatedLeg(
                    leg_type="TAKE_PROFIT",
                    action=inverse_action,
                    quantity=quantity,
                    limit_price=take_profit_price
                ))

            logger.info(f"Synthesized Bracket order for {broker_name}: primary={action} {quantity} {symbol}")
            return SynthesizedOrderPlan(
                is_native=False,
                primary_order_type=OrderType.LIMIT if price else OrderType.MARKET,
                primary_action=action,
                primary_quantity=quantity,
                emulated_legs=emulated_legs,
                description=f"Software-emulated BRACKET order for {broker_name} (managed locally).",
            )

        elif requested_order_type == OrderType.OCO:
            if not stop_loss_price or not take_profit_price:
                raise ValueError("OCO requires both stop_loss_price and take_profit_price.")
                
            emulated_legs.extend([
                EmulatedLeg(leg_type="STOP_LOSS", action=action, quantity=quantity, trigger_price=stop_loss_price),
                EmulatedLeg(leg_type="LIMIT_PROFIT", action=action, quantity=quantity, limit_price=take_profit_price)
            ])
            return SynthesizedOrderPlan(
                is_native=False,
                primary_order_type=OrderType.OCO,
                primary_action=action,
                primary_quantity=quantity,
                emulated_legs=emulated_legs,
                description=f"Software-emulated OCO order for {broker_name} (mutually exclusive triggers).",
            )

        elif requested_order_type == OrderType.ICEBERG:
            slices = iceberg_slices or 5
            slice_qty = quantity / slices
            emulated_legs.append(EmulatedLeg(
                leg_type="SLICE_FEEDER",
                action=action,
                quantity=quantity,
                slice_qty=slice_qty,
                metadata={"total_slices": slices}
            ))
            logger.info(f"Synthesized Iceberg order for {broker_name} with {slices} slices.")
            return SynthesizedOrderPlan(
                is_native=False,
                primary_order_type=OrderType.LIMIT,
                primary_action=action,
                primary_quantity=slice_qty, 
                emulated_legs=emulated_legs,
                description=f"Software-emulated ICEBERG order for {broker_name} ({slices} slices).",
            )
            
        elif requested_order_type == OrderType.TWAP:
            duration = twap_duration_minutes or 60
            intervals = 10
            interval_seconds = (duration * 60) // intervals
            slice_qty = quantity / intervals
            emulated_legs.append(EmulatedLeg(
                leg_type="TWAP_FEEDER",
                action=action,
                quantity=quantity,
                slice_qty=slice_qty,
                interval_seconds=interval_seconds
            ))
            return SynthesizedOrderPlan(
                is_native=False,
                primary_order_type=OrderType.MARKET, 
                primary_action=action,
                primary_quantity=slice_qty,
                emulated_legs=emulated_legs,
                description=f"Software-emulated TWAP order over {duration} minutes.",
            )

        raise ValueError(f"Unsupported and non-emulatable order type {requested_order_type.value} for broker '{broker_name}'.")
