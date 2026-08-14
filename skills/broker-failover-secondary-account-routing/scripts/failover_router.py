"""
broker-failover-secondary-account-routing: a circuit-breaking router that moves order
flow to a secondary broker *account* when the primary degrades — without duplicating
the order, without laundering a rejection, and without turning a close into an opening
position in the wrong account.

Ordinary circuit breakers guard idempotent reads. This one guards **live order
submission across two accounts**, and that changes three things about the pattern:

  1. **A failed call is not a failed order.** When the primary times out, the order may
     already be working. Re-sending it to the secondary is not a retry — it is a second
     order, in a second account, that nothing will ever net against. Binance's REST
     documentation states the general case plainly for 5xx: "It is important to **NOT**
     treat this as a failure operation; the execution status is **UNKNOWN** and could
     have been a success." This router therefore refuses to fail over an ambiguous
     outcome. It either resolves the ambiguity through a caller-supplied resolver, or it
     raises :class:`AmbiguousOrderStateError` and makes the caller reconcile.

  2. **Not every error is an outage.** "Insufficient buying power", "invalid symbol",
     "order too large" are the primary broker's *pre-trade risk controls doing their
     job*. Failing those over to a secondary account looks for a broker that will say
     yes — which is how a rejected order becomes a filled one. Terminal rejections are
     re-raised, never rerouted, and never counted against broker health.

  3. **A probe is a real order.** In a generic circuit breaker the half-open probe is a
     cheap idempotent GET, and libraries permit several concurrently (resilience4j's
     ``permittedNumberOfCallsInHalfOpenState`` defaults to 10). Here every probe is
     capital at risk against a broker believed to be down, so probes are bounded by an
     explicit permit, defaulting to **one**.

The fourth concern is specific to *account* failover and has no analogue in endpoint
failover: **positions do not net across accounts.** A strategy long 100 shares in the
primary account that sends a closing sell to the secondary account does not flatten —
it ends the day long 100 in one account and short 100 in the other, with double the
gross exposure. Under Regulation SHO this is also a compliance event, not merely an
accounting one: Rule 200(c) provides that "[a] person shall be deemed to own securities
only to the extent that he has a net long position in such securities", Rule 200(g)
permits marking a sell order "long" only where the seller owns the security, and Rule
203(b)(1) requires a locate before accepting a short sale order. Netting across the two
accounts is available only under the independent-trading-unit aggregation conditions of
Rule 200(f), which require a documented written plan. So orders that *reduce* exposure
carry ``PositionEffect.REDUCE`` and are pinned to the account holding the position; they
are never failed over into a new position elsewhere.

Scope limits — read before wiring this into a live engine:

  - **This module does not reconcile.** It detects ambiguity and refuses to act on it.
    Resolving "did the primary accept my order?" requires the broker's order-state
    stream or an order-status query, supplied by the caller as ``order_status_resolver``.
    See ``order-placement-idempotency`` and ``webhook-based-order-fill-notifications``.
  - **Cross-broker de-duplication is impossible at the broker.** A ``client_order_id``
    protects you against a duplicate at the *same* broker; the secondary has never seen
    it. Only a local intent ledger plus reconciliation prevents a cross-account
    duplicate, which is why ambiguity here is fatal rather than retryable.
  - **This is not an idempotency layer.** Submitting the same ``client_order_id`` twice
    sends two orders; the router does not maintain an intent cache and will not
    short-circuit the second. Carrying an id is what makes reconciliation *possible*, not
    what makes submission idempotent — that belongs to ``order-placement-idempotency``.
  - **The position map is a cache, not a source of truth.** It is seeded by the caller
    via :meth:`set_position` and updated from fills this router observed. It does not
    know about positions opened elsewhere, manual trades, or corporate actions. Reseed
    it from the brokers' own position reports at least at session start.
  - **All state is in-memory and per-process.** Circuit state, positions, and counters
    do not survive a restart and are not shared across replicas. Two replicas will trip
    their breakers independently.
  - **This is not a risk control.** Pre-trade limits must sit above the router and apply
    to *both* accounts. SEC Rule 15c3-5(b) requires documented risk-management controls
    for broker-dealers with market access; (c)(1)(i) requires preventing orders that
    exceed pre-set credit or capital thresholds, and (c)(1)(ii) preventing erroneous
    orders by rejecting those outside price or size parameters. A second account is a
    second market-access path, not an exemption from either.

References (consulted while writing this module):
  - Binance Spot REST general API information: 5XX means the execution status is
    UNKNOWN; 429 rate limit and 418 IP ban both carry ``Retry-After`` in seconds.
    https://developers.binance.com/docs/binance-spot-api-docs/rest-api/general-api-information
  - RFC 9110 Section 15.6.4 (503 Service Unavailable — a temporary condition; the server
    "SHOULD generate a Retry-After header field") and Section 10.2.3 (``Retry-After``
    accepts delay-seconds **or** an HTTP-date). 429 is defined in RFC 6585, which
    RFC 9110 does not obsolete.
  - 17 CFR 242.200 (Regulation SHO): (b) ownership, (c) net long position, (f)
    independent trading unit aggregation, (g) order marking; 17 CFR 242.203(b)(1)
    locate requirement. US equities only.
  - 17 CFR 240.15c3-5(b), (c)(1)(i), (c)(1)(ii). US broker-dealers with market access.
  - Commission Delegated Regulation (EU) 2017/589 (MiFID II RTS 6) Article 14, business
    continuity arrangements for algorithmic trading systems. EU-authorised investment
    firms only.
  - M. Nygard, *Release It!*, which popularised the Circuit Breaker pattern; Fowler,
    "CircuitBreaker", https://martinfowler.com/bliki/CircuitBreaker.html
"""
import logging
import socket
import threading
import time
from dataclasses import dataclass, field
from decimal import Decimal, InvalidOperation
from enum import Enum
from typing import Any, Callable, Dict, List, Optional, Tuple, Union

logger = logging.getLogger(__name__)

Numeric = Union[str, int, float, Decimal]

_BUY = "BUY"
_SELL = "SELL"


class CircuitBreakerState(Enum):
    CLOSED = "CLOSED"        # Normal operation, primary broker is healthy
    OPEN = "OPEN"            # Primary is unhealthy, routing failover-eligible flow to secondary
    HALF_OPEN = "HALF_OPEN"  # Bounded probing of the primary


class BrokerHealthStatus(Enum):
    """Coarse health of one broker leg, derived from recent outcomes."""

    HEALTHY = "HEALTHY"
    DEGRADED = "DEGRADED"
    DOWN = "DOWN"


class FailureClass(Enum):
    """Why a broker call failed — which determines whether failing over is safe.

    The distinction this enum exists to enforce: ``AMBIGUOUS`` means *the order may be
    working at that broker*. Nothing may be re-sent anywhere until that is resolved.
    """

    #: The request may have been received and acted on. Reconcile before doing anything.
    AMBIGUOUS = "AMBIGUOUS"
    #: The broker demonstrably did not receive the order (connection refused, DNS
    #: failure). Failing over is safe.
    UNAVAILABLE = "UNAVAILABLE"
    #: Throttled (429/418). The order was not accepted, but this broker needs backoff.
    RATE_LIMITED = "RATE_LIMITED"
    #: A business rejection: bad symbol, insufficient buying power, size limit. The
    #: order is dead and must NOT be routed elsewhere.
    REJECTED = "REJECTED"


class PositionEffect(Enum):
    """Whether an order may open new exposure, or must only reduce existing exposure."""

    #: Opens or increases exposure. Eligible for failover to the secondary account.
    OPEN = "OPEN"
    #: Closes or reduces exposure. Pinned to the account holding the position; never
    #: failed over, because in another account the same order opens new exposure.
    REDUCE = "REDUCE"


class BrokerError(Exception):
    """Adapter-raised error carrying an explicit failure classification.

    Adapters should raise this rather than a bare exception, because only the adapter
    knows whether the request left the machine. The distinction that matters most:
    a connect timeout is ``UNAVAILABLE`` (nothing was sent) while a read timeout is
    ``AMBIGUOUS`` (the request was sent and the response was lost).
    """

    def __init__(
        self,
        message: str,
        failure_class: FailureClass,
        status_code: Optional[int] = None,
        retry_after_s: Optional[float] = None,
    ) -> None:
        super().__init__(message)
        self.failure_class = failure_class
        self.status_code = status_code
        self.retry_after_s = retry_after_s


class AmbiguousOrderStateError(Exception):
    """The order may be live at a broker; the router refused to send it anywhere else.

    This is deliberately fatal to the submission path. The caller must query the
    broker's order state for ``client_order_id`` before retrying or failing over. The
    alternative — failing over on a guess — puts a second order in a second account
    that will never net against the first.
    """

    def __init__(self, message: str, client_order_id: str, broker_name: str) -> None:
        super().__init__(message)
        self.client_order_id = client_order_id
        self.broker_name = broker_name


class AllBrokersUnavailableError(Exception):
    """Both legs failed in a way that leaves no route for the order."""

    def __init__(self, message: str, client_order_id: str) -> None:
        super().__init__(message)
        self.client_order_id = client_order_id


class PositionAffinityError(Exception):
    """A REDUCE order could not be routed to an account that holds the position."""


class SymbolMappingError(LookupError):
    """No symbol translation was registered under strict mapping.

    A local configuration fault, raised before anything is sent. It is deliberately not
    a :class:`FailureClass` — nothing reached a broker, so there is nothing ambiguous
    about it and no failover is warranted.
    """


def _to_decimal(value: Numeric, field_name: str) -> Decimal:
    """Normalise to ``Decimal`` without inheriting binary float error.

    ``Decimal(0.1)`` carries the float's representation error; ``Decimal(str(0.1))``
    does not. Quantities feed position arithmetic, where that error accumulates.
    """
    if isinstance(value, bool):
        raise ValueError(f"{field_name} must be numeric, got bool {value!r}")
    if isinstance(value, Decimal):
        candidate = value
    elif isinstance(value, (int, float, str)):
        try:
            candidate = Decimal(str(value).strip())
        except (InvalidOperation, ValueError) as exc:
            raise ValueError(f"{field_name} is not a valid decimal: {value!r}") from exc
    else:
        raise TypeError(f"{field_name} must be str/int/float/Decimal, got {type(value).__name__}")
    if not candidate.is_finite():
        raise ValueError(f"{field_name} must be finite, got {value!r}")
    return candidate


@dataclass(frozen=True)
class OrderRequest:
    """A broker-neutral order instruction. Immutable once constructed.

    ``client_order_id`` is **required**. It is the key the caller reconciles on after an
    ambiguous outcome, and without it an ambiguous failure is unrecoverable: there is no
    way to ask the primary "did you take my order?".

    ``position_effect`` must be set to :attr:`PositionEffect.REDUCE` for any order
    intended to close or reduce a position. That is what stops the router from turning a
    close in one account into an opening position in another.
    """

    symbol: str
    action: str
    quantity: Numeric
    client_order_id: str
    order_type: str = "MARKET"
    limit_price: Optional[Numeric] = None
    position_effect: PositionEffect = PositionEffect.OPEN

    def __post_init__(self) -> None:
        def _set(name: str, value: Any) -> None:
            object.__setattr__(self, name, value)

        symbol = str(self.symbol).strip().upper()
        if not symbol:
            raise ValueError("symbol must be a non-empty string")
        _set("symbol", symbol)

        action = str(self.action).strip().upper()
        if action not in (_BUY, _SELL):
            raise ValueError(f"action must be {_BUY!r} or {_SELL!r}, got {self.action!r}")
        _set("action", action)

        client_order_id = str(self.client_order_id).strip()
        if not client_order_id:
            raise ValueError(
                "client_order_id is required: it is the key you reconcile on when a "
                "broker call is ambiguous"
            )
        _set("client_order_id", client_order_id)

        order_type = str(self.order_type).strip().upper()
        if not order_type:
            raise ValueError("order_type must be a non-empty string")
        _set("order_type", order_type)

        quantity = _to_decimal(self.quantity, "quantity")
        if quantity <= 0:
            raise ValueError(f"quantity must be strictly positive, got {quantity}")
        _set("quantity", quantity)

        if self.limit_price is not None:
            limit_price = _to_decimal(self.limit_price, "limit_price")
            if limit_price <= 0:
                raise ValueError(f"limit_price must be strictly positive, got {limit_price}")
            _set("limit_price", limit_price)

        if not isinstance(self.position_effect, PositionEffect):
            raise TypeError("position_effect must be a PositionEffect")

    @property
    def signed_quantity(self) -> Decimal:
        """Quantity signed by direction: BUY positive, SELL negative."""
        return self.quantity if self.action == _BUY else -self.quantity


@dataclass
class OrderResult:
    order_id: str
    broker_name: str
    account_id: str
    symbol: str
    action: str
    quantity: Decimal
    status: str
    executed_at: float
    client_order_id: str = ""


class BrokerAdapter:
    """Interface a live broker adapter must satisfy.

    Implementations should raise :class:`BrokerError` with an explicit
    :class:`FailureClass`. When they raise something else, the router classifies
    conservatively — see :func:`classify_exception` — which means most unknown
    exceptions are treated as ``AMBIGUOUS`` and will *not* trigger an automatic
    failover. That is the intended bias.
    """

    name: str
    account_id: str

    def place_order(self, order: OrderRequest) -> OrderResult:  # pragma: no cover
        raise NotImplementedError


def classify_exception(exc: BaseException) -> FailureClass:
    """Classify an adapter exception conservatively.

    Only errors that prove the request never reached the broker are treated as
    ``UNAVAILABLE``: a refused TCP connection and a DNS resolution failure. Everything
    unrecognised is ``AMBIGUOUS``, because the cost of the two mistakes is not
    symmetric — treating an ambiguous outcome as a clean failure duplicates an order
    across accounts, while treating a clean failure as ambiguous costs one status query.
    """
    if isinstance(exc, BrokerError):
        return exc.failure_class
    if isinstance(exc, (ConnectionRefusedError, socket.gaierror)):
        return FailureClass.UNAVAILABLE
    # TimeoutError covers socket.timeout on Python 3.10+; ConnectionResetError and
    # BrokenPipeError mean the connection died mid-exchange, after bytes were sent.
    if isinstance(exc, (TimeoutError, ConnectionResetError, BrokenPipeError)):
        return FailureClass.AMBIGUOUS
    return FailureClass.AMBIGUOUS


class MockBrokerAdapter(BrokerAdapter):
    """In-memory adapter for tests, demos, and the checklist's dry run.

    ``failure`` lets a test choose the failure classification precisely; ``should_fail``
    is retained for backwards compatibility and raises an ``UNAVAILABLE`` error, since
    that is the only class for which the historical behaviour (immediate failover) was
    actually safe.
    """

    def __init__(
        self,
        name: str,
        account_id: str,
        should_fail: bool = False,
        failure: Optional[BrokerError] = None,
    ) -> None:
        self.name = name
        self.account_id = account_id
        self.should_fail = should_fail
        self.failure = failure
        self.executed_orders: List[OrderResult] = []
        self._lock = threading.Lock()

    def place_order(self, order: OrderRequest) -> OrderResult:
        if self.failure is not None:
            raise self.failure
        if self.should_fail:
            raise BrokerError(
                f"Broker {self.name} refused the connection (HTTP 503 / unreachable).",
                failure_class=FailureClass.UNAVAILABLE,
                status_code=503,
            )
        with self._lock:
            result = OrderResult(
                order_id=f"{self.name.upper()}_ORD_{len(self.executed_orders) + 1}",
                broker_name=self.name,
                account_id=self.account_id,
                symbol=order.symbol,
                action=order.action,
                quantity=order.quantity,
                status="FILLED",
                executed_at=time.time(),
                client_order_id=order.client_order_id,
            )
            self.executed_orders.append(result)
        return result


@dataclass(frozen=True)
class RouterStats:
    """Point-in-time snapshot of routing and circuit telemetry."""

    circuit_state: str
    primary_failures: int
    consecutive_half_open_successes: int
    seconds_until_probe: Optional[float]
    primary_health: str
    routed_primary: int
    routed_secondary: int
    ambiguous_outcomes: int
    terminal_rejections: int
    failovers: int
    half_open_probes_in_flight: int


class BrokerFailoverRouter:
    """Circuit-breaking router across a primary and secondary broker account.

    Typical wiring::

        router = BrokerFailoverRouter(
            primary_broker=primary,
            secondary_broker=secondary,
            order_status_resolver=lambda broker, coid: broker_query(broker, coid),
        )
        router.register_symbol_map("AAPL", "AAPL STK SMART", "AAPL.S")
        router.set_position("primary_broker", "AAPL", 100)   # seed from the broker

        try:
            result = router.submit_order(order)
        except AmbiguousOrderStateError as exc:
            # The order may be live at exc.broker_name under exc.client_order_id.
            reconcile(exc.broker_name, exc.client_order_id)

    Every mutating method is safe to call from multiple threads.
    """

    def __init__(
        self,
        primary_broker: BrokerAdapter,
        secondary_broker: BrokerAdapter,
        max_consecutive_failures: int = 3,
        recovery_timeout_seconds: float = 60.0,
        half_open_max_probes: int = 1,
        half_open_successes_to_close: int = 1,
        order_status_resolver: Optional[Callable[[BrokerAdapter, str], Optional[OrderResult]]] = None,
        strict_symbol_mapping: bool = False,
    ) -> None:
        if primary_broker.name == secondary_broker.name:
            raise ValueError("primary and secondary brokers must have distinct names")
        if max_consecutive_failures < 1:
            raise ValueError("max_consecutive_failures must be >= 1")
        if recovery_timeout_seconds < 0:
            raise ValueError("recovery_timeout_seconds must be >= 0")
        if half_open_max_probes < 1:
            raise ValueError("half_open_max_probes must be >= 1")
        if half_open_successes_to_close < 1:
            raise ValueError("half_open_successes_to_close must be >= 1")

        self.primary_broker = primary_broker
        self.secondary_broker = secondary_broker
        self.max_consecutive_failures = max_consecutive_failures
        self.recovery_timeout_seconds = recovery_timeout_seconds
        self.half_open_max_probes = half_open_max_probes
        self.half_open_successes_to_close = half_open_successes_to_close
        self.order_status_resolver = order_status_resolver
        self.strict_symbol_mapping = strict_symbol_mapping

        self.circuit_state = CircuitBreakerState.CLOSED
        self.primary_failures = 0
        self.lock = threading.Lock()

        # Monotonic, not wall clock: the recovery timeout must survive an NTP step. A
        # backward jump in time.time() would hold the circuit OPEN indefinitely.
        self._last_failure_monotonic = 0.0
        self._primary_backoff_until = 0.0

        # Incremented on every trip. An in-flight call that started in an earlier
        # generation must not close a circuit that has since tripped for other reasons.
        self._generation = 0
        self._half_open_probes = 0
        self._half_open_successes = 0
        self._manually_opened = False

        self.symbol_mapping: Dict[str, Dict[str, str]] = {}
        self._positions: Dict[str, Dict[str, Decimal]] = {
            primary_broker.name: {},
            secondary_broker.name: {},
        }
        self._counters: Dict[str, int] = {
            "routed_primary": 0,
            "routed_secondary": 0,
            "ambiguous_outcomes": 0,
            "terminal_rejections": 0,
            "failovers": 0,
        }

    # ------------------------------------------------------------- symbol mapping

    def register_symbol_map(
        self, canonical_symbol: str, primary_symbol: str, secondary_symbol: str
    ) -> None:
        canonical_symbol = str(canonical_symbol).strip().upper()
        if not canonical_symbol:
            raise ValueError("canonical_symbol must be non-empty")
        for name, value in (("primary_symbol", primary_symbol), ("secondary_symbol", secondary_symbol)):
            if not str(value).strip():
                raise ValueError(f"{name} must be non-empty")
        with self.lock:
            self.symbol_mapping[canonical_symbol] = {
                self.primary_broker.name: str(primary_symbol).strip(),
                self.secondary_broker.name: str(secondary_symbol).strip(),
            }

    def _get_broker_symbol(self, canonical_symbol: str, broker_name: str) -> str:
        """Translate a canonical ticker for one broker.

        With ``strict_symbol_mapping`` the absence of a mapping raises. The permissive
        default passes the canonical symbol through, which is convenient and dangerous:
        the same ticker can denote a different instrument at another broker, and a
        silent fallthrough sends a live order for it. Enable strict mode in production.
        """
        with self.lock:
            mapping = self.symbol_mapping.get(canonical_symbol)
        if mapping is None:
            if self.strict_symbol_mapping:
                raise SymbolMappingError(
                    f"no symbol mapping registered for {canonical_symbol!r}; refusing to "
                    f"send the canonical ticker to {broker_name!r} under strict mapping"
                )
            logger.warning(
                "No symbol mapping for %s at %s; sending the canonical ticker unchanged",
                canonical_symbol,
                broker_name,
            )
            return canonical_symbol
        resolved = mapping.get(broker_name)
        if resolved is None:
            if self.strict_symbol_mapping:
                raise SymbolMappingError(
                    f"symbol map for {canonical_symbol!r} has no entry for {broker_name!r}"
                )
            return canonical_symbol
        return resolved

    # ----------------------------------------------------------------- positions

    def set_position(self, broker_name: str, symbol: str, quantity: Numeric) -> None:
        """Seed or correct the cached net position for one account.

        The router's position view is a cache. It only sees fills it routed, so it must
        be seeded from each broker's own position report at session start and after any
        out-of-band activity, or a REDUCE order will be refused (or, worse, permitted)
        on stale information.
        """
        broker_name = self._require_known_broker(broker_name)
        symbol = str(symbol).strip().upper()
        if not symbol:
            raise ValueError("symbol must be non-empty")
        signed = _to_decimal(quantity, "quantity")
        with self.lock:
            self._positions[broker_name][symbol] = signed

    def get_position(self, broker_name: str, symbol: str) -> Decimal:
        broker_name = self._require_known_broker(broker_name)
        with self.lock:
            return self._positions[broker_name].get(str(symbol).strip().upper(), Decimal(0))

    def _require_known_broker(self, broker_name: str) -> str:
        if broker_name not in (self.primary_broker.name, self.secondary_broker.name):
            raise ValueError(
                f"unknown broker {broker_name!r}; expected one of "
                f"{(self.primary_broker.name, self.secondary_broker.name)}"
            )
        return broker_name

    def _can_reduce_at(self, broker_name: str, order: OrderRequest) -> bool:
        """True when this account holds a position the order would genuinely reduce."""
        position = self.get_position(broker_name, order.symbol)
        if order.action == _SELL:
            return position >= order.quantity
        return position <= -order.quantity

    def _apply_fill(self, broker_name: str, order: OrderRequest) -> None:
        with self.lock:
            book = self._positions[broker_name]
            book[order.symbol] = book.get(order.symbol, Decimal(0)) + order.signed_quantity

    # ------------------------------------------------------------------- circuit

    def _snapshot_state(self) -> Tuple[CircuitBreakerState, int]:
        """Read the circuit, promoting OPEN to HALF_OPEN when the timeout has elapsed."""
        with self.lock:
            if (
                self.circuit_state is CircuitBreakerState.OPEN
                and not self._manually_opened
                and (time.monotonic() - self._last_failure_monotonic) > self.recovery_timeout_seconds
            ):
                logger.info("Recovery timeout elapsed; transitioning to HALF_OPEN to probe primary")
                self.circuit_state = CircuitBreakerState.HALF_OPEN
                self._half_open_probes = 0
                self._half_open_successes = 0
            return self.circuit_state, self._generation

    def _acquire_probe_permit(self) -> bool:
        """Claim one of the bounded half-open probe slots.

        Without this, every concurrent caller in HALF_OPEN sends a live order to a
        broker believed to be down. Generic circuit breakers permit several concurrent
        trial calls (resilience4j defaults to 10) because those are cheap idempotent
        reads; here each probe is capital at risk, so the default is one.
        """
        with self.lock:
            if self.circuit_state is not CircuitBreakerState.HALF_OPEN:
                return False
            if self._half_open_probes >= self.half_open_max_probes:
                return False
            self._half_open_probes += 1
            return True

    def _release_probe_permit(self) -> None:
        with self.lock:
            if self._half_open_probes > 0:
                self._half_open_probes -= 1

    def _record_primary_success(self, generation: int, was_probe: bool) -> None:
        with self.lock:
            if generation != self._generation:
                # A slow call that started before the circuit tripped has just returned.
                # Honouring it would close a circuit that other threads opened for good
                # reason, and reset the failure counter with it.
                logger.info(
                    "Ignoring stale primary success from generation %d (current %d)",
                    generation,
                    self._generation,
                )
                return
            if self.circuit_state is CircuitBreakerState.HALF_OPEN and was_probe:
                self._half_open_successes += 1
                if self._half_open_successes < self.half_open_successes_to_close:
                    logger.info(
                        "Primary probe succeeded (%d/%d needed to close)",
                        self._half_open_successes,
                        self.half_open_successes_to_close,
                    )
                    return
                logger.info("Primary probe threshold met; closing circuit")
            elif self.circuit_state is CircuitBreakerState.OPEN:
                return
            self.circuit_state = CircuitBreakerState.CLOSED
            self.primary_failures = 0
            self._half_open_successes = 0
            self._primary_backoff_until = 0.0

    def _record_primary_failure(
        self, generation: int, failure_class: FailureClass, retry_after_s: Optional[float]
    ) -> None:
        """Count a health-affecting failure and trip the circuit when warranted.

        Terminal rejections never reach here: a broker correctly refusing an order is
        not evidence that the broker is unhealthy, and counting it would trip the
        circuit on the strategy's own bad orders.
        """
        with self.lock:
            now = time.monotonic()
            self._last_failure_monotonic = now
            if retry_after_s is not None and retry_after_s > 0:
                self._primary_backoff_until = max(self._primary_backoff_until, now + retry_after_s)

            if generation != self._generation:
                return

            self.primary_failures += 1
            if self.circuit_state is CircuitBreakerState.HALF_OPEN:
                logger.warning("Primary probe failed (%s); reopening circuit", failure_class.value)
                self._trip_locked()
            elif self.primary_failures >= self.max_consecutive_failures:
                logger.error(
                    "Primary failures (%d) reached threshold %d (%s); circuit OPEN",
                    self.primary_failures,
                    self.max_consecutive_failures,
                    failure_class.value,
                )
                self._trip_locked()
            else:
                logger.warning(
                    "Primary failure %d/%d (%s)",
                    self.primary_failures,
                    self.max_consecutive_failures,
                    failure_class.value,
                )

    def _trip_locked(self) -> None:
        self.circuit_state = CircuitBreakerState.OPEN
        self._generation += 1
        self._half_open_probes = 0
        self._half_open_successes = 0

    def manual_reset(self) -> None:
        """Force the circuit CLOSED and clear the failure history and any backoff."""
        with self.lock:
            self.circuit_state = CircuitBreakerState.CLOSED
            self.primary_failures = 0
            self._half_open_probes = 0
            self._half_open_successes = 0
            self._last_failure_monotonic = 0.0
            self._primary_backoff_until = 0.0
            self._manually_opened = False
            self._generation += 1
        logger.warning("Circuit breaker manually reset to CLOSED")

    def manual_open(self, reason: str) -> None:
        """Force the circuit OPEN and hold it there until :meth:`manual_reset`.

        The operator counterpart to the automatic trip: it survives the recovery
        timeout, so a broker known to be in a bad state is not probed on a timer while
        the desk is still working out what happened.
        """
        reason = str(reason).strip() or "unspecified"
        with self.lock:
            self._manually_opened = True
            self._last_failure_monotonic = time.monotonic()
            self._trip_locked()
        logger.critical("Circuit breaker manually forced OPEN: %s", reason)

    # -------------------------------------------------------------------- routing

    def submit_order(self, order: OrderRequest) -> OrderResult:
        """Route one order, failing over only when that is demonstrably safe.

        Raises:
            AmbiguousOrderStateError: the order may be live at a broker. Reconcile on
                ``client_order_id`` before re-sending anything, anywhere.
            AllBrokersUnavailableError: neither leg could accept the order.
            PositionAffinityError: a REDUCE order had no account holding the position.
            BrokerError: a terminal business rejection, re-raised unrouted.
        """
        if not isinstance(order, OrderRequest):
            raise TypeError(f"order must be an OrderRequest, got {type(order).__name__}")

        if order.position_effect is PositionEffect.REDUCE:
            return self._submit_reduce_order(order)

        state, generation = self._snapshot_state()
        primary_eligible = state is CircuitBreakerState.CLOSED
        was_probe = False

        if state is CircuitBreakerState.HALF_OPEN:
            primary_eligible = was_probe = self._acquire_probe_permit()

        if primary_eligible and self._in_backoff():
            logger.info("Primary is inside its Retry-After backoff window; routing to secondary")
            primary_eligible = False
            if was_probe:
                self._release_probe_permit()
                was_probe = False

        primary_error: Optional[BaseException] = None
        if primary_eligible:
            try:
                result = self._route_to_broker(self.primary_broker, order)
            except SymbolMappingError:
                # A local configuration fault: nothing was sent, so there is nothing to
                # fail over and nothing ambiguous. Surface it unchanged.
                raise
            except Exception as exc:  # classified below; never blindly rerouted
                primary_error = exc
                resolved = self._handle_primary_exception(exc, order, generation)
                if resolved is not None:
                    return resolved
                # Reaching here means the classification is failover-eligible;
                # AMBIGUOUS and REJECTED have already raised.
            else:
                self._record_primary_success(generation, was_probe)
                self._bump("routed_primary")
                self._apply_fill(self.primary_broker.name, order)
                return result
            finally:
                if was_probe:
                    self._release_probe_permit()

        return self._route_failover(order, primary_error)

    def _submit_reduce_order(self, order: OrderRequest) -> OrderResult:
        """Route a position-reducing order to the account that holds the position.

        A REDUCE order is never failed over. In an account with no position the same
        instruction opens new exposure rather than closing anything: the strategy ends
        up long in one account and short in the other, with double the gross exposure
        and, in US equities, a sale that Rule 200(g) does not permit to be marked long.

        This path deliberately **bypasses the circuit breaker**. An order that reduces
        risk should still be attempted against a degraded primary, because the only
        alternative the router has is to open a position somewhere else, and that is not
        a degraded version of closing one — it is the opposite trade. Failures are still
        recorded against broker health; the order simply is not diverted.
        """
        for broker in (self.primary_broker, self.secondary_broker):
            if not self._can_reduce_at(broker.name, order):
                continue
            try:
                result = self._route_to_broker(broker, order)
            except SymbolMappingError:
                raise
            except Exception as exc:
                failure_class = classify_exception(exc)
                if broker is self.primary_broker and failure_class is not FailureClass.REJECTED:
                    self._record_primary_failure(
                        self._current_generation(), failure_class, _retry_after_of(exc)
                    )
                if failure_class is FailureClass.AMBIGUOUS:
                    self._bump("ambiguous_outcomes")
                    raise AmbiguousOrderStateError(
                        f"REDUCE order {order.client_order_id} may be working at "
                        f"{broker.name}; reconcile before re-sending",
                        client_order_id=order.client_order_id,
                        broker_name=broker.name,
                    ) from exc
                if failure_class is FailureClass.REJECTED:
                    self._bump("terminal_rejections")
                    raise
                # The holding account is unreachable. Routing this order to the other
                # account would open a position, not reduce one.
                raise PositionAffinityError(
                    f"account {broker.name} holds the position for {order.symbol} but is "
                    f"unavailable ({failure_class.value}); refusing to route a REDUCE "
                    f"order to an account that would open new exposure instead"
                ) from exc
            else:
                if broker is self.primary_broker:
                    self._record_primary_success(self._current_generation(), False)
                self._bump("routed_primary" if broker is self.primary_broker else "routed_secondary")
                self._apply_fill(broker.name, order)
                return result

        raise PositionAffinityError(
            f"no account holds a position in {order.symbol} sufficient to satisfy a "
            f"REDUCE order for {order.quantity}; seed positions with set_position() or "
            f"submit this as PositionEffect.OPEN if opening exposure is genuinely intended"
        )

    def _handle_primary_exception(
        self, exc: BaseException, order: OrderRequest, generation: int
    ) -> Optional[OrderResult]:
        """Classify a primary failure and decide whether failover is permissible.

        Returns:
            The real :class:`OrderResult` when an ambiguous call was resolved to a live
            order at the primary, or ``None`` when the failure is failover-eligible.

        Raises:
            AmbiguousOrderStateError: unresolved ambiguity — nothing may be re-sent.
            BrokerError: a terminal rejection, re-raised unrouted.
        """
        failure_class = classify_exception(exc)

        if failure_class is FailureClass.REJECTED:
            # The primary's own pre-trade controls refused this order. Shopping it to a
            # second account looks for a broker that will say yes.
            self._bump("terminal_rejections")
            logger.warning(
                "Primary rejected order %s terminally (%s); not routing to secondary",
                order.client_order_id,
                exc,
            )
            raise exc

        self._record_primary_failure(generation, failure_class, _retry_after_of(exc))

        if failure_class is FailureClass.AMBIGUOUS:
            self._bump("ambiguous_outcomes")
            resolved = self._try_resolve(self.primary_broker, order)
            if resolved is not None:
                logger.warning(
                    "Primary call for %s failed but the order was found live/filled at "
                    "%s; not failing over",
                    order.client_order_id,
                    self.primary_broker.name,
                )
                self._apply_fill(self.primary_broker.name, order)
                self._bump("routed_primary")
                return resolved
            raise AmbiguousOrderStateError(
                f"Primary call for {order.client_order_id} failed ambiguously; the order "
                f"may be working at {self.primary_broker.name}. Reconcile before "
                f"re-sending — the secondary account cannot de-duplicate it.",
                client_order_id=order.client_order_id,
                broker_name=self.primary_broker.name,
            ) from exc

    def _try_resolve(self, broker: BrokerAdapter, order: OrderRequest) -> Optional[OrderResult]:
        """Ask the caller's resolver whether the order actually landed.

        Returns the order if it is live/filled, ``None`` if the resolver is absent, it
        failed, or it could not find the order. ``None`` therefore means "still
        ambiguous" — a resolver that itself errors must never be read as "not there".
        """
        if self.order_status_resolver is None:
            return None
        try:
            return self.order_status_resolver(broker, order.client_order_id)
        except Exception:
            logger.exception(
                "order_status_resolver failed for %s at %s; treating as unresolved",
                order.client_order_id,
                broker.name,
            )
            return None

    def _route_failover(self, order: OrderRequest, primary_error: Optional[BaseException]) -> OrderResult:
        if primary_error is not None:
            self._bump("failovers")
            logger.warning(
                "Failing order %s over to %s after a %s primary failure",
                order.client_order_id,
                self.secondary_broker.name,
                classify_exception(primary_error).value,
            )
        try:
            result = self._route_to_broker(self.secondary_broker, order)
        except Exception as exc:
            failure_class = classify_exception(exc)
            if failure_class is FailureClass.AMBIGUOUS:
                self._bump("ambiguous_outcomes")
                raise AmbiguousOrderStateError(
                    f"Secondary call for {order.client_order_id} failed ambiguously; the "
                    f"order may be working at {self.secondary_broker.name}. Reconcile "
                    f"before re-sending.",
                    client_order_id=order.client_order_id,
                    broker_name=self.secondary_broker.name,
                ) from exc
            if failure_class is FailureClass.REJECTED:
                self._bump("terminal_rejections")
                raise
            raise AllBrokersUnavailableError(
                f"No route for order {order.client_order_id}: primary "
                f"{'unavailable' if primary_error is not None else 'circuit OPEN'}, "
                f"secondary failed ({failure_class.value}: {exc})",
                client_order_id=order.client_order_id,
            ) from exc
        self._bump("routed_secondary")
        self._apply_fill(self.secondary_broker.name, order)
        return result

    def _route_to_broker(self, broker: BrokerAdapter, order: OrderRequest) -> OrderResult:
        mapped = OrderRequest(
            symbol=self._get_broker_symbol(order.symbol, broker.name),
            action=order.action,
            quantity=order.quantity,
            client_order_id=order.client_order_id,
            order_type=order.order_type,
            limit_price=order.limit_price,
            position_effect=order.position_effect,
        )
        return broker.place_order(mapped)

    def _in_backoff(self) -> bool:
        with self.lock:
            return time.monotonic() < self._primary_backoff_until

    def _current_generation(self) -> int:
        with self.lock:
            return self._generation

    def _bump(self, counter: str) -> None:
        with self.lock:
            self._counters[counter] += 1

    # ----------------------------------------------------------------- telemetry

    def primary_health(self) -> BrokerHealthStatus:
        """Coarse primary health derived from circuit state and the failure counter."""
        with self.lock:
            if self.circuit_state is CircuitBreakerState.OPEN:
                return BrokerHealthStatus.DOWN
            if self.circuit_state is CircuitBreakerState.HALF_OPEN or self.primary_failures > 0:
                return BrokerHealthStatus.DEGRADED
            return BrokerHealthStatus.HEALTHY

    def stats(self) -> RouterStats:
        health = self.primary_health()
        with self.lock:
            remaining: Optional[float] = None
            if self.circuit_state is CircuitBreakerState.OPEN and not self._manually_opened:
                elapsed = time.monotonic() - self._last_failure_monotonic
                remaining = max(0.0, self.recovery_timeout_seconds - elapsed)
            return RouterStats(
                circuit_state=self.circuit_state.value,
                primary_failures=self.primary_failures,
                consecutive_half_open_successes=self._half_open_successes,
                seconds_until_probe=remaining,
                primary_health=health.value,
                routed_primary=self._counters["routed_primary"],
                routed_secondary=self._counters["routed_secondary"],
                ambiguous_outcomes=self._counters["ambiguous_outcomes"],
                terminal_rejections=self._counters["terminal_rejections"],
                failovers=self._counters["failovers"],
                half_open_probes_in_flight=self._half_open_probes,
            )


def _retry_after_of(exc: BaseException) -> Optional[float]:
    """Extract a positive ``Retry-After`` in seconds from an adapter error, if present.

    RFC 9110 Section 10.2.3 allows ``Retry-After`` to be either delay-seconds or an
    HTTP-date; parsing the header belongs to the adapter, which sets
    ``BrokerError.retry_after_s`` in seconds.
    """
    value = getattr(exc, "retry_after_s", None)
    if value is None:
        return None
    try:
        numeric = float(value)
    except (TypeError, ValueError):
        return None
    return numeric if numeric > 0 else None
