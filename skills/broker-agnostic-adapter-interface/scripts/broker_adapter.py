"""
broker-agnostic-adapter-interface: Production-grade unified broker adapter interface,
standardized domain models (OrderRequest, OrderResult, Position, AccountBalance),
normalized enums, and pluggable broker factory registry.
"""
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from enum import Enum
import logging
import time
from typing import Any, Dict, List, Optional, Type

logger = logging.getLogger(__name__)


class OrderSide(str, Enum):
    BUY = "BUY"
    SELL = "SELL"


class OrderType(str, Enum):
    MARKET = "MARKET"
    LIMIT = "LIMIT"


class OrderStatus(str, Enum):
    PENDING = "PENDING"
    FILLED = "FILLED"
    REJECTED = "REJECTED"
    CANCELLED = "CANCELLED"


@dataclass
class OrderRequest:
    symbol: str
    side: OrderSide
    order_type: OrderType
    quantity: float
    price: float = 0.0
    client_order_id: Optional[str] = None


@dataclass
class OrderResult:
    order_id: str
    broker_name: str
    status: OrderStatus
    filled_quantity: float
    average_price: float
    message: str = ""
    timestamp: float = field(default_factory=time.time)


@dataclass
class Position:
    symbol: str
    quantity: float
    average_price: float
    unrealized_pnl: float = 0.0


@dataclass
class AccountBalance:
    cash_available: float
    margin_used: float
    total_equity: float
    currency: str = "USD"


class BaseBrokerAdapter(ABC):
    """Abstract interface defining the contract for all broker execution adapters."""

    @property
    @abstractmethod
    def broker_name(self) -> str:
        pass

    @abstractmethod
    def place_order(self, request: OrderRequest) -> OrderResult:
        pass

    @abstractmethod
    def cancel_order(self, order_id: str) -> bool:
        pass

    @abstractmethod
    def get_positions(self) -> List[Position]:
        pass

    @abstractmethod
    def get_account_balance(self) -> AccountBalance:
        pass


class MockZerodhaAdapter(BaseBrokerAdapter):
    """Zerodha Kite Connect adapter implementation."""

    @property
    def broker_name(self) -> str:
        return "zerodha"

    def place_order(self, request: OrderRequest) -> OrderResult:
        order_id = f"Z_{int(time.time()*1000)}"
        return OrderResult(
            order_id=order_id,
            broker_name=self.broker_name,
            status=OrderStatus.FILLED,
            filled_quantity=request.quantity,
            average_price=request.price if request.price > 0 else 100.0,
            message="Order executed via Zerodha Kite Connect",
        )

    def cancel_order(self, order_id: str) -> bool:
        return True

    def get_positions(self) -> List[Position]:
        return [Position(symbol="INFY", quantity=50, average_price=1500.0, unrealized_pnl=250.0)]

    def get_account_balance(self) -> AccountBalance:
        return AccountBalance(cash_available=100000.0, margin_used=20000.0, total_equity=120000.0, currency="INR")


class MockAlpacaAdapter(BaseBrokerAdapter):
    """Alpaca Trading API adapter implementation."""

    @property
    def broker_name(self) -> str:
        return "alpaca"

    def place_order(self, request: OrderRequest) -> OrderResult:
        order_id = f"ALP_{int(time.time()*1000)}"
        return OrderResult(
            order_id=order_id,
            broker_name=self.broker_name,
            status=OrderStatus.FILLED,
            filled_quantity=request.quantity,
            average_price=request.price if request.price > 0 else 250.0,
            message="Order executed via Alpaca Trading API",
        )

    def cancel_order(self, order_id: str) -> bool:
        return True

    def get_positions(self) -> List[Position]:
        return [Position(symbol="AAPL", quantity=10, average_price=180.0, unrealized_pnl=50.0)]

    def get_account_balance(self) -> AccountBalance:
        return AccountBalance(cash_available=50000.0, margin_used=5000.0, total_equity=55000.0, currency="USD")


class BrokerAdapterFactory:
    """Factory registry for instantiating broker adapters."""

    _registry: Dict[str, Type[BaseBrokerAdapter]] = {
        "zerodha": MockZerodhaAdapter,
        "alpaca": MockAlpacaAdapter,
    }

    @classmethod
    def register(cls, name: str, adapter_cls: Type[BaseBrokerAdapter]):
        cls._registry[name.lower()] = adapter_cls

    @classmethod
    def create(cls, name: str, **kwargs) -> BaseBrokerAdapter:
        key = name.lower()
        if key not in cls._registry:
            raise KeyError(f"Broker adapter '{name}' not found in factory registry. Available: {list(cls._registry.keys())}")
        return cls._registry[key](**kwargs)
