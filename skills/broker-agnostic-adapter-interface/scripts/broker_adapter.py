import logging
import time
import uuid
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import datetime, timezone
from decimal import Decimal
from enum import Enum
from typing import Any, Dict, List, Optional, Type

logger = logging.getLogger(__name__)


# --- Exceptions ---
class BrokerAdapterError(Exception):
    """Base exception for all broker adapter errors."""


class OrderExecutionError(BrokerAdapterError):
    """Raised when an order placement fails."""


class AuthenticationError(BrokerAdapterError):
    """Raised when adapter fails to authenticate with the broker API."""


class NetworkError(BrokerAdapterError):
    """Raised when network issues occur while communicating with the broker API."""


# --- Enums ---
class OrderSide(str, Enum):
    BUY = "BUY"
    SELL = "SELL"


class OrderType(str, Enum):
    MARKET = "MARKET"
    LIMIT = "LIMIT"
    STOP = "STOP"
    STOP_LIMIT = "STOP_LIMIT"


class OrderStatus(str, Enum):
    PENDING = "PENDING"
    FILLED = "FILLED"
    PARTIALLY_FILLED = "PARTIALLY_FILLED"
    REJECTED = "REJECTED"
    CANCELLED = "CANCELLED"
    EXPIRED = "EXPIRED"


# --- Data Models ---
@dataclass
class OrderRequest:
    symbol: str
    side: OrderSide
    order_type: OrderType
    quantity: Decimal
    price: Optional[Decimal] = None
    stop_price: Optional[Decimal] = None
    client_order_id: str = field(default_factory=lambda: uuid.uuid4().hex)


@dataclass
class OrderResult:
    order_id: str
    broker_name: str
    status: OrderStatus
    filled_quantity: Decimal
    average_price: Decimal
    message: str = ""
    timestamp: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    commission: Decimal = Decimal("0.0")


@dataclass
class Position:
    symbol: str
    quantity: Decimal
    average_price: Decimal
    current_price: Decimal = Decimal("0.0")
    unrealized_pnl: Decimal = Decimal("0.0")


@dataclass
class AccountBalance:
    cash_available: Decimal
    margin_used: Decimal
    total_equity: Decimal
    currency: str = "USD"
    buying_power: Decimal = Decimal("0.0")


# --- Interface ---
class BaseBrokerAdapter(ABC):
    """Abstract interface defining the contract for all broker execution adapters."""

    @property
    @abstractmethod
    def broker_name(self) -> str:
        """Return the unique identifier for this broker."""
        pass

    @abstractmethod
    def place_order(self, request: OrderRequest) -> OrderResult:
        """Place an order and return the normalized execution result."""
        pass

    @abstractmethod
    def cancel_order(self, order_id: str) -> bool:
        """Cancel an existing order by its broker-assigned order_id."""
        pass

    @abstractmethod
    def get_order_status(self, order_id: str) -> OrderResult:
        """Query the latest status of an order."""
        pass

    @abstractmethod
    def get_positions(self) -> List[Position]:
        """Fetch all current open positions."""
        pass

    @abstractmethod
    def get_account_balance(self) -> AccountBalance:
        """Fetch real-time account balances and margin utilization."""
        pass

    @abstractmethod
    def normalize_status(self, broker_status: str) -> OrderStatus:
        """Map a broker-specific status string to the standardized OrderStatus enum."""
        pass


# --- Concrete Implementations ---
class MockZerodhaAdapter(BaseBrokerAdapter):
    """Zerodha Kite Connect adapter implementation."""

    _STATUS_MAP = {
        "COMPLETE": OrderStatus.FILLED,
        "REJECTED": OrderStatus.REJECTED,
        "OPEN": OrderStatus.PENDING,
        "CANCELLED": OrderStatus.CANCELLED,
    }

    @property
    def broker_name(self) -> str:
        return "zerodha"

    def normalize_status(self, broker_status: str) -> OrderStatus:
        return self._STATUS_MAP.get(broker_status.upper(), OrderStatus.PENDING)

    def place_order(self, request: OrderRequest) -> OrderResult:
        if request.quantity <= Decimal("0"):
            raise OrderExecutionError("Quantity must be greater than zero.")
        
        # Simulate network logic & mock response
        broker_status = "COMPLETE"
        order_id = f"Z_{int(time.time()*1000)}"
        return OrderResult(
            order_id=order_id,
            broker_name=self.broker_name,
            status=self.normalize_status(broker_status),
            filled_quantity=request.quantity,
            average_price=request.price if request.price else Decimal("100.50"),
            message="Order executed via Zerodha Kite Connect",
            commission=Decimal("20.0")  # typical fixed flat fee in INR
        )

    def cancel_order(self, order_id: str) -> bool:
        return True

    def get_order_status(self, order_id: str) -> OrderResult:
        return OrderResult(
            order_id=order_id,
            broker_name=self.broker_name,
            status=OrderStatus.FILLED,
            filled_quantity=Decimal("0"),
            average_price=Decimal("0.0")
        )

    def get_positions(self) -> List[Position]:
        return [Position(symbol="INFY", quantity=Decimal("50"), average_price=Decimal("1500.00"), unrealized_pnl=Decimal("250.00"))]

    def get_account_balance(self) -> AccountBalance:
        return AccountBalance(
            cash_available=Decimal("100000.00"),
            margin_used=Decimal("20000.00"),
            total_equity=Decimal("120000.00"),
            currency="INR",
            buying_power=Decimal("500000.00")
        )


class MockAlpacaAdapter(BaseBrokerAdapter):
    """Alpaca Trading API adapter implementation."""

    _STATUS_MAP = {
        "filled": OrderStatus.FILLED,
        "rejected": OrderStatus.REJECTED,
        "new": OrderStatus.PENDING,
        "canceled": OrderStatus.CANCELLED,
        "partially_filled": OrderStatus.PARTIALLY_FILLED,
        "expired": OrderStatus.EXPIRED,
    }

    @property
    def broker_name(self) -> str:
        return "alpaca"

    def normalize_status(self, broker_status: str) -> OrderStatus:
        return self._STATUS_MAP.get(broker_status.lower(), OrderStatus.PENDING)

    def place_order(self, request: OrderRequest) -> OrderResult:
        if request.quantity <= Decimal("0"):
            raise OrderExecutionError("Quantity must be greater than zero.")
            
        broker_status = "filled"
        order_id = f"ALP_{int(time.time()*1000)}"
        return OrderResult(
            order_id=order_id,
            broker_name=self.broker_name,
            status=self.normalize_status(broker_status),
            filled_quantity=request.quantity,
            average_price=request.price if request.price else Decimal("250.00"),
            message="Order executed via Alpaca Trading API",
            commission=Decimal("0.0")
        )

    def cancel_order(self, order_id: str) -> bool:
        return True

    def get_order_status(self, order_id: str) -> OrderResult:
        return OrderResult(
            order_id=order_id,
            broker_name=self.broker_name,
            status=OrderStatus.FILLED,
            filled_quantity=Decimal("0"),
            average_price=Decimal("0.0")
        )

    def get_positions(self) -> List[Position]:
        return [Position(symbol="AAPL", quantity=Decimal("10"), average_price=Decimal("180.00"), unrealized_pnl=Decimal("50.00"))]

    def get_account_balance(self) -> AccountBalance:
        return AccountBalance(
            cash_available=Decimal("50000.00"),
            margin_used=Decimal("5000.00"),
            total_equity=Decimal("55000.00"),
            currency="USD",
            buying_power=Decimal("100000.00")
        )


class MockIBKRAdapter(BaseBrokerAdapter):
    """Interactive Brokers TWS API adapter implementation."""
    
    _STATUS_MAP = {
        "Submitted": OrderStatus.PENDING,
        "Filled": OrderStatus.FILLED,
        "Cancelled": OrderStatus.CANCELLED,
        "Inactive": OrderStatus.REJECTED,
    }

    @property
    def broker_name(self) -> str:
        return "ibkr"

    def normalize_status(self, broker_status: str) -> OrderStatus:
        return self._STATUS_MAP.get(broker_status, OrderStatus.PENDING)

    def place_order(self, request: OrderRequest) -> OrderResult:
        if request.quantity <= Decimal("0"):
            raise OrderExecutionError("Quantity must be greater than zero.")
            
        broker_status = "Submitted"
        order_id = f"IB_{int(time.time()*1000)}"
        return OrderResult(
            order_id=order_id,
            broker_name=self.broker_name,
            status=self.normalize_status(broker_status),
            filled_quantity=Decimal("0"),
            average_price=Decimal("0.0"),
            message="Order placed via IBKR TWS API",
            commission=Decimal("1.00")
        )

    def cancel_order(self, order_id: str) -> bool:
        return True

    def get_order_status(self, order_id: str) -> OrderResult:
        return OrderResult(
            order_id=order_id,
            broker_name=self.broker_name,
            status=OrderStatus.PENDING,
            filled_quantity=Decimal("0"),
            average_price=Decimal("0.0")
        )

    def get_positions(self) -> List[Position]:
        return []

    def get_account_balance(self) -> AccountBalance:
        return AccountBalance(
            cash_available=Decimal("1000000.00"),
            margin_used=Decimal("0.00"),
            total_equity=Decimal("1000000.00"),
            currency="USD",
            buying_power=Decimal("4000000.00")
        )


class BrokerAdapterFactory:
    """Factory registry for instantiating broker adapters."""

    _registry: Dict[str, Type[BaseBrokerAdapter]] = {
        "zerodha": MockZerodhaAdapter,
        "alpaca": MockAlpacaAdapter,
        "ibkr": MockIBKRAdapter,
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
