"""
broker-agnostic-adapter-interface:
A unified adapter contract that decouples strategy logic from broker SDKs —
standard order models in ``Decimal``, a typed exception hierarchy, normalized
cross-venue status mapping, and a factory registry.

Two design decisions here exist because the obvious alternatives lose money:

  1. **An unrecognised broker status becomes ``OrderStatus.UNKNOWN``, never
     ``PENDING``.** Defaulting an unmapped string to PENDING tells the strategy
     "this order is live and working", which is the single most dangerous thing
     you can say about an order you cannot classify. Real brokers ship statuses
     that a hand-written map will miss — Zerodha's ``LAPSED``, IBKR's
     ``ApiCancelled``, Alpaca's ``done_for_day`` are all terminal, and reporting
     any of them as PENDING leaves the strategy waiting forever on a dead order
     or re-sending one it thinks never went through. ``UNKNOWN`` forces the
     caller to re-query or reconcile instead of guessing.
  2. **The simulated adapters are not registered under production broker names
     by default.** ``BrokerAdapterFactory.create("zerodha")`` raises until a real
     adapter is registered. The simulated adapters fabricate fills; binding them
     to the name a config file will supply is how a paper implementation reaches
     production and reports every order FILLED at an invented price. Opt in
     explicitly with ``BrokerAdapterFactory.register_simulated_adapters()``.

Scope: this module defines the *contract* and a set of simulated adapters that
demonstrate it. It performs no network I/O. Real adapters must wrap their SDK's
exceptions into this module's hierarchy so nothing broker-specific escapes the
boundary.

See ``references/standards.md`` for each broker's documented status set and the
sources behind the mappings below.
"""
import logging
import time
import uuid
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
from enum import Enum
from typing import Dict, List, Optional, Type, Union

logger = logging.getLogger(__name__)


# --- Exceptions ---
class BrokerAdapterError(Exception):
    """Base exception for all broker adapter errors."""


class OrderExecutionError(BrokerAdapterError):
    """Raised when an order placement fails or the request is invalid."""


class AuthenticationError(BrokerAdapterError):
    """Raised when adapter fails to authenticate with the broker API."""


class NetworkError(BrokerAdapterError):
    """Raised when network issues occur while communicating with the broker API."""


class AdapterRegistrationError(BrokerAdapterError):
    """Raised when a broker adapter cannot be registered or resolved."""


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
    """
    Normalized order status across venues.

    ``UNKNOWN`` is not a broker state — it is this layer admitting it could not
    classify one. Treat it as "re-query or reconcile before acting". Never treat
    it as live and never treat it as terminal.
    """
    PENDING = "PENDING"
    FILLED = "FILLED"
    PARTIALLY_FILLED = "PARTIALLY_FILLED"
    REJECTED = "REJECTED"
    CANCELLED = "CANCELLED"
    EXPIRED = "EXPIRED"
    UNKNOWN = "UNKNOWN"


# Statuses after which no further updates are expected. UNKNOWN is deliberately
# excluded: an unclassifiable order is not known to be finished.
TERMINAL_STATUSES = frozenset({
    OrderStatus.FILLED, OrderStatus.REJECTED,
    OrderStatus.CANCELLED, OrderStatus.EXPIRED,
})


# Quantities and prices accept Decimal or int. int is exact, so it is widened
# losslessly; float is refused because binary floating point cannot represent
# ordinary decimal prices exactly, which is the whole reason this module exists.
Numeric = Union[Decimal, int]


def _to_decimal(name: str, value: object, *, allow_none: bool = False) -> Optional[Decimal]:
    """
    Coerce a quantity/price to Decimal, refusing float and non-finite values.

    Accepting a float here would silently defeat the module's precision
    guarantee: it survives the comparison checks, propagates into OrderResult,
    and only surfaces later as a TypeError the first time it meets a Decimal in
    arithmetic — usually far from the code that introduced it.
    """
    if value is None:
        if allow_none:
            return None
        raise OrderExecutionError(f"{name} is required and must not be None.")
    if isinstance(value, bool):
        raise OrderExecutionError(f"{name} must be a Decimal, got bool {value!r}")
    if isinstance(value, float):
        raise OrderExecutionError(
            f"{name} must be a Decimal, got float {value!r}. Binary floats cannot represent "
            f"decimal prices and tick sizes exactly; construct it as Decimal(str(value)) at "
            f"the boundary instead of letting the imprecision through."
        )
    if isinstance(value, int):
        value = Decimal(value)
    if not isinstance(value, Decimal):
        raise OrderExecutionError(f"{name} must be a Decimal, got {type(value).__name__}")
    # NaN and Infinity raise InvalidOperation on comparison rather than returning
    # False, so they must be rejected before any threshold test.
    if not value.is_finite():
        raise OrderExecutionError(f"{name} must be finite, got {value!r}")
    return value


# --- Data Models ---
@dataclass
class OrderRequest:
    symbol: str
    side: OrderSide
    order_type: OrderType
    quantity: Numeric
    price: Optional[Numeric] = None
    stop_price: Optional[Numeric] = None
    client_order_id: str = field(default_factory=lambda: uuid.uuid4().hex)


@dataclass
class OrderResult:
    """
    Normalized outcome of an order operation.

    ``client_order_id`` echoes the request's ID. Without it the caller cannot
    correlate a response to the request that produced it, which makes retry-safe
    submission impossible — see the ``order-placement-idempotency`` skill.
    """
    order_id: str
    broker_name: str
    status: OrderStatus
    filled_quantity: Decimal
    average_price: Decimal
    message: str = ""
    timestamp: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    commission: Decimal = Decimal("0.0")
    client_order_id: str = ""

    @property
    def is_terminal(self) -> bool:
        """True when no further updates are expected. UNKNOWN is never terminal."""
        return self.status in TERMINAL_STATUSES


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

    #: Broker-native status string -> normalized OrderStatus. Subclasses override.
    #: Keys are matched case-insensitively; see ``normalize_status``.
    _STATUS_MAP: Dict[str, OrderStatus] = {}

    @property
    @abstractmethod
    def broker_name(self) -> str:
        """Return the unique identifier for this broker."""

    @abstractmethod
    def place_order(self, request: OrderRequest) -> OrderResult:
        """
        Place an order and return the normalized execution result.

        Implementations must call ``self._validate_request(request)`` first and
        must wrap SDK exceptions in this module's hierarchy.
        """

    @abstractmethod
    def cancel_order(self, order_id: str) -> bool:
        """
        **Request** cancellation of an order.

        Returns True when the broker *accepted the cancellation request*, which
        is not the same as the order being cancelled. Most venues answer a cancel
        request asynchronously and may refuse it — an order can fill in the race
        window between the request and its acknowledgement. Confirm the outcome
        with ``get_order_status`` before treating the order as dead, and never
        release its risk budget on a True return alone.
        """

    @abstractmethod
    def get_order_status(self, order_id: str) -> OrderResult:
        """Query the latest status of an order."""

    @abstractmethod
    def get_positions(self) -> List[Position]:
        """Fetch all current open positions."""

    @abstractmethod
    def get_account_balance(self) -> AccountBalance:
        """Fetch real-time account balances and margin utilization."""

    def normalize_status(self, broker_status: str) -> OrderStatus:
        """
        Map a broker-native status string to the standardized enum.

        An unmapped status returns ``OrderStatus.UNKNOWN`` and logs at ERROR. It
        deliberately does **not** fall back to PENDING: brokers add and rename
        statuses, and silently calling an unclassified order "live" is how a
        terminal state (Zerodha ``LAPSED``, IBKR ``ApiCancelled``, Alpaca
        ``done_for_day``) gets mistaken for a working order.

        Override only to change the lookup strategy; extend ``_STATUS_MAP`` to
        add statuses.
        """
        if not isinstance(broker_status, str):
            logger.error(
                f"{self.broker_name}: non-string status {broker_status!r}; returning UNKNOWN."
            )
            return OrderStatus.UNKNOWN
        mapped = self._STATUS_MAP.get(broker_status.strip().upper())
        if mapped is None:
            logger.error(
                f"{self.broker_name}: unmapped order status {broker_status!r}. Returning UNKNOWN "
                f"— re-query or reconcile this order; do not assume it is working. Add the "
                f"status to {type(self).__name__}._STATUS_MAP once its meaning is confirmed."
            )
            return OrderStatus.UNKNOWN
        return mapped

    def _validate_request(self, request: OrderRequest) -> OrderRequest:
        """
        Validate and normalize an order request before it reaches the broker.

        Lives on the base class so a new adapter cannot forget it. Returns a
        request whose numeric fields are guaranteed Decimal and finite, so
        implementations can use them without re-checking.

        Raises:
            OrderExecutionError: the request would be rejected on protocol or
                sanity grounds. Raised rather than returned so a malformed order
                can never be silently reshaped into a valid-looking one.
        """
        if not isinstance(request, OrderRequest):
            raise OrderExecutionError(
                f"request must be an OrderRequest, got {type(request).__name__}"
            )
        if not isinstance(request.symbol, str) or not request.symbol.strip():
            raise OrderExecutionError("Order symbol must be a non-empty string.")
        if not isinstance(request.side, OrderSide):
            raise OrderExecutionError(f"side must be an OrderSide, got {request.side!r}")
        if not isinstance(request.order_type, OrderType):
            raise OrderExecutionError(
                f"order_type must be an OrderType, got {request.order_type!r}"
            )

        try:
            quantity = _to_decimal("quantity", request.quantity)
            price = _to_decimal("price", request.price, allow_none=True)
            stop_price = _to_decimal("stop_price", request.stop_price, allow_none=True)
        except InvalidOperation as exc:  # pragma: no cover - defensive
            raise OrderExecutionError(f"Invalid decimal value in order request: {exc}") from exc

        if quantity <= Decimal("0"):
            raise OrderExecutionError("Quantity must be greater than zero.")

        needs_price = request.order_type in (OrderType.LIMIT, OrderType.STOP_LIMIT)
        needs_stop = request.order_type in (OrderType.STOP, OrderType.STOP_LIMIT)

        if needs_price:
            # `if request.price` would treat Decimal("0") as absent and silently
            # substitute a default, turning a bad limit price into a plausible one.
            if price is None:
                raise OrderExecutionError(
                    f"{request.order_type.value} orders require an explicit price."
                )
            if price <= Decimal("0"):
                raise OrderExecutionError(f"Limit price must be positive, got {price}.")
        elif request.order_type is OrderType.MARKET and price is not None:
            raise OrderExecutionError(
                "MARKET orders must not carry a price; supplying one hides the caller's intent."
            )

        if needs_stop:
            if stop_price is None:
                raise OrderExecutionError(
                    f"{request.order_type.value} orders require an explicit stop_price."
                )
            if stop_price <= Decimal("0"):
                raise OrderExecutionError(f"Stop price must be positive, got {stop_price}.")

        request.quantity = quantity
        request.price = price
        request.stop_price = stop_price
        return request


# --- Simulated Implementations ---
# These fabricate responses. They demonstrate the contract and back the test
# suite; they are NOT registered under production broker names by default.
class MockZerodhaAdapter(BaseBrokerAdapter):
    """
    Simulated Zerodha Kite Connect adapter.

    Status set per Kite Connect's documented order lifecycle. Orders traverse
    several interim states before reaching a final one; ``LAPSED`` is terminal
    (the order expired without executing) and must not be read as still working.
    """

    _STATUS_MAP = {
        "COMPLETE": OrderStatus.FILLED,
        "REJECTED": OrderStatus.REJECTED,
        "CANCELLED": OrderStatus.CANCELLED,
        "LAPSED": OrderStatus.EXPIRED,
        "OPEN": OrderStatus.PENDING,
        "OPEN PENDING": OrderStatus.PENDING,
        "VALIDATION PENDING": OrderStatus.PENDING,
        "PUT ORDER REQ RECEIVED": OrderStatus.PENDING,
        "TRIGGER PENDING": OrderStatus.PENDING,
        "MODIFIED": OrderStatus.PENDING,
        "MODIFY PENDING": OrderStatus.PENDING,
        "MODIFY VALIDATION PENDING": OrderStatus.PENDING,
        "CANCEL PENDING": OrderStatus.PENDING,
        "AMO REQ RECEIVED": OrderStatus.PENDING,
    }

    @property
    def broker_name(self) -> str:
        return "zerodha"

    def place_order(self, request: OrderRequest) -> OrderResult:
        request = self._validate_request(request)
        broker_status = "COMPLETE"
        order_id = f"Z_{int(time.time() * 1000)}"
        return OrderResult(
            order_id=order_id,
            broker_name=self.broker_name,
            status=self.normalize_status(broker_status),
            filled_quantity=request.quantity,
            average_price=request.price if request.price is not None else Decimal("100.50"),
            message="Simulated fill — Zerodha Kite Connect",
            commission=Decimal("20.0"),
            client_order_id=request.client_order_id,
        )

    def cancel_order(self, order_id: str) -> bool:
        return True

    def get_order_status(self, order_id: str) -> OrderResult:
        return OrderResult(
            order_id=order_id,
            broker_name=self.broker_name,
            status=OrderStatus.FILLED,
            filled_quantity=Decimal("0"),
            average_price=Decimal("0.0"),
        )

    def get_positions(self) -> List[Position]:
        return [Position(symbol="INFY", quantity=Decimal("50"),
                         average_price=Decimal("1500.00"), unrealized_pnl=Decimal("250.00"))]

    def get_account_balance(self) -> AccountBalance:
        return AccountBalance(
            cash_available=Decimal("100000.00"),
            margin_used=Decimal("20000.00"),
            total_equity=Decimal("120000.00"),
            currency="INR",
            buying_power=Decimal("500000.00"),
        )


class MockAlpacaAdapter(BaseBrokerAdapter):
    """Simulated Alpaca Trading API adapter, mapping Alpaca's documented statuses."""

    _STATUS_MAP = {
        "FILLED": OrderStatus.FILLED,
        "PARTIALLY_FILLED": OrderStatus.PARTIALLY_FILLED,
        "REJECTED": OrderStatus.REJECTED,
        "CANCELED": OrderStatus.CANCELLED,
        "EXPIRED": OrderStatus.EXPIRED,
        "REPLACED": OrderStatus.CANCELLED,
        # done_for_day: finished for the session, no further updates today.
        "DONE_FOR_DAY": OrderStatus.EXPIRED,
        "NEW": OrderStatus.PENDING,
        "PENDING_NEW": OrderStatus.PENDING,
        "ACCEPTED": OrderStatus.PENDING,
        "PENDING_CANCEL": OrderStatus.PENDING,
        "PENDING_REPLACE": OrderStatus.PENDING,
        "SUSPENDED": OrderStatus.PENDING,
        "STOPPED": OrderStatus.PENDING,
        "CALCULATED": OrderStatus.PENDING,
        "ACCEPTED_FOR_BIDDING": OrderStatus.PENDING,
    }

    @property
    def broker_name(self) -> str:
        return "alpaca"

    def place_order(self, request: OrderRequest) -> OrderResult:
        request = self._validate_request(request)
        broker_status = "filled"
        order_id = f"ALP_{int(time.time() * 1000)}"
        return OrderResult(
            order_id=order_id,
            broker_name=self.broker_name,
            status=self.normalize_status(broker_status),
            filled_quantity=request.quantity,
            average_price=request.price if request.price is not None else Decimal("250.00"),
            message="Simulated fill — Alpaca Trading API",
            commission=Decimal("0.0"),
            client_order_id=request.client_order_id,
        )

    def cancel_order(self, order_id: str) -> bool:
        return True

    def get_order_status(self, order_id: str) -> OrderResult:
        return OrderResult(
            order_id=order_id,
            broker_name=self.broker_name,
            status=OrderStatus.FILLED,
            filled_quantity=Decimal("0"),
            average_price=Decimal("0.0"),
        )

    def get_positions(self) -> List[Position]:
        return [Position(symbol="AAPL", quantity=Decimal("10"),
                         average_price=Decimal("180.00"), unrealized_pnl=Decimal("50.00"))]

    def get_account_balance(self) -> AccountBalance:
        return AccountBalance(
            cash_available=Decimal("50000.00"),
            margin_used=Decimal("5000.00"),
            total_equity=Decimal("55000.00"),
            currency="USD",
            buying_power=Decimal("100000.00"),
        )


class MockIBKRAdapter(BaseBrokerAdapter):
    """
    Simulated Interactive Brokers TWS API adapter.

    IBKR's status strings are mixed case (``PreSubmitted``, ``ApiCancelled``);
    the base class upper-cases before lookup so the map is written upper-case and
    matching is case-insensitive. A case-sensitive lookup would send ``"FILLED"``
    to the unmapped branch while ``"Filled"`` resolved correctly.
    """

    _STATUS_MAP = {
        "FILLED": OrderStatus.FILLED,
        "CANCELLED": OrderStatus.CANCELLED,
        "APICANCELLED": OrderStatus.CANCELLED,
        # Inactive: the order is not working — commonly a rejection or an invalid
        # order that TWS did not accept.
        "INACTIVE": OrderStatus.REJECTED,
        "SUBMITTED": OrderStatus.PENDING,
        "PRESUBMITTED": OrderStatus.PENDING,
        "PENDINGSUBMIT": OrderStatus.PENDING,
        "PENDINGCANCEL": OrderStatus.PENDING,
        "APIPENDING": OrderStatus.PENDING,
    }

    @property
    def broker_name(self) -> str:
        return "ibkr"

    def place_order(self, request: OrderRequest) -> OrderResult:
        request = self._validate_request(request)
        broker_status = "Submitted"
        order_id = f"IB_{int(time.time() * 1000)}"
        return OrderResult(
            order_id=order_id,
            broker_name=self.broker_name,
            status=self.normalize_status(broker_status),
            filled_quantity=Decimal("0"),
            average_price=Decimal("0.0"),
            message="Simulated acknowledgement — IBKR TWS API",
            commission=Decimal("1.00"),
            client_order_id=request.client_order_id,
        )

    def cancel_order(self, order_id: str) -> bool:
        return True

    def get_order_status(self, order_id: str) -> OrderResult:
        return OrderResult(
            order_id=order_id,
            broker_name=self.broker_name,
            status=OrderStatus.PENDING,
            filled_quantity=Decimal("0"),
            average_price=Decimal("0.0"),
        )

    def get_positions(self) -> List[Position]:
        return []

    def get_account_balance(self) -> AccountBalance:
        return AccountBalance(
            cash_available=Decimal("1000000.00"),
            margin_used=Decimal("0.00"),
            total_equity=Decimal("1000000.00"),
            currency="USD",
            buying_power=Decimal("4000000.00"),
        )


#: Simulated adapters, keyed by broker name. Registered only on explicit request.
SIMULATED_ADAPTERS: Dict[str, Type[BaseBrokerAdapter]] = {
    "zerodha": MockZerodhaAdapter,
    "alpaca": MockAlpacaAdapter,
    "ibkr": MockIBKRAdapter,
}


class BrokerAdapterFactory:
    """
    Factory registry for instantiating broker adapters by configuration string.

    **The registry starts empty.** The simulated adapters above are deliberately
    not pre-registered under their production broker names: they fabricate fills
    and report every order complete. A system that resolves its adapter from
    ``config["broker"]`` would otherwise pick up a mock and trade against
    invented prices with no error anywhere. Call
    ``register_simulated_adapters()`` to opt in for demos and tests.

    The registry is class-level shared state; ``register`` mutates it process
    wide. Use ``reset()`` between tests to avoid leaking registrations.
    """

    _registry: Dict[str, Type[BaseBrokerAdapter]] = {}

    @classmethod
    def register(cls, name: str, adapter_cls: Type[BaseBrokerAdapter]) -> None:
        """
        Register an adapter class under a config key.

        Raises:
            AdapterRegistrationError: the name is empty or the class does not
                implement ``BaseBrokerAdapter``. Checked here rather than at
                ``create`` time so a bad registration fails at wiring time, not
                on the first order.
        """
        if not isinstance(name, str) or not name.strip():
            raise AdapterRegistrationError("Adapter name must be a non-empty string.")
        if not (isinstance(adapter_cls, type) and issubclass(adapter_cls, BaseBrokerAdapter)):
            raise AdapterRegistrationError(
                f"{adapter_cls!r} does not implement BaseBrokerAdapter and cannot be registered "
                f"under '{name}'."
            )
        key = name.strip().lower()
        if key in cls._registry and cls._registry[key] is not adapter_cls:
            logger.warning(
                f"Broker adapter '{key}' is already registered to "
                f"{cls._registry[key].__name__}; replacing with {adapter_cls.__name__}."
            )
        cls._registry[key] = adapter_cls

    @classmethod
    def register_simulated_adapters(cls) -> None:
        """
        Opt in to the simulated adapters under their broker names.

        For demos, tests and offline development only. These adapters invent
        fills; never call this in a process that can reach a live venue.
        """
        logger.warning(
            "Registering SIMULATED broker adapters. These fabricate fills and must never be "
            "used in a process with live market access."
        )
        for name, adapter_cls in SIMULATED_ADAPTERS.items():
            cls.register(name, adapter_cls)

    @classmethod
    def reset(cls) -> None:
        """Clear the registry. Intended for test isolation."""
        cls._registry = {}

    @classmethod
    def available(cls) -> List[str]:
        """Names currently registered."""
        return sorted(cls._registry)

    @classmethod
    def create(cls, name: str, **kwargs) -> BaseBrokerAdapter:
        """
        Instantiate a registered adapter.

        Raises:
            KeyError: no adapter is registered under ``name``.
        """
        if not isinstance(name, str) or not name.strip():
            raise KeyError("Broker name must be a non-empty string.")
        key = name.strip().lower()
        if key not in cls._registry:
            raise KeyError(
                f"Broker adapter '{name}' not found in factory registry. "
                f"Registered: {cls.available() or 'none'}. The registry starts empty by design; "
                f"register your production adapter, or call register_simulated_adapters() for "
                f"the offline simulated ones."
            )
        return cls._registry[key](**kwargs)
