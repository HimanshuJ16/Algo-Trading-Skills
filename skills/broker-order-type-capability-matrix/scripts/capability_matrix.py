"""
broker-order-type-capability-matrix: which order types a broker supports natively,
and how to decompose the ones it does not into locally managed legs.

Four decisions in this module exist because the obvious alternative silently
mis-executes:

  1. **A synthesized slice schedule sums exactly to the parent quantity.** The
     first slice is the primary order and the feeder leg carries the remainder,
     so ``primary_quantity + sum(schedule) == quantity`` holds exactly. Emitting
     a feeder for the *full* quantity alongside a primary slice — and telling the
     caller to fire both — over-executes the parent order by one slice. Decimal
     arithmetic with the residual absorbed by the final slice keeps the sum exact;
     ``quantity / slices`` in binary float does not — seven slices of ``10000/7``,
     added up, come to ``10000.000000000002``.

  2. **An emulated OCO has no primary order.** ``primary_order_type`` is ``None``
     for it. Reporting ``OrderType.OCO`` as the primary type of an *emulated* OCO
     tells the caller to fire a native OCO at the one broker that just failed the
     native-support check.

  3. **Price geometry is validated on the native path too.** A bracket whose stop
     loss sits on the profitable side of the entry triggers the moment it is
     registered. That is just as wrong when the broker accepts it natively, so the
     check runs before the native/emulated branch, not inside the synthesizer.

  4. **An unknown broker emulates rather than routes natively.** ``supports_native``
     returns ``False`` (and logs) for a broker it does not know, because guessing
     "native" for an unrecognised name sends an order type the venue may reject.
     ``plan_order_execution`` raises outright — planning against a broker that is
     not in the registry is a configuration error, not a fallback case.

Scope: this module plans an order. It performs no network I/O and submits nothing.
The emulated legs it returns are instructions for a local Execution Management
System, which owns the trigger watching, the timers, and — critically — the
persistence. See ``references/standards.md`` for the per-broker capability
evidence and ``references/workflows.md`` for the EMS contract.
"""
import logging
from dataclasses import dataclass, field, replace
from decimal import Decimal, getcontext
from enum import Enum
from typing import Any, Dict, List, Optional, Sequence, Set, Union

logger = logging.getLogger(__name__)

#: Values accepted anywhere this module takes a quantity or a price.
Numeric = Union[int, float, Decimal]


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


#: Order types this module can decompose when a broker lacks native support.
#: Everything else — TRAILING_STOP, PEGGED, MOC, VWAP — depends on state this
#: planner does not have (a continuously updated reference price, an auction
#: imbalance feed, a live volume forecast) and is refused rather than
#: approximated. For VWAP specifically see ``execution-algo-twap-vwap-slicing``.
EMULATABLE_ORDER_TYPES: Set[OrderType] = {
    OrderType.BRACKET,
    OrderType.OCO,
    OrderType.ICEBERG,
    OrderType.TWAP,
}

DEFAULT_ICEBERG_SLICES = 5
DEFAULT_TWAP_DURATION_MINUTES = 60
DEFAULT_TWAP_SLICES = 10


def _to_decimal(value: Numeric, name: str) -> Decimal:
    """Convert a numeric input to ``Decimal``, rejecting bools, NaN and Infinity.

    Floats go through ``str`` so ``0.1`` becomes ``Decimal("0.1")`` rather than the
    binary expansion that inherits float's rounding error.
    """
    if isinstance(value, bool):
        raise ValueError(f"{name} must be a number, got bool {value!r}")
    if isinstance(value, Decimal):
        result = value
    elif isinstance(value, int):
        result = Decimal(value)
    elif isinstance(value, float):
        result = Decimal(str(value))
    else:
        raise ValueError(
            f"{name} must be int, float or Decimal, got {type(value).__name__}"
        )
    if not result.is_finite():
        raise ValueError(f"{name} must be finite, got {value!r}")
    return result


def _positive_decimal(value: Numeric, name: str) -> Decimal:
    """As ``_to_decimal``, additionally rejecting zero and negatives.

    Prices and quantities are checked with this rather than a falsy test: ``if not
    price`` treats ``0`` as "not supplied" and silently substitutes a default for a
    value that should have been rejected outright.
    """
    result = _to_decimal(value, name)
    if result <= 0:
        raise ValueError(f"{name} must be greater than zero, got {result}")
    return result


def _positive_int(value: Any, name: str, minimum: int = 1) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise ValueError(f"{name} must be an int, got {type(value).__name__}")
    if value < minimum:
        raise ValueError(f"{name} must be >= {minimum}, got {value}")
    return value


def _json_safe(value: Any) -> Any:
    """Render Decimals as strings so a plan can be persisted as JSON losslessly."""
    if isinstance(value, Decimal):
        return str(value)
    if isinstance(value, dict):
        return {k: _json_safe(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_json_safe(v) for v in value]
    if isinstance(value, Enum):
        return value.value
    return value


@dataclass
class BrokerCapabilities:
    """One broker's native order-type surface.

    The ``supports_*`` booleans are a convenience view of ``native_order_types``,
    not an independent source of truth. ``__post_init__`` rejects a profile where
    the two disagree, so a mis-registered broker fails at registration instead of
    at order time — a profile claiming ``supports_oco=True`` while omitting
    ``OrderType.OCO`` would otherwise pass every readable check and still send the
    order down the emulation path.

    ``supports_algorithmic`` means "the broker exposes a scheduled execution algo",
    i.e. TWAP or VWAP is in ``native_order_types``.
    """

    broker_name: str
    native_order_types: Set[OrderType]
    supports_fractional: bool = False
    supports_bracket: bool = False
    supports_oco: bool = False
    supports_iceberg: bool = False
    supports_pegged: bool = False
    supports_algorithmic: bool = False

    def __post_init__(self) -> None:
        if not isinstance(self.broker_name, str) or not self.broker_name.strip():
            raise ValueError("broker_name must be a non-empty string.")
        self.broker_name = self.broker_name.strip().lower()

        self.native_order_types = set(self.native_order_types)
        invalid = [t for t in self.native_order_types if not isinstance(t, OrderType)]
        if invalid:
            raise ValueError(
                f"{self.broker_name}: native_order_types must contain OrderType "
                f"members, got {invalid!r}"
            )

        derived = {
            "supports_bracket": OrderType.BRACKET in self.native_order_types,
            "supports_oco": OrderType.OCO in self.native_order_types,
            "supports_iceberg": OrderType.ICEBERG in self.native_order_types,
            "supports_pegged": OrderType.PEGGED in self.native_order_types,
            "supports_algorithmic": bool(
                {OrderType.TWAP, OrderType.VWAP} & self.native_order_types
            ),
        }
        for flag, expected in derived.items():
            if getattr(self, flag) != expected:
                raise ValueError(
                    f"{self.broker_name}: {flag}={getattr(self, flag)!r} contradicts "
                    f"native_order_types (expected {expected!r}). The flags are a view "
                    f"of native_order_types; fix whichever one is wrong."
                )


@dataclass
class EmulatedLeg:
    """One instruction for the local EMS.

    ``quantity`` is the quantity this leg is responsible for — for a feeder that is
    the parent quantity *minus* whatever the primary order already sent, so the EMS
    executes the leg verbatim without subtracting anything itself.
    """

    leg_type: str
    action: str
    quantity: Decimal
    trigger_price: Optional[Decimal] = None
    limit_price: Optional[Decimal] = None
    slice_qty: Optional[Decimal] = None
    interval_seconds: Optional[int] = None
    metadata: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        """JSON-serializable form, for persisting emulated state across an EMS restart."""
        return {
            "leg_type": self.leg_type,
            "action": self.action,
            "quantity": str(self.quantity),
            "trigger_price": None if self.trigger_price is None else str(self.trigger_price),
            "limit_price": None if self.limit_price is None else str(self.limit_price),
            "slice_qty": None if self.slice_qty is None else str(self.slice_qty),
            "interval_seconds": self.interval_seconds,
            "metadata": _json_safe(self.metadata),
        }


@dataclass
class SynthesizedOrderPlan:
    """The routing decision for one requested order.

    Execution contract, which the EMS must follow exactly:

    * If ``has_primary_order`` is True, submit ``primary_quantity`` of
      ``primary_order_type`` at ``primary_price`` (``None`` for MARKET) **once**.
    * Then register every entry in ``emulated_legs``.
    * ``primary_quantity`` plus the quantity of every scheduling leg equals the
      requested quantity. Do not re-derive slice counts from the parent quantity.

    ``primary_order_type is None`` means there is nothing to fire now — every leg is
    conditional. That is the case for an emulated OCO.
    """

    is_native: bool
    primary_order_type: Optional[OrderType]
    primary_action: str
    primary_quantity: Decimal
    emulated_legs: List[EmulatedLeg]
    description: str
    symbol: str = ""
    primary_price: Optional[Decimal] = None

    @property
    def has_primary_order(self) -> bool:
        """True when the caller must submit an order immediately."""
        return self.primary_order_type is not None and self.primary_quantity > 0

    def to_dict(self) -> Dict[str, Any]:
        """JSON-serializable form, for persisting emulated state across an EMS restart."""
        return {
            "is_native": self.is_native,
            "symbol": self.symbol,
            "primary_order_type": (
                None if self.primary_order_type is None else self.primary_order_type.value
            ),
            "primary_action": self.primary_action,
            "primary_quantity": str(self.primary_quantity),
            "primary_price": None if self.primary_price is None else str(self.primary_price),
            "emulated_legs": [leg.to_dict() for leg in self.emulated_legs],
            "description": self.description,
        }


# Default capability matrix for major integrated brokers.
#
# THIS IS A STARTING TEMPLATE, NOT A LIVE FEED. Broker order-type support changes,
# and it varies by asset class, account type, entitlement and API surface — the same
# broker name can be native for one product and unsupported for another. Every entry
# below is sourced in references/standards.md; re-verify against current broker
# documentation before trading it. When in doubt, leave the order type out: the cost
# of an unnecessary emulation is latency, and the cost of a wrong "native" claim is a
# rejected or mis-routed order.
DEFAULT_CAPABILITIES: Dict[str, BrokerCapabilities] = {
    # IBKR TWS API. Bracket + OCA groups, iceberg via Order.displaySize, the pegged
    # family, MOC, and the TWAP/VWAP IBALGOs. The algos are documented for US
    # equities via SMART routing — not every product on every exchange.
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
    # Alpaca Trading API. Order classes simple/bracket/oco/oto; MOC and LOC via
    # time_in_force="cls". Alpaca documents no iceberg, no pegged orders and no
    # execution algos. Fractional orders are day-only and cannot use these advanced
    # classes — see references/standards.md.
    "alpaca": BrokerCapabilities(
        broker_name="alpaca",
        native_order_types={
            OrderType.MARKET, OrderType.LIMIT, OrderType.STOP_LOSS,
            OrderType.STOP_LIMIT, OrderType.TRAILING_STOP, OrderType.BRACKET,
            OrderType.OCO, OrderType.MOC,
        },
        supports_fractional=True,
        supports_bracket=True,
        supports_oco=True,
        supports_iceberg=False,
        supports_pegged=False,
        supports_algorithmic=False,
    ),
    # Zerodha Kite Connect. variety="iceberg" (2-50 legs) is native. Bracket orders
    # (variety="bo") were withdrawn and are absent from the current API. Kite does
    # expose a two-leg OCO — but only through the *GTT* API, as a broker-side resting
    # trigger for LIMIT orders, not through the order API this matrix plans against,
    # so it is not listed as a native OCO here.
    "zerodha": BrokerCapabilities(
        broker_name="zerodha",
        native_order_types={
            OrderType.MARKET, OrderType.LIMIT, OrderType.STOP_LOSS,
            OrderType.STOP_LIMIT, OrderType.ICEBERG,
        },
        supports_fractional=False,
        supports_bracket=False,
        supports_oco=False,
        supports_iceberg=True,
        supports_pegged=False,
        supports_algorithmic=False,
    ),
    # Binance Spot. OCO and OTOCO order lists, icebergQty on LIMIT/LIMIT_MAKER,
    # trailing stops via trailingDelta. OTOCO is functionally a native bracket: a
    # working order that, on fill, activates a take-profit/stop-loss OCO pair. TWAP
    # exists only on the separate Algo endpoints and under a notional band, so a spot
    # integration confined to /api/v3/order cannot fire it. VWAP is not offered.
    "binance": BrokerCapabilities(
        broker_name="binance",
        native_order_types={
            OrderType.MARKET, OrderType.LIMIT, OrderType.STOP_LOSS,
            OrderType.STOP_LIMIT, OrderType.TRAILING_STOP, OrderType.BRACKET,
            OrderType.OCO, OrderType.ICEBERG, OrderType.TWAP,
        },
        supports_fractional=True,
        supports_bracket=True,
        supports_oco=True,
        supports_iceberg=True,
        supports_pegged=False,
        supports_algorithmic=True,
    ),
}


def _build_slice_schedule(total: Decimal, slices: int) -> List[Decimal]:
    """Split ``total`` into ``slices`` parts summing **exactly** to ``total``.

    Every slice but the last carries the uniform quantity; the last absorbs the
    division residual. Without this the EMS either leaves quantity unexecuted or
    sends more than the parent order asked for.

    The arithmetic deliberately stays in the caller's own decimal context, and the
    result is then reconciled exactly as the execution contract states it
    (``schedule[0] + sum(schedule[1:]) == total``) — a schedule that only balances at
    some elevated internal precision does not balance for the EMS that adds it up. A
    quantity carrying more significant digits than the active context can hold is
    refused rather than sliced approximately: losing whole units off a schedule is not
    a rounding detail, it is the wrong order size.

    Slices are not rounded to an instrument's quantity step — this planner does not
    know it. Round the schedule to the venue's step before submission; see
    ``minimum-fill-size-and-lot-rounding-logic``.
    """
    uniform = total / slices
    schedule = [uniform] * (slices - 1)
    schedule.append(total - sum(schedule, Decimal(0)))

    if schedule[0] + sum(schedule[1:], Decimal(0)) != total:
        raise ValueError(
            f"Cannot split {total} into {slices} slices that sum back to it at the "
            f"active decimal precision ({getcontext().prec} significant digits). Round "
            f"the quantity to a realistic order size, use a different slice count, or "
            f"raise decimal.getcontext().prec."
        )
    return schedule


def _validate_price_geometry(
    order_type: OrderType,
    action: str,
    price: Optional[Decimal],
    stop_loss_price: Optional[Decimal],
    take_profit_price: Optional[Decimal],
) -> None:
    """Reject exit prices sitting on the wrong side of the entry, or of each other.

    A bracket with the stop above the take-profit on a long is not a slow loss — it
    is an immediate one, because both legs are already through their triggers the
    instant the EMS registers them.

    The two order types read ``action`` differently, which is why the checks mirror:

    * ``BRACKET``: ``action`` is the **entry** side and the exit legs are the
      opposite side. Long entry -> stop below, target above.
    * ``OCO``: ``action`` is the side of **both exit legs** (venues offering OCO
      natively, Binance and Alpaca among them, require the pair to share a side).
      A SELL OCO closes a long, so the target is above and the stop below; a BUY OCO
      closes a short, so the target is *below* and the stop above.
    """
    if order_type is OrderType.BRACKET:
        target_above = action == "BUY"
    elif order_type is OrderType.OCO:
        target_above = action == "SELL"
    else:
        return

    if stop_loss_price is not None and take_profit_price is not None:
        if target_above and take_profit_price <= stop_loss_price:
            raise ValueError(
                f"{order_type.value} {action}: take_profit_price ({take_profit_price}) "
                f"must be above stop_loss_price ({stop_loss_price}); as given, both legs "
                f"trigger immediately."
            )
        if not target_above and take_profit_price >= stop_loss_price:
            raise ValueError(
                f"{order_type.value} {action}: take_profit_price ({take_profit_price}) "
                f"must be below stop_loss_price ({stop_loss_price}); as given, both legs "
                f"trigger immediately."
            )

    if price is None:
        return

    if take_profit_price is not None:
        if target_above and take_profit_price <= price:
            raise ValueError(
                f"{order_type.value} {action}: take_profit_price ({take_profit_price}) "
                f"must be above the reference price ({price})."
            )
        if not target_above and take_profit_price >= price:
            raise ValueError(
                f"{order_type.value} {action}: take_profit_price ({take_profit_price}) "
                f"must be below the reference price ({price})."
            )
    if stop_loss_price is not None:
        if target_above and stop_loss_price >= price:
            raise ValueError(
                f"{order_type.value} {action}: stop_loss_price ({stop_loss_price}) must "
                f"be below the reference price ({price})."
            )
        if not target_above and stop_loss_price <= price:
            raise ValueError(
                f"{order_type.value} {action}: stop_loss_price ({stop_loss_price}) must "
                f"be above the reference price ({price})."
            )


class BrokerOrderCapabilityMatrix:
    """
    Registry of native broker order-type capabilities, with a synthesizer that
    decomposes unsupported types into legs a local EMS can manage.
    """

    def __init__(self, custom_matrix: Optional[Dict[str, BrokerCapabilities]] = None):
        """Build a registry, defaulting to ``DEFAULT_CAPABILITIES``.

        An explicitly empty ``custom_matrix`` yields an empty registry. Testing the
        argument for truthiness instead would quietly restore the full defaults for a
        caller who passed ``{}`` precisely so that no broker would resolve.

        Profiles are copied in, so mutating the registry cannot reach
        ``DEFAULT_CAPABILITIES`` or the caller's dictionary.
        """
        source = DEFAULT_CAPABILITIES if custom_matrix is None else custom_matrix
        self.matrix: Dict[str, BrokerCapabilities] = {}
        for key, capabilities in source.items():
            if not isinstance(capabilities, BrokerCapabilities):
                raise TypeError(
                    f"matrix entry '{key}' must be a BrokerCapabilities, got "
                    f"{type(capabilities).__name__}"
                )
            if str(key).strip().lower() != capabilities.broker_name:
                logger.warning(
                    "Matrix key %r does not match broker_name %r; registering under %r.",
                    key, capabilities.broker_name, capabilities.broker_name,
                )
            self.matrix[capabilities.broker_name] = self._copy(capabilities)

    @staticmethod
    def _copy(capabilities: BrokerCapabilities) -> BrokerCapabilities:
        return replace(
            capabilities, native_order_types=set(capabilities.native_order_types)
        )

    def register_broker(self, capabilities: BrokerCapabilities) -> None:
        """Register or override a broker capability profile."""
        if not isinstance(capabilities, BrokerCapabilities):
            raise TypeError(
                f"capabilities must be a BrokerCapabilities, got "
                f"{type(capabilities).__name__}"
            )
        self.matrix[capabilities.broker_name] = self._copy(capabilities)
        logger.info(
            "Registered broker %s with %d native order types.",
            capabilities.broker_name, len(capabilities.native_order_types),
        )

    def supports_native(self, broker_name: str, order_type: OrderType) -> bool:
        """Whether ``broker_name`` natively supports ``order_type``.

        An unregistered broker returns ``False`` — the conservative direction, since
        it routes to emulation rather than asserting native support for a venue this
        registry knows nothing about — and logs a warning, because in practice the
        cause is a misspelled broker key rather than a genuinely unknown venue.
        """
        if not isinstance(order_type, OrderType):
            raise TypeError(
                f"order_type must be an OrderType member, got {order_type!r}. A raw "
                f"string silently reports every order type as unsupported."
            )
        key = str(broker_name).strip().lower()
        capabilities = self.matrix.get(key)
        if capabilities is None:
            logger.warning(
                "Broker %r is not registered in the capability matrix; reporting %s as "
                "not natively supported.", broker_name, order_type.value,
            )
            return False
        return order_type in capabilities.native_order_types

    def plan_order_execution(
        self,
        broker_name: str,
        requested_order_type: OrderType,
        symbol: str,
        action: str,
        quantity: Numeric,
        price: Optional[Numeric] = None,
        stop_loss_price: Optional[Numeric] = None,
        take_profit_price: Optional[Numeric] = None,
        iceberg_slices: Optional[int] = None,
        twap_duration_minutes: Optional[int] = None,
        twap_slices: Optional[int] = None,
        min_slice_qty: Optional[Numeric] = None,
    ) -> SynthesizedOrderPlan:
        """Plan one order: route natively where supported, else synthesize legs.

        Args:
            broker_name: Registry key. An unknown broker raises — planning against an
                unregistered venue is a configuration error.
            requested_order_type: The order type the strategy asked for.
            symbol: Instrument identifier, carried onto the plan so a persisted
                emulated leg is self-describing.
            action: ``BUY`` or ``SELL``. For ``BRACKET`` this is the **entry** side and
                the exit legs invert it; for ``OCO`` it is the side of **both** exit
                legs. See ``_validate_price_geometry``.
            quantity: Parent quantity. Floats are converted through ``str``.
            price: Limit price. Required for ``ICEBERG``; used as the reference price
                for bracket/OCO geometry checks; makes a synthesized TWAP submit limit
                slices instead of market slices.
            stop_loss_price: Stop trigger. Required for ``OCO``; ``BRACKET`` needs this
                or ``take_profit_price``.
            take_profit_price: Profit target.
            iceberg_slices: Slice count for ``ICEBERG`` (default 5, minimum 2).
            twap_duration_minutes: Schedule span for ``TWAP`` (default 60).
            twap_slices: Slice count for ``TWAP`` (default 10, minimum 2).
            min_slice_qty: Venue minimum order quantity. When supplied, a schedule
                whose slices fall below it is rejected rather than submitted into
                certain rejection.

        Returns:
            A ``SynthesizedOrderPlan``. Follow its execution contract exactly; in
            particular do not fire the primary order *and* re-slice the parent
            quantity out of the feeder leg.

        Raises:
            ValueError: unknown broker, malformed argument, an argument the requested
                order type would silently discard, exit prices on the wrong side of
                the market, or an order type that is neither natively supported nor
                emulatable here.
            TypeError: ``requested_order_type`` is not an ``OrderType``.
        """
        if not isinstance(requested_order_type, OrderType):
            raise TypeError(
                f"requested_order_type must be an OrderType member, got "
                f"{requested_order_type!r}"
            )

        key = str(broker_name).strip().lower()
        if key not in self.matrix:
            raise ValueError(
                f"Broker '{broker_name}' is not registered in the capability matrix."
            )

        if not isinstance(symbol, str) or not symbol.strip():
            raise ValueError("symbol must be a non-empty string.")
        symbol = symbol.strip()

        if not isinstance(action, str):
            raise ValueError(f"action must be a string, got {type(action).__name__}")
        action = action.strip().upper()
        if action not in ("BUY", "SELL"):
            raise ValueError(f"Action must be BUY or SELL, got {action}")
        inverse_action = "SELL" if action == "BUY" else "BUY"

        qty = _positive_decimal(quantity, "quantity")
        limit_price = None if price is None else _positive_decimal(price, "price")
        stop_price = (
            None if stop_loss_price is None
            else _positive_decimal(stop_loss_price, "stop_loss_price")
        )
        target_price = (
            None if take_profit_price is None
            else _positive_decimal(take_profit_price, "take_profit_price")
        )
        floor_qty = (
            None if min_slice_qty is None
            else _positive_decimal(min_slice_qty, "min_slice_qty")
        )

        # An argument the requested order type does not consume is a misunderstanding,
        # not a harmless extra. Silently dropping stop_loss_price from a MARKET order
        # leaves the caller believing the position is protected when nothing watches
        # it, so every unused argument is refused by name.
        self._reject_unused_arguments(
            requested_order_type,
            stop_loss_price=stop_price,
            take_profit_price=target_price,
            iceberg_slices=iceberg_slices,
            twap_duration_minutes=twap_duration_minutes,
            twap_slices=twap_slices,
            min_slice_qty=floor_qty,
        )

        # Multi-leg completeness and price geometry are checked before the
        # native/emulated branch: a bracket with inverted exits, or with no exits at
        # all, is malformed whether or not the venue would have accepted it.
        if (
            requested_order_type is OrderType.BRACKET
            and stop_price is None
            and target_price is None
        ):
            raise ValueError(
                "BRACKET requires at least one of stop_loss_price or take_profit_price."
            )
        if requested_order_type is OrderType.OCO and (
            stop_price is None or target_price is None
        ):
            raise ValueError("OCO requires both stop_loss_price and take_profit_price.")
        _validate_price_geometry(
            requested_order_type, action, limit_price, stop_price, target_price
        )

        if self.supports_native(key, requested_order_type):
            logger.info(
                "Routing native %s to %s: %s %s %s.",
                requested_order_type.value, key, action, qty, symbol,
            )
            return SynthesizedOrderPlan(
                is_native=True,
                primary_order_type=requested_order_type,
                primary_action=action,
                primary_quantity=qty,
                emulated_legs=[],
                description=(
                    f"Native {requested_order_type.value} order routed to {broker_name}."
                ),
                symbol=symbol,
                primary_price=limit_price,
            )

        if requested_order_type not in EMULATABLE_ORDER_TYPES:
            raise ValueError(
                f"Broker '{broker_name}' has no native {requested_order_type.value} and "
                f"this planner cannot emulate it. Emulatable types: "
                f"{sorted(t.value for t in EMULATABLE_ORDER_TYPES)}. Choose a natively "
                f"supported type, or use a dedicated execution algo (see "
                f"execution-algo-twap-vwap-slicing for VWAP)."
            )

        if requested_order_type is OrderType.BRACKET:
            return self._synthesize_bracket(
                broker_name, symbol, action, inverse_action, qty,
                limit_price, stop_price, target_price,
            )
        if requested_order_type is OrderType.OCO:
            return self._synthesize_oco(
                broker_name, symbol, action, qty, stop_price, target_price,
            )
        if requested_order_type is OrderType.ICEBERG:
            return self._synthesize_iceberg(
                broker_name, symbol, action, qty, limit_price, iceberg_slices, floor_qty,
            )
        return self._synthesize_twap(
            broker_name, symbol, action, qty, limit_price,
            twap_duration_minutes, twap_slices, floor_qty,
        )

    #: Which optional arguments each requested order type actually consumes.
    _ARGUMENTS_BY_ORDER_TYPE: Dict[OrderType, Set[str]] = {
        OrderType.BRACKET: {"stop_loss_price", "take_profit_price"},
        OrderType.OCO: {"stop_loss_price", "take_profit_price"},
        OrderType.ICEBERG: {"iceberg_slices", "min_slice_qty"},
        OrderType.TWAP: {"twap_duration_minutes", "twap_slices", "min_slice_qty"},
    }

    @classmethod
    def _reject_unused_arguments(
        cls, order_type: OrderType, **supplied: Any
    ) -> None:
        """Refuse arguments the requested order type would silently discard."""
        consumed = cls._ARGUMENTS_BY_ORDER_TYPE.get(order_type, set())
        ignored = sorted(
            name for name, value in supplied.items()
            if value is not None and name not in consumed
        )
        if ignored:
            raise ValueError(
                f"{order_type.value} does not use {', '.join(ignored)}. Passing them "
                f"here has no effect on the plan; drop them, or request an order type "
                f"that consumes them."
            )

    # --- Synthesizers ---

    def _synthesize_bracket(
        self,
        broker_name: str,
        symbol: str,
        action: str,
        inverse_action: str,
        qty: Decimal,
        limit_price: Optional[Decimal],
        stop_price: Optional[Decimal],
        target_price: Optional[Decimal],
    ) -> SynthesizedOrderPlan:
        """Entry order now; both exits registered locally on the opposite side.

        The exits are a mutually exclusive pair covering the same quantity — the EMS
        must cancel the sibling when one triggers, and must not register either until
        the entry actually fills.
        """
        legs: List[EmulatedLeg] = []
        if stop_price is not None:
            legs.append(EmulatedLeg(
                leg_type="STOP_LOSS",
                action=inverse_action,
                quantity=qty,
                trigger_price=stop_price,
                metadata={"activate_on": "PRIMARY_FILL", "mutually_exclusive": True},
            ))
        if target_price is not None:
            legs.append(EmulatedLeg(
                leg_type="TAKE_PROFIT",
                action=inverse_action,
                quantity=qty,
                limit_price=target_price,
                metadata={"activate_on": "PRIMARY_FILL", "mutually_exclusive": True},
            ))

        logger.info(
            "Synthesized BRACKET for %s: primary=%s %s %s, %d emulated exit leg(s).",
            broker_name, action, qty, symbol, len(legs),
        )
        return SynthesizedOrderPlan(
            is_native=False,
            primary_order_type=(
                OrderType.LIMIT if limit_price is not None else OrderType.MARKET
            ),
            primary_action=action,
            primary_quantity=qty,
            emulated_legs=legs,
            description=(
                f"Software-emulated BRACKET order for {broker_name} (managed locally)."
            ),
            symbol=symbol,
            primary_price=limit_price,
        )

    def _synthesize_oco(
        self,
        broker_name: str,
        symbol: str,
        action: str,
        qty: Decimal,
        stop_price: Decimal,
        target_price: Decimal,
    ) -> SynthesizedOrderPlan:
        """Two conditional exits on the same side, and **no** order to fire now.

        ``primary_order_type`` is ``None`` here. An emulated OCO exists precisely
        because the broker has no native OCO, so naming ``OrderType.OCO`` as the
        primary type would send the caller straight back to the endpoint that cannot
        accept it.
        """
        legs = [
            EmulatedLeg(
                leg_type="STOP_LOSS",
                action=action,
                quantity=qty,
                trigger_price=stop_price,
                metadata={"mutually_exclusive": True},
            ),
            EmulatedLeg(
                leg_type="LIMIT_PROFIT",
                action=action,
                quantity=qty,
                limit_price=target_price,
                metadata={"mutually_exclusive": True},
            ),
        ]
        logger.info(
            "Synthesized OCO for %s: %s %s %s, no primary order.",
            broker_name, action, qty, symbol,
        )
        return SynthesizedOrderPlan(
            is_native=False,
            primary_order_type=None,
            primary_action=action,
            primary_quantity=Decimal(0),
            emulated_legs=legs,
            description=(
                f"Software-emulated OCO order for {broker_name} (mutually exclusive "
                f"triggers, no primary order to submit)."
            ),
            symbol=symbol,
        )

    @staticmethod
    def _check_slice_floor(
        schedule: Sequence[Decimal],
        floor_qty: Optional[Decimal],
        total: Decimal,
        slices: int,
        parameter: str,
    ) -> None:
        """Refuse a schedule whose smallest slice the venue would reject outright."""
        if floor_qty is None:
            return
        smallest = min(schedule)
        if smallest >= floor_qty:
            return
        affordable = int(total // floor_qty)
        if affordable < 2:
            raise ValueError(
                f"A parent quantity of {total} cannot be sliced at all above "
                f"min_slice_qty {floor_qty}. Send it as a single order, or increase the "
                f"quantity."
            )
        raise ValueError(
            f"{slices} slices of {total} produce a slice of {smallest}, below "
            f"min_slice_qty {floor_qty}. Use {parameter} <= {affordable}, or reduce the "
            f"parent quantity."
        )

    def _synthesize_iceberg(
        self,
        broker_name: str,
        symbol: str,
        action: str,
        qty: Decimal,
        limit_price: Optional[Decimal],
        iceberg_slices: Optional[int],
        floor_qty: Optional[Decimal],
    ) -> SynthesizedOrderPlan:
        """First slice now, remainder fed by the EMS as each slice completes.

        An iceberg requires ``price``: the point of the order type is a resting quote
        with a restricted display size. Slicing a *market* order instead sweeps the
        book one slice at a time, which is a bad TWAP rather than an iceberg — and it
        is why the venues offering iceberg natively (Binance ``icebergQty``, IBKR
        ``displaySize``) attach it to limit orders.
        """
        if limit_price is None:
            raise ValueError(
                "ICEBERG requires a price: an iceberg is a limit order with a restricted "
                "display size. Without one the EMS can only send market slices, which "
                "sweeps the book instead of resting on it."
            )
        slices = (
            DEFAULT_ICEBERG_SLICES if iceberg_slices is None
            else _positive_int(iceberg_slices, "iceberg_slices", minimum=2)
        )
        schedule = _build_slice_schedule(qty, slices)
        self._check_slice_floor(schedule, floor_qty, qty, slices, "iceberg_slices")

        remainder = schedule[1:]
        leg = EmulatedLeg(
            leg_type="SLICE_FEEDER",
            action=action,
            quantity=sum(remainder, Decimal(0)),
            limit_price=limit_price,
            slice_qty=schedule[0],
            metadata={
                "total_slices": slices,
                "remaining_slices": len(remainder),
                "total_quantity": qty,
                "slice_schedule": list(remainder),
                "replenish_on": "SLICE_FILL",
            },
        )
        logger.info(
            "Synthesized ICEBERG for %s: %s %s %s in %d slices at %s.",
            broker_name, action, qty, symbol, slices, limit_price,
        )
        return SynthesizedOrderPlan(
            is_native=False,
            primary_order_type=OrderType.LIMIT,
            primary_action=action,
            primary_quantity=schedule[0],
            emulated_legs=[leg],
            description=(
                f"Software-emulated ICEBERG order for {broker_name} ({slices} slices; "
                f"first slice is the primary order, {len(remainder)} held by the EMS)."
            ),
            symbol=symbol,
            primary_price=limit_price,
        )

    def _synthesize_twap(
        self,
        broker_name: str,
        symbol: str,
        action: str,
        qty: Decimal,
        limit_price: Optional[Decimal],
        twap_duration_minutes: Optional[int],
        twap_slices: Optional[int],
        floor_qty: Optional[Decimal],
    ) -> SynthesizedOrderPlan:
        """First slice now, the rest on a timer.

        Slice *k* fires at ``k * interval_seconds`` from submission, so the primary
        slice goes out at t=0 and the final slice one interval before the window
        closes. ``interval_seconds`` is floored to whole seconds, so a window that
        does not divide evenly finishes marginally early; ``effective_span_seconds``
        reports what the schedule actually spans.
        """
        duration = (
            DEFAULT_TWAP_DURATION_MINUTES if twap_duration_minutes is None
            else _positive_int(twap_duration_minutes, "twap_duration_minutes")
        )
        slices = (
            DEFAULT_TWAP_SLICES if twap_slices is None
            else _positive_int(twap_slices, "twap_slices", minimum=2)
        )
        interval_seconds = (duration * 60) // slices
        if interval_seconds < 1:
            raise ValueError(
                f"twap_duration_minutes={duration} over twap_slices={slices} gives an "
                f"interval below one second. Lengthen the window or use fewer slices."
            )

        schedule = _build_slice_schedule(qty, slices)
        self._check_slice_floor(schedule, floor_qty, qty, slices, "twap_slices")

        remainder = schedule[1:]
        leg = EmulatedLeg(
            leg_type="TWAP_FEEDER",
            action=action,
            quantity=sum(remainder, Decimal(0)),
            limit_price=limit_price,
            slice_qty=schedule[0],
            interval_seconds=interval_seconds,
            metadata={
                "total_slices": slices,
                "remaining_slices": len(remainder),
                "total_quantity": qty,
                "slice_schedule": list(remainder),
                "requested_duration_seconds": duration * 60,
                "effective_span_seconds": interval_seconds * (slices - 1),
                "first_slice_offset_seconds": interval_seconds,
            },
        )
        logger.info(
            "Synthesized TWAP for %s: %s %s %s over %d min in %d slices (%ds interval).",
            broker_name, action, qty, symbol, duration, slices, interval_seconds,
        )
        return SynthesizedOrderPlan(
            is_native=False,
            primary_order_type=(
                OrderType.LIMIT if limit_price is not None else OrderType.MARKET
            ),
            primary_action=action,
            primary_quantity=schedule[0],
            emulated_legs=[leg],
            description=(
                f"Software-emulated TWAP order for {broker_name} over {duration} minutes "
                f"({slices} slices; first slice is the primary order, {len(remainder)} "
                f"held by the EMS)."
            ),
            symbol=symbol,
            primary_price=limit_price,
        )
