"""
broker-api-versioning-migration-playbook: a phase-gated router for moving live order
flow from one broker API version to the next without duplicating orders, silently
changing order semantics, or losing the ability to go back.

The governing rule of this module is that **a version migration is a change to the
order path, so every unsafe default on that path is an order defect, not a config
defect.** Three consequences shape the whole design:

  1. **Canary routing must be deterministic per order, not per call.** A router that
     rolls ``random.random()`` on every call gives the *same* order a fresh coin flip
     on every retry. Submit, time out, retry — and the retry can land on the other API
     version, where the broker's client-order-id de-duplication namespace may not see
     the first attempt. One order, two versions, two fills. Routing here is a stable
     hash of ``client_order_id`` (BLAKE2b, not Python's per-process-salted ``hash()``),
     so every retry and every replica agrees on the same assignment.

  2. **A follow-up must reach the version that holds the order.** Determinism alone is
     not enough: ramping the canary from 5% to 25% re-buckets orders, so a cancel
     issued after the ramp can be aimed at a version that never saw the order. The
     migrator therefore keeps an explicit, bounded order->version affinity map
     (``route_followup_version``) and returns ``None`` rather than guessing when the
     binding has been evicted.

  3. **Ambiguity resolves toward the legacy version.** V1 is the version with a
     production track record. Rollback latches (``ROLLBACK_V1`` can only be left via an
     explicit, logged ``clear_rollback``) so an automated ramp scheduler cannot flip a
     known-bad version back into the order path.

Payload translation is deliberately **pluggable**. A migrator does not know your
broker's target schema, and inventing one is the exact failure this skill exists to
prevent. ``CoinbaseAdvancedTradeTranslator`` is a worked reference implementation whose
field names and ``order_configuration`` keys were checked against the Coinbase Advanced
Trade ``CreateOrder`` reference (see References below); it **rejects** any order type /
time-in-force combination it cannot express rather than substituting a nearby one. That
matters more than it sounds: silently mapping a LIMIT/IOC order onto ``limit_limit_gtc``
converts an immediate-or-cancel instruction into a resting order and leaves unintended
exposure in the book.

Scope limits — read these before wiring it into a live engine:

  - **This module does not send anything.** It decides *which version* a request goes
    to, translates a payload, and measures what came back. Transport, authentication,
    retries, and reconciliation belong to the caller.
  - **It is not a risk control.** Pre-trade risk checks must sit *above* the version
    router, so neither branch of a canary can bypass them. For US broker-dealers with
    market access this is not optional: SEC Rule 15c3-5(c)(1)(i) requires the financial
    risk controls to apply on a pre-trade basis, and 15c3-5(d)(1) requires them to be
    under the broker-dealer's direct and exclusive control.
  - **Shadow reads are best-effort and sheddable.** The V1 call runs on the calling
    thread and returns as soon as it completes; the V2 call is handed to a bounded
    background pool and is dropped when that pool is saturated. Python cannot interrupt
    a blocked worker thread, so ``v2_call`` **must** carry its own network timeout —
    the pending-shadow cap bounds the damage, it does not cancel the call.
  - **The affinity map and all counters are in-memory and per-process.** They do not
    survive a restart and are not shared across replicas. Hash routing *is* stable
    across both; affinity is not.
  - **Percentile gates need samples.** ``LatencyTracker`` reports
    ``percentiles_reliable`` and refuses to gate on a p99 estimated from too few
    observations, because a p99 taken from 200 samples rests on two data points.

References (consulted while writing this module):
  - Coinbase Advanced Trade API, ``CreateOrder`` request reference: top-level
    ``client_order_id`` / ``product_id`` / ``side`` (``BUY``|``SELL``) /
    ``order_configuration``; configuration keys ``market_market_ioc``,
    ``market_market_fok``, ``limit_limit_gtc``, ``limit_limit_gtd``,
    ``limit_limit_fok``, ``stop_limit_stop_limit_gtc``, ``stop_limit_stop_limit_gtd``;
    ``stop_limit_stop_limit_gtc`` requires ``base_size``, ``limit_price``,
    ``stop_price`` and ``stop_direction``
    (``STOP_DIRECTION_STOP_UP``|``STOP_DIRECTION_STOP_DOWN``); all sizes and prices are
    **strings**. There is no ``stop_stop_gtc`` key.
    https://docs.cdp.coinbase.com/api-reference/advanced-trade-api/rest-api/orders/create-order
  - Commission Delegated Regulation (EU) 2017/589 (MiFID II RTS 6): Article 6
    (conformance testing, required prior to the deployment or material update of an
    algorithmic trading system), Article 7 (testing environments separated from
    production), Article 8 (controlled deployment of algorithms), Article 12 (kill
    functionality). EU-authorised investment firms only.
  - 17 CFR 240.15c3-5 (SEC market access rule), paragraphs (b), (c)(1)(i) and (d)(1).
    US broker-dealers with market access only.
"""
import hashlib
import logging
import math
import random
import threading
import time
from collections import OrderedDict, deque
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from enum import Enum
from typing import Any, Callable, Dict, FrozenSet, List, Optional, Sequence, Tuple, Union

logger = logging.getLogger(__name__)

#: Value accepted for ``quantity`` / prices before normalisation to ``Decimal``.
Numeric = Union[str, int, float, Decimal]


class MigrationPhase(Enum):
    """Phases of a broker API version migration.

    The ordering is a gate sequence, not a suggestion: ``SHADOW_MODE`` proves read
    equivalence before any write moves, and ``CANARY_CUTOVER`` exposes writes
    incrementally before ``V2_ONLY``. ``set_phase`` enforces the legal transitions.
    """

    V1_ONLY = "V1_ONLY"
    SHADOW_MODE = "SHADOW_MODE"
    CANARY_CUTOVER = "CANARY_CUTOVER"
    V2_ONLY = "V2_ONLY"
    ROLLBACK_V1 = "ROLLBACK_V1"


#: Legal phase transitions. Two deliberate omissions:
#:   - ``V1_ONLY -> V2_ONLY`` and ``SHADOW_MODE -> V2_ONLY`` are illegal, because they
#:     put 100% of order flow on an API version whose write path has never carried a
#:     single order.
#:   - ``ROLLBACK_V1`` has no outbound edge. It is a latch: leaving it requires the
#:     explicit, logged ``clear_rollback()`` call, so an automated ramp scheduler
#:     cannot quietly re-promote the version that just failed.
_LEGAL_TRANSITIONS: Dict[MigrationPhase, FrozenSet[MigrationPhase]] = {
    MigrationPhase.V1_ONLY: frozenset(
        {MigrationPhase.V1_ONLY, MigrationPhase.SHADOW_MODE, MigrationPhase.ROLLBACK_V1}
    ),
    MigrationPhase.SHADOW_MODE: frozenset(
        {
            MigrationPhase.SHADOW_MODE,
            MigrationPhase.V1_ONLY,
            MigrationPhase.CANARY_CUTOVER,
            MigrationPhase.ROLLBACK_V1,
        }
    ),
    MigrationPhase.CANARY_CUTOVER: frozenset(
        {
            MigrationPhase.CANARY_CUTOVER,
            MigrationPhase.SHADOW_MODE,
            MigrationPhase.V2_ONLY,
            MigrationPhase.ROLLBACK_V1,
        }
    ),
    MigrationPhase.V2_ONLY: frozenset(
        {MigrationPhase.V2_ONLY, MigrationPhase.ROLLBACK_V1}
    ),
    MigrationPhase.ROLLBACK_V1: frozenset({MigrationPhase.ROLLBACK_V1}),
}

_V1 = "V1"
_V2 = "V2"


def _to_decimal(value: Numeric, field_name: str) -> Decimal:
    """Normalise a numeric input to ``Decimal`` without inheriting binary float error.

    ``Decimal(0.1)`` is 0.1000000000000000055511151231257827; ``Decimal(str(0.1))`` is
    exactly ``0.1``. Going through ``str`` keeps the value the caller actually wrote,
    which is what the broker will price against.
    """
    if isinstance(value, Decimal):
        candidate = value
    elif isinstance(value, bool):
        # bool is a subclass of int; a boolean quantity is always a caller mistake.
        raise ValueError(f"{field_name} must be numeric, got bool {value!r}")
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


def format_decimal(value: Decimal) -> str:
    """Render a ``Decimal`` as a fixed-point string with no exponent.

    ``str(1e-05)`` is ``'1e-05'`` and ``str(0.1 + 0.2)`` is
    ``'0.30000000000000004'`` — both are rejected or mispriced by brokers that parse
    decimal strings. ``format(value, 'f')`` never emits an exponent and preserves the
    caller's scale, so ``Decimal('65000.0')`` stays ``'65000.0'`` and
    ``Decimal('1E-5')`` becomes ``'0.00001'``.
    """
    return format(value, "f")


@dataclass(frozen=True)
class OrderPayload:
    """A version-neutral order instruction. Immutable once constructed.

    Validation happens here rather than in the translator so that an unusable order is
    rejected at construction — before it can be routed, counted, or half-translated.

    ``client_order_id`` is **required**: it is both the broker's de-duplication key and
    the input to deterministic canary routing, and an order without one cannot be made
    retry-safe during exactly the window where retries are most likely.

    The dataclass is frozen for the same reason. The migrator binds a version to the
    order id at routing time and a cancel is later routed from that binding; a payload
    mutated between routing and dispatch would leave the binding pointing at a version
    that never received this order, with no error anywhere to show for it.
    """

    symbol: str
    action: str
    quantity: Numeric
    client_order_id: str
    order_type: str = "MARKET"
    limit_price: Optional[Numeric] = None
    stop_price: Optional[Numeric] = None
    stop_direction: Optional[str] = None
    time_in_force: str = "GTC"

    def __post_init__(self) -> None:
        # object.__setattr__ because the dataclass is frozen; normalisation has to
        # happen before the instance is handed out.
        def _set(name: str, value: Any) -> None:
            object.__setattr__(self, name, value)

        for name in ("symbol", "action", "order_type", "time_in_force"):
            text = str(getattr(self, name)).strip().upper()
            if not text:
                raise ValueError(f"{name} must be a non-empty string")
            _set(name, text)

        client_order_id = str(self.client_order_id).strip()
        if not client_order_id:
            raise ValueError(
                "client_order_id is required: it is the broker de-duplication key and "
                "the deterministic canary routing key"
            )
        _set("client_order_id", client_order_id)

        quantity = _to_decimal(self.quantity, "quantity")
        if quantity <= 0:
            raise ValueError(f"quantity must be strictly positive, got {quantity}")
        _set("quantity", quantity)

        for name in ("limit_price", "stop_price"):
            raw = getattr(self, name)
            if raw is None:
                continue
            price = _to_decimal(raw, name)
            if price <= 0:
                raise ValueError(f"{name} must be strictly positive, got {price}")
            _set(name, price)

        if self.stop_direction is not None:
            _set("stop_direction", str(self.stop_direction).strip().upper() or None)


class PayloadTranslator:
    """Interface for translating an :class:`OrderPayload` into a target-version body.

    Implement one per broker. The contract every implementation must honour:

    - **Never substitute a nearby order semantic.** If the target version cannot
      express the requested (``order_type``, ``time_in_force``) pair, raise
      :class:`UnsupportedOrderError`. Downgrading IOC to GTC, or a stop-limit to a
      market order, changes what the order does in the book.
    - **Never default a missing price.** A limit order with no limit price is a caller
      bug; emitting ``"0"`` turns it into an order that either bounces or executes at a
      price nobody chose.
    """

    def translate(self, payload: OrderPayload) -> Dict[str, Any]:  # pragma: no cover
        raise NotImplementedError


class UnsupportedOrderError(ValueError):
    """The target API version cannot express this order without changing its meaning."""


class CoinbaseAdvancedTradeTranslator(PayloadTranslator):
    """Reference translator for the Coinbase Advanced Trade ``CreateOrder`` body.

    Field names and ``order_configuration`` keys follow the published CreateOrder
    reference (see the module docstring). Notable details that the obvious guess gets
    wrong:

    - The product identifier is ``product_id``, not ``instrument_id``/``symbol``.
    - The size lives *inside* ``order_configuration`` as ``base_size``, not at the top
      level, and every size and price is a **string**.
    - There is no ``stop_stop_gtc`` configuration. Stops are stop-*limit* orders
      (``stop_limit_stop_limit_gtc``) and require ``limit_price``, ``stop_price`` and
      ``stop_direction`` together — a stop order carrying only a stop price cannot be
      expressed and is rejected here rather than sent incomplete.
    - ``market_market_fok`` is documented as perpetuals-only; it is emitted when asked
      for, but the venue, not this translator, is the authority on eligibility.

    ``time_in_force`` selects the configuration key, so it can never be silently
    dropped. GTD is rejected: it requires an ``end_time`` that this version-neutral
    payload does not carry, and inventing one would change the order's lifetime.
    """

    SIDES: FrozenSet[str] = frozenset({"BUY", "SELL"})
    STOP_DIRECTIONS: FrozenSet[str] = frozenset(
        {"STOP_DIRECTION_STOP_UP", "STOP_DIRECTION_STOP_DOWN"}
    )
    _MARKET_ALIASES: FrozenSet[str] = frozenset({"MARKET"})
    _LIMIT_ALIASES: FrozenSet[str] = frozenset({"LIMIT"})
    _STOP_ALIASES: FrozenSet[str] = frozenset({"STOP", "STOP_LIMIT"})

    def translate(self, payload: OrderPayload) -> Dict[str, Any]:
        if payload.action not in self.SIDES:
            raise UnsupportedOrderError(
                f"side must be one of {sorted(self.SIDES)}, got {payload.action!r}"
            )

        tif = payload.time_in_force
        if tif == "GTD":
            raise UnsupportedOrderError(
                "GTD requires an end_time that OrderPayload does not carry; supply a "
                "translator that models expiry rather than silently changing the "
                "order's lifetime"
            )

        base_size = format_decimal(payload.quantity)

        if payload.order_type in self._MARKET_ALIASES:
            configuration = self._market_configuration(tif, base_size)
        elif payload.order_type in self._LIMIT_ALIASES:
            configuration = self._limit_configuration(tif, base_size, payload)
        elif payload.order_type in self._STOP_ALIASES:
            configuration = self._stop_limit_configuration(tif, base_size, payload)
        else:
            raise UnsupportedOrderError(
                f"unsupported order_type {payload.order_type!r}; supported: "
                "MARKET, LIMIT, STOP/STOP_LIMIT"
            )

        return {
            "client_order_id": payload.client_order_id,
            "product_id": payload.symbol,
            "side": payload.action,
            "order_configuration": configuration,
        }

    def _market_configuration(self, tif: str, base_size: str) -> Dict[str, Any]:
        # A market order is inherently immediate; Coinbase models it as IOC. FOK is a
        # genuinely different instruction and gets its own key.
        if tif in ("IOC", "GTC"):
            # GTC on a market order is not a resting instruction anywhere; it is the
            # library default and is mapped to the only market configuration that
            # exists. This is the one alias accepted, and it does not change behaviour.
            return {"market_market_ioc": {"base_size": base_size}}
        if tif == "FOK":
            return {"market_market_fok": {"base_size": base_size}}
        raise UnsupportedOrderError(
            f"MARKET orders support time_in_force IOC/GTC or FOK, got {tif!r}"
        )

    def _limit_configuration(
        self, tif: str, base_size: str, payload: OrderPayload
    ) -> Dict[str, Any]:
        if payload.limit_price is None:
            raise UnsupportedOrderError("LIMIT orders require limit_price")
        limit_price = format_decimal(payload.limit_price)

        if tif == "GTC":
            return {"limit_limit_gtc": {"base_size": base_size, "limit_price": limit_price}}
        if tif == "FOK":
            return {"limit_limit_fok": {"base_size": base_size, "limit_price": limit_price}}
        raise UnsupportedOrderError(
            f"LIMIT orders here support time_in_force GTC or FOK, got {tif!r}; an IOC "
            "limit order routes through SOR (sor_limit_ioc) and is not equivalent"
        )

    def _stop_limit_configuration(
        self, tif: str, base_size: str, payload: OrderPayload
    ) -> Dict[str, Any]:
        if tif != "GTC":
            raise UnsupportedOrderError(
                f"STOP_LIMIT here supports time_in_force GTC only, got {tif!r}"
            )
        missing = [
            name
            for name in ("limit_price", "stop_price", "stop_direction")
            if getattr(payload, name) is None
        ]
        if missing:
            raise UnsupportedOrderError(
                "stop_limit_stop_limit_gtc requires "
                f"{', '.join(missing)}; a stop order without them cannot be expressed"
            )
        if payload.stop_direction not in self.STOP_DIRECTIONS:
            raise UnsupportedOrderError(
                f"stop_direction must be one of {sorted(self.STOP_DIRECTIONS)}, "
                f"got {payload.stop_direction!r}"
            )
        return {
            "stop_limit_stop_limit_gtc": {
                "base_size": base_size,
                "limit_price": format_decimal(payload.limit_price),
                "stop_price": format_decimal(payload.stop_price),
                "stop_direction": payload.stop_direction,
            }
        }


@dataclass(frozen=True)
class SchemaAuditDiff:
    """Result of comparing one V1 read response against its V2 shadow.

    ``missing_in_v2`` and ``type_mismatches`` are keyed by **dotted path**
    (``account.balance``, ``fills[].price``) so nested drift is visible; top-level
    fields appear under their bare name.

    ``is_equivalent`` means "nothing observed differed". It does **not** mean "verified
    equivalent" — check ``unverified_paths`` too. A field that was ``null`` in either
    response, or a list that was empty on one side, carries no type information, and a
    shadow phase whose gate passes only because half the payload was null has proved
    nothing.
    """

    endpoint: str
    v1_keys: FrozenSet[str]
    v2_keys: FrozenSet[str]
    missing_in_v2: FrozenSet[str]
    type_mismatches: Dict[str, Tuple[type, type]]
    is_equivalent: bool
    latency_diff_ms: float
    added_in_v2: FrozenSet[str] = frozenset()
    unverified_paths: FrozenSet[str] = frozenset()


@dataclass(frozen=True)
class LatencySummary:
    """Descriptive statistics for one API version's observed round-trip times."""

    count: int
    mean_ms: Optional[float]
    p50_ms: Optional[float]
    p95_ms: Optional[float]
    p99_ms: Optional[float]
    max_ms: Optional[float]
    percentiles_reliable: bool


@dataclass(frozen=True)
class LatencyComparison:
    """V2-versus-V1 latency verdict against the configured tolerances."""

    v1: LatencySummary
    v2: LatencySummary
    mean_regression: Optional[float]
    p99_ratio: Optional[float]
    within_tolerance: Optional[bool]
    reasons: Tuple[str, ...]


def _percentile(sorted_values: Sequence[float], q: float) -> float:
    """Linear-interpolation percentile of an already-sorted sequence.

    ``q`` is a fraction in [0, 1]. Matches the common "linear" / type-7 definition:
    the rank is ``q * (n - 1)`` and the result interpolates between the two
    neighbouring order statistics.
    """
    if not sorted_values:
        raise ValueError("cannot take a percentile of an empty sequence")
    if not 0.0 <= q <= 1.0:
        raise ValueError(f"q must be in [0, 1], got {q}")
    if len(sorted_values) == 1:
        return float(sorted_values[0])
    rank = q * (len(sorted_values) - 1)
    lower = math.floor(rank)
    upper = math.ceil(rank)
    if lower == upper:
        return float(sorted_values[int(rank)])
    weight = rank - lower
    return float(sorted_values[lower] * (1.0 - weight) + sorted_values[upper] * weight)


class _Reservoir:
    """Fixed-memory, unbiased sample of an unbounded latency stream (Vitter's R).

    A ``deque(maxlen=n)`` keeps only the *most recent* n observations, which cannot
    support a percentile gate evaluated over a multi-session shadow phase: after two
    trading days of reads, the last thousand samples describe the last few minutes.
    Reservoir sampling keeps a uniform sample of everything seen since the phase began,
    in the same fixed memory, so ``p99`` describes the phase rather than the tail of it.

    Exact aggregates (``count``, ``sum``, ``max``) are maintained separately and are
    not estimates.
    """

    def __init__(self, capacity: int, rng: random.Random) -> None:
        if capacity < 1:
            raise ValueError(f"capacity must be >= 1, got {capacity}")
        self._capacity = capacity
        self._rng = rng
        self._samples: List[float] = []
        self.count = 0
        self._sum = 0.0
        self._max: Optional[float] = None

    def add(self, value: float) -> None:
        self.count += 1
        self._sum += value
        self._max = value if self._max is None else max(self._max, value)

        if len(self._samples) < self._capacity:
            self._samples.append(value)
            return
        # Element ``count`` (1-indexed) is retained with probability capacity/count.
        idx = self._rng.randrange(self.count)
        if idx < self._capacity:
            self._samples[idx] = value

    def summary(self, min_samples_for_percentiles: int) -> LatencySummary:
        if self.count == 0:
            return LatencySummary(0, None, None, None, None, None, False)
        ordered = sorted(self._samples)
        return LatencySummary(
            count=self.count,
            mean_ms=self._sum / self.count,
            p50_ms=_percentile(ordered, 0.50),
            p95_ms=_percentile(ordered, 0.95),
            p99_ms=_percentile(ordered, 0.99),
            max_ms=self._max,
            percentiles_reliable=self.count >= min_samples_for_percentiles,
        )


class LatencyTracker:
    """Thread-safe round-trip-time tracker for the two API versions.

    Exposes the statistics the migration gates are actually written against. The
    previous revision recorded samples and offered no way to read them back, so the
    documented "V2 mean latency within 5%, p99 within 1.2x" gate could not be evaluated
    at all.
    """

    def __init__(
        self,
        reservoir_size: int = 4096,
        min_samples_for_percentiles: int = 1000,
        seed: Optional[int] = None,
    ) -> None:
        if min_samples_for_percentiles < 1:
            raise ValueError("min_samples_for_percentiles must be >= 1")
        self._lock = threading.Lock()
        self._rng = random.Random(seed)
        self.min_samples_for_percentiles = min_samples_for_percentiles
        self._v1 = _Reservoir(reservoir_size, self._rng)
        self._v2 = _Reservoir(reservoir_size, self._rng)

    def record(self, v1_ms: Optional[float] = None, v2_ms: Optional[float] = None) -> None:
        """Record one or both versions' latency, in milliseconds.

        Either argument may be omitted: a shadow read that fails on V2 still yields a
        valid V1 observation, and discarding it would bias the V1 baseline toward the
        calls that happened to have a healthy shadow.
        """
        for value, reservoir, name in ((v1_ms, self._v1, "v1_ms"), (v2_ms, self._v2, "v2_ms")):
            if value is None:
                continue
            if not math.isfinite(value) or value < 0:
                raise ValueError(f"{name} must be a finite, non-negative float, got {value!r}")
        with self._lock:
            if v1_ms is not None:
                self._v1.add(float(v1_ms))
            if v2_ms is not None:
                self._v2.add(float(v2_ms))

    def snapshot(self) -> Dict[str, LatencySummary]:
        with self._lock:
            return {
                _V1: self._v1.summary(self.min_samples_for_percentiles),
                _V2: self._v2.summary(self.min_samples_for_percentiles),
            }

    def compare(
        self,
        max_mean_regression: float = 0.05,
        max_p99_ratio: float = 1.20,
    ) -> LatencyComparison:
        """Compare V2 against V1 under the configured tolerances.

        ``within_tolerance`` is ``None`` — not ``True`` — when there is not enough data
        to decide. "No evidence of a regression" and "evidence of no regression" are
        different claims, and a migration gate that conflates them promotes on silence.
        """
        stats = self.snapshot()
        v1, v2 = stats[_V1], stats[_V2]
        reasons: List[str] = []

        if v1.count == 0 or v2.count == 0:
            return LatencyComparison(v1, v2, None, None, None, ("insufficient samples",))

        mean_regression = (v2.mean_ms - v1.mean_ms) / v1.mean_ms if v1.mean_ms else None
        p99_ratio = (v2.p99_ms / v1.p99_ms) if v1.p99_ms else None

        # `breached` and `undecidable` are tracked separately on purpose. A confirmed
        # mean regression is a failure even when the p99 arm has too few samples to
        # decide; folding the two together would let "we could not check the tail" mask
        # "the average already regressed".
        breached = False
        undecidable = False

        if mean_regression is None:
            reasons.append("V1 mean latency is zero; mean regression undefined")
            undecidable = True
        elif mean_regression > max_mean_regression:
            reasons.append(
                f"V2 mean latency regressed {mean_regression:.2%} "
                f"(tolerance {max_mean_regression:.2%})"
            )
            breached = True

        if not (v1.percentiles_reliable and v2.percentiles_reliable):
            reasons.append(
                f"p99 not gated: fewer than {self.min_samples_for_percentiles} samples "
                f"(V1={v1.count}, V2={v2.count})"
            )
            undecidable = True
        elif p99_ratio is None:
            reasons.append("V1 p99 latency is zero; p99 ratio undefined")
            undecidable = True
        elif p99_ratio > max_p99_ratio:
            reasons.append(f"V2 p99 latency is {p99_ratio:.2f}x V1 (tolerance {max_p99_ratio:.2f}x)")
            breached = True

        if breached:
            within: Optional[bool] = False
        elif undecidable:
            within = None
        else:
            within = True

        return LatencyComparison(v1, v2, mean_regression, p99_ratio, within, tuple(reasons))


@dataclass(frozen=True)
class RollbackPolicy:
    """Thresholds that abort a cutover.

    These defaults are **illustrative starting points, not a standard**. No regulator
    or exchange publishes a canary error-rate threshold; calibrate them against your own
    V1 baseline, measured in ``V1_ONLY`` before the migration begins.
    """

    max_v2_error_rate: float = 0.02
    min_v2_orders_for_error_rate: int = 50
    max_mean_latency_regression: float = 0.05
    max_p99_latency_ratio: float = 1.20
    max_schema_drift_rate: float = 0.0
    min_audits_for_drift_rate: int = 100

    def __post_init__(self) -> None:
        for name in ("max_v2_error_rate", "max_schema_drift_rate"):
            value = getattr(self, name)
            if not 0.0 <= value <= 1.0:
                raise ValueError(f"{name} must be a rate in [0, 1], got {value}")
        if self.max_p99_latency_ratio <= 0:
            raise ValueError("max_p99_latency_ratio must be positive")


@dataclass(frozen=True)
class RollbackDecision:
    """Outcome of evaluating :class:`RollbackPolicy` against observed telemetry."""

    should_rollback: bool
    reasons: Tuple[str, ...]
    v2_error_rate: Optional[float]
    schema_drift_rate: Optional[float]
    latency: LatencyComparison


class BrokerAPIVersionMigrator:
    """Phase-gated router for a broker API version migration.

    Typical wiring::

        migrator = BrokerAPIVersionMigrator()
        migrator.set_phase(MigrationPhase.SHADOW_MODE)          # reads only
        ...
        migrator.set_phase(MigrationPhase.CANARY_CUTOVER, 0.01)  # 1% of writes

        version = migrator.route_order_version(payload)
        body = migrator.translate_payload_v1_to_v2(payload) if version == "V2" else legacy(payload)
        accepted = broker.submit(version, body)
        migrator.record_order_outcome(version, accepted)

        if migrator.enforce_rollback_policy().should_rollback:
            page_the_desk()

    Every mutating method is safe to call from multiple threads. Routing is
    deterministic per ``client_order_id``, so concurrent callers and separate replicas
    reach the same decision for the same order.
    """

    def __init__(
        self,
        canary_v2_percentage: float = 0.0,
        shadow_read_ratio: float = 1.0,
        translator: Optional[PayloadTranslator] = None,
        rollback_policy: Optional[RollbackPolicy] = None,
        audit_log_size: int = 2048,
        affinity_size: int = 50_000,
        max_pending_shadows: int = 64,
        shadow_workers: int = 4,
        latency_tracker: Optional[LatencyTracker] = None,
        shadow_sample_seed: Optional[int] = None,
    ) -> None:
        self._lock = threading.Lock()
        self.phase = MigrationPhase.V1_ONLY
        self.canary_v2_percentage = _validate_ratio(canary_v2_percentage, "canary_v2_percentage")
        self.shadow_read_ratio = _validate_ratio(shadow_read_ratio, "shadow_read_ratio")
        self.translator: PayloadTranslator = translator or CoinbaseAdvancedTradeTranslator()
        self.rollback_policy = rollback_policy or RollbackPolicy()

        self.v1_requests = 0
        self.v2_requests = 0
        self._order_outcomes: Dict[str, Dict[str, int]] = {
            _V1: {"accepted": 0, "rejected": 0},
            _V2: {"accepted": 0, "rejected": 0},
        }

        # Bounded: a 48-hour shadow phase over a busy read path appends one diff per
        # read, and an unbounded list is a slow memory leak in a process that must not
        # be restarted mid-session. Exact totals are kept separately.
        self.audit_log: deque = deque(maxlen=audit_log_size)
        self._audit_totals: Dict[str, int] = {"compared": 0, "drifted": 0, "unverified": 0}
        self._audit_drift_by_endpoint: Dict[str, int] = {}
        self._shadow_errors = 0
        self._shadow_shed = 0

        self.latency_tracker = latency_tracker or LatencyTracker()

        self._affinity: "OrderedDict[str, str]" = OrderedDict()
        self._affinity_size = max(0, affinity_size)

        self._rollback_reason: Optional[str] = None
        self._max_pending_shadows = max(0, max_pending_shadows)
        self._pending_shadows = 0
        # Shadow-read sampling only; the *routing* decision is deliberately not random.
        # Seeded so a test can pin which reads get shadowed. Always drawn under
        # ``self._lock`` — ``random.Random`` instances are not thread-safe.
        self._sample_rng = random.Random(shadow_sample_seed)
        self._shadow_executor = ThreadPoolExecutor(
            max_workers=max(1, shadow_workers), thread_name_prefix="api-shadow"
        )
        self._closed = False

    # ------------------------------------------------------------------ phases

    def set_phase(self, phase: MigrationPhase, canary_percentage: Optional[float] = None) -> None:
        """Move to ``phase``, enforcing the legal transition graph.

        ``canary_percentage`` is **rejected**, never clamped, when out of range. The
        clamping this replaces was an order-flow hazard: an operator typing ``50`` for
        "50%" silently got ``1.0`` and put every order on the untested version at once.
        Omitting it leaves the current value, so a phase change cannot quietly reset a
        ramp.

        Raises:
            ValueError: on an illegal transition, or an out-of-range percentage.
        """
        if canary_percentage is not None:
            canary_percentage = _validate_ratio(canary_percentage, "canary_percentage")

        with self._lock:
            current = self.phase
            if phase not in _LEGAL_TRANSITIONS[current]:
                if current is MigrationPhase.ROLLBACK_V1:
                    raise ValueError(
                        "ROLLBACK_V1 is latched; call clear_rollback(operator, reason) "
                        "before resuming a migration"
                    )
                raise ValueError(
                    f"illegal migration transition {current.value} -> {phase.value}; "
                    f"legal targets: {sorted(p.value for p in _LEGAL_TRANSITIONS[current])}"
                )

            if canary_percentage is not None:
                self.canary_v2_percentage = canary_percentage
            if phase in (MigrationPhase.V1_ONLY, MigrationPhase.ROLLBACK_V1):
                self.canary_v2_percentage = 0.0
            elif phase is MigrationPhase.V2_ONLY:
                self.canary_v2_percentage = 1.0

            self.phase = phase
            effective = self.canary_v2_percentage

        logger.info("Migration phase %s -> %s (canary V2 %.2f%%)", current.value, phase.value, effective * 100)

    def get_phase(self) -> MigrationPhase:
        with self._lock:
            return self.phase

    def trigger_rollback(self, reason: str) -> None:
        """Latch the migration back onto V1. Idempotent; safe from any thread.

        This is the emergency path, so it never raises on a legal-transition check —
        every phase may enter ``ROLLBACK_V1``.
        """
        reason = str(reason).strip() or "unspecified"
        with self._lock:
            already = self.phase is MigrationPhase.ROLLBACK_V1
            previous = self.phase
            self.phase = MigrationPhase.ROLLBACK_V1
            self.canary_v2_percentage = 0.0
            if not already:
                self._rollback_reason = reason
        if already:
            logger.warning("Rollback re-triggered (already latched): %s", reason)
        else:
            logger.critical("ROLLBACK to V1 from %s: %s", previous.value, reason)

    def clear_rollback(self, operator: str, reason: str) -> MigrationPhase:
        """Release the rollback latch and return to ``V1_ONLY``.

        Deliberately requires a named operator and a reason: the latch exists so a
        human decides that whatever broke V2 has been fixed. It never returns straight
        to ``CANARY_CUTOVER`` — the migration restarts from the gate sequence.
        """
        operator = str(operator).strip()
        reason = str(reason).strip()
        if not operator or not reason:
            raise ValueError("clear_rollback requires a non-empty operator and reason")
        with self._lock:
            if self.phase is not MigrationPhase.ROLLBACK_V1:
                raise ValueError(f"not in ROLLBACK_V1 (phase is {self.phase.value})")
            self.phase = MigrationPhase.V1_ONLY
            self.canary_v2_percentage = 0.0
            self._rollback_reason = None
        logger.warning("Rollback latch cleared by %s: %s", operator, reason)
        return MigrationPhase.V1_ONLY

    @property
    def rollback_reason(self) -> Optional[str]:
        with self._lock:
            return self._rollback_reason

    # ----------------------------------------------------------------- routing

    def route_order_version(self, payload: OrderPayload) -> str:
        """Return ``"V1"`` or ``"V2"`` for this order and record the binding.

        In ``CANARY_CUTOVER`` the decision is a stable BLAKE2b hash of
        ``client_order_id``, **not** a fresh random draw. Two properties follow, and
        both are load-bearing:

        - A retry of the same order under the same ``client_order_id`` routes to the
          same version. With per-call randomness, an order that timed out on V1 could
          be retried onto V2, where the broker's de-duplication may not recognise the
          first attempt — one intent, two live orders.
        - Every process computes the same assignment, because ``hashlib`` is stable
          across runs while Python's built-in ``hash()`` of a string is salted per
          process by ``PYTHONHASHSEED``.

        The chosen version is bound to the order id so cancels and modifies can be
        aimed at the version that holds it (see :meth:`route_followup_version`).
        """
        client_order_id = getattr(payload, "client_order_id", "") or ""
        with self._lock:
            phase = self.phase
            canary_pct = self.canary_v2_percentage

            if phase in (MigrationPhase.V1_ONLY, MigrationPhase.ROLLBACK_V1, MigrationPhase.SHADOW_MODE):
                # SHADOW_MODE shadows reads only; writes stay on V1 by definition.
                version = _V1
            elif phase is MigrationPhase.V2_ONLY:
                version = _V2
            elif not client_order_id:
                # Unreachable via OrderPayload, which requires the id. A duck-typed
                # payload without one cannot be routed deterministically, so fail safe
                # onto the version with a production track record.
                version = _V1
            else:
                version = _V2 if _stable_bucket(client_order_id) < canary_pct else _V1

            if version == _V1:
                self.v1_requests += 1
            else:
                self.v2_requests += 1

            if client_order_id:
                self._bind_locked(client_order_id, version)

        if not client_order_id:
            logger.error(
                "Order payload carried no client_order_id; routed to V1 and not bound "
                "for follow-up routing"
            )
        return version

    def bind_order_version(self, client_order_id: str, version: str) -> None:
        """Record (or correct) which API version actually holds an order.

        Call this after the broker accepts the order if the caller fell back to the
        other version — the binding written at routing time reflects intent, and a
        cancel must follow the order, not the intent.
        """
        version = _validate_version(version)
        client_order_id = str(client_order_id).strip()
        if not client_order_id:
            raise ValueError("client_order_id must be non-empty")
        with self._lock:
            self._bind_locked(client_order_id, version)

    def _bind_locked(self, client_order_id: str, version: str) -> None:
        if self._affinity_size == 0:
            return
        self._affinity[client_order_id] = version
        self._affinity.move_to_end(client_order_id)
        while len(self._affinity) > self._affinity_size:
            evicted_id, _ = self._affinity.popitem(last=False)
            logger.debug("Evicted version affinity for order %s", evicted_id)

    def route_followup_version(self, client_order_id: str) -> Optional[str]:
        """Version that holds ``client_order_id``, or ``None`` if it is not known.

        ``None`` is a real answer and must not be collapsed into a default. A cancel
        aimed at the version that never saw the order gets "unknown order" back, which
        is *not* proof the order is gone — see ``broker-api-idempotent-cancel-requests``.
        Query both versions instead of guessing.

        The map is bounded, so a binding can be evicted after ``affinity_size`` newer
        orders. Long-lived resting orders need a durable order ledger, not this cache.
        """
        client_order_id = str(client_order_id).strip()
        if not client_order_id:
            return None
        with self._lock:
            version = self._affinity.get(client_order_id)
            if version is not None:
                self._affinity.move_to_end(client_order_id)
            return version

    def translate_payload_v1_to_v2(self, payload: OrderPayload) -> Dict[str, Any]:
        """Translate a V1-shaped order into the configured target-version body.

        Delegates to :attr:`translator`. Raises :class:`UnsupportedOrderError` rather
        than emitting an order whose meaning differs from the one requested.
        """
        return self.translator.translate(payload)

    # ------------------------------------------------------------------ shadow

    def audit_shadow_response(
        self,
        endpoint: str,
        v1_response: Any,
        v2_response: Any,
        v1_latency_ms: float,
        v2_latency_ms: float,
    ) -> SchemaAuditDiff:
        """Recursively compare a V1 read response against its V2 shadow.

        The comparison descends into nested objects and lists. The previous revision
        compared top-level keys only, so a broker that moved a field one level down —
        or changed a price from number to string inside a nested fill record — passed
        the shadow gate cleanly. On a gate, a false negative is the dangerous direction.

        Lists are compared element-schema-wise using the first element of each side; a
        list that is empty on either side yields an ``unverified`` path rather than a
        silent pass.
        """
        self.latency_tracker.record(v1_latency_ms, v2_latency_ms)

        missing: List[str] = []
        added: List[str] = []
        mismatches: Dict[str, Tuple[type, type]] = {}
        unverified: List[str] = []
        _diff_structures("", v1_response, v2_response, missing, added, mismatches, unverified)

        v1_keys = frozenset(v1_response.keys()) if isinstance(v1_response, dict) else frozenset()
        v2_keys = frozenset(v2_response.keys()) if isinstance(v2_response, dict) else frozenset()

        diff = SchemaAuditDiff(
            endpoint=endpoint,
            v1_keys=v1_keys,
            v2_keys=v2_keys,
            missing_in_v2=frozenset(missing),
            type_mismatches=mismatches,
            is_equivalent=not missing and not mismatches,
            latency_diff_ms=v2_latency_ms - v1_latency_ms,
            added_in_v2=frozenset(added),
            unverified_paths=frozenset(unverified),
        )

        with self._lock:
            self.audit_log.append(diff)
            self._audit_totals["compared"] += 1
            if not diff.is_equivalent:
                self._audit_totals["drifted"] += 1
                self._audit_drift_by_endpoint[endpoint] = (
                    self._audit_drift_by_endpoint.get(endpoint, 0) + 1
                )
            if unverified:
                self._audit_totals["unverified"] += 1

        if not diff.is_equivalent:
            logger.warning(
                "Shadow audit drift on %s: missing_in_v2=%s type_mismatches=%s",
                endpoint,
                sorted(missing),
                {k: (a.__name__, b.__name__) for k, (a, b) in mismatches.items()},
            )
        elif unverified:
            logger.info(
                "Shadow audit on %s passed but %d path(s) were unverified (null or "
                "empty on one side): %s",
                endpoint,
                len(unverified),
                sorted(unverified)[:10],
            )
        return diff

    def execute_read_shadowing(
        self, v1_call: Callable[[], Any], v2_call: Callable[[], Any], endpoint: str
    ) -> Any:
        """Run the V1 read and return its result; shadow V2 in the background.

        The V1 call runs **on the calling thread** and its result is returned as soon
        as it completes. The previous revision submitted both calls to a
        ``with ThreadPoolExecutor(...)`` block, whose ``__exit__`` joins the pool — so a
        slow or hung V2 endpoint blocked the live read path it was supposed to leave
        alone, and V2's latency was measured only after V1's future had been awaited,
        biasing the very number the migration gate reads.

        The shadow is best-effort: it is dropped when ``max_pending_shadows`` are
        already in flight, and any exception it raises is counted, not propagated.
        ``v2_call`` must impose its own network timeout — a Python worker thread blocked
        in a socket read cannot be cancelled from outside.

        The V1 call is timed in **every** phase, including ``V1_ONLY``. That is what
        makes the pre-migration baseline exist: comparing shadow-phase V2 latency
        against a V1 sample first collected in the same phase gives the gate almost
        nothing to work with.
        """
        t0 = time.perf_counter()
        v1_result = v1_call()
        v1_latency_ms = (time.perf_counter() - t0) * 1000.0

        with self._lock:
            phase = self.phase
            ratio = self.shadow_read_ratio
            closed = self._closed
            shadowing_phase = phase in (MigrationPhase.SHADOW_MODE, MigrationPhase.CANARY_CUTOVER)
            saturated = shadowing_phase and self._pending_shadows >= self._max_pending_shadows
            sampled_out = ratio <= 0.0 or (ratio < 1.0 and self._sample_rng.random() >= ratio)
            shadow = shadowing_phase and not closed and not saturated and not sampled_out
            if saturated:
                self._shadow_shed += 1
            if shadow:
                self._pending_shadows += 1

        if not shadow:
            # No V2 sample this call, but the V1 observation is still real; dropping it
            # would condition the V1 baseline on the subset of calls that happened to
            # be shadowed.
            self.latency_tracker.record(v1_latency_ms, None)
            if saturated:
                logger.warning(
                    "Shadow read shed on %s: %d already in flight",
                    endpoint,
                    self._max_pending_shadows,
                )
            return v1_result

        try:
            self._shadow_executor.submit(
                self._run_shadow, endpoint, v2_call, v1_result, v1_latency_ms
            )
        except RuntimeError:
            # Pool shut down between the check and the submit.
            with self._lock:
                self._pending_shadows -= 1
                self._shadow_shed += 1
            self.latency_tracker.record(v1_latency_ms, None)
            logger.warning("Shadow read on %s dropped: executor is shut down", endpoint)
        return v1_result

    def _run_shadow(
        self,
        endpoint: str,
        v2_call: Callable[[], Any],
        v1_result: Any,
        v1_latency_ms: float,
    ) -> None:
        try:
            t0 = time.perf_counter()
            v2_result = v2_call()
            v2_latency_ms = (time.perf_counter() - t0) * 1000.0
            self.audit_shadow_response(endpoint, v1_result, v2_result, v1_latency_ms, v2_latency_ms)
        except Exception:
            # Shadow traffic must never surface into the production read path, but a
            # failing V2 endpoint is a migration signal, so it is counted rather than
            # only logged. The previous revision discarded it entirely.
            with self._lock:
                self._shadow_errors += 1
            self.latency_tracker.record(v1_latency_ms, None)
            logger.exception("V2 shadow call failed on %s (not propagated)", endpoint)
        finally:
            with self._lock:
                self._pending_shadows -= 1

    def drain_shadows(self, timeout_s: float = 5.0) -> int:
        """Block until in-flight shadow reads finish. Returns the number still pending.

        Intended for tests and for orderly shutdown; not for the trading loop.
        """
        deadline = time.monotonic() + max(0.0, timeout_s)
        while time.monotonic() < deadline:
            with self._lock:
                pending = self._pending_shadows
            if pending == 0:
                return 0
            time.sleep(0.005)
        with self._lock:
            return self._pending_shadows

    def close(self, timeout_s: float = 5.0) -> None:
        """Stop accepting shadows and shut the background pool down."""
        with self._lock:
            if self._closed:
                return
            self._closed = True
        self.drain_shadows(timeout_s)
        self._shadow_executor.shutdown(wait=True)

    # -------------------------------------------------------------- telemetry

    def record_order_outcome(self, version: str, accepted: bool) -> None:
        """Record whether the broker accepted an order sent to ``version``.

        Feeds the V2 error rate that :meth:`evaluate_rollback` gates on. Without this
        call the migrator has no view of write-path health, and the canary is a ramp
        with no abort criterion.
        """
        version = _validate_version(version)
        with self._lock:
            self._order_outcomes[version]["accepted" if accepted else "rejected"] += 1

    def stats(self) -> Dict[str, Any]:
        """Snapshot of routing, audit, and shadow counters."""
        with self._lock:
            return {
                "phase": self.phase.value,
                "canary_v2_percentage": self.canary_v2_percentage,
                "rollback_reason": self._rollback_reason,
                "v1_requests": self.v1_requests,
                "v2_requests": self.v2_requests,
                "order_outcomes": {v: dict(counts) for v, counts in self._order_outcomes.items()},
                "audit_totals": dict(self._audit_totals),
                "audit_drift_by_endpoint": dict(self._audit_drift_by_endpoint),
                "shadow_errors": self._shadow_errors,
                "shadow_shed": self._shadow_shed,
                "audit_log_retained": len(self.audit_log),
                "affinity_tracked": len(self._affinity),
            }

    def evaluate_rollback(self) -> RollbackDecision:
        """Evaluate the rollback policy against observed telemetry. Pure: no side effects.

        Latency is only gated once both versions have enough samples for the configured
        percentile confidence; until then the latency arm abstains rather than passing.
        """
        with self._lock:
            policy = self.rollback_policy
            v2 = dict(self._order_outcomes[_V2])
            compared = self._audit_totals["compared"]
            drifted = self._audit_totals["drifted"]

        reasons: List[str] = []

        v2_total = v2["accepted"] + v2["rejected"]
        v2_error_rate: Optional[float] = v2["rejected"] / v2_total if v2_total else None
        if v2_total >= policy.min_v2_orders_for_error_rate and v2_error_rate is not None:
            if v2_error_rate > policy.max_v2_error_rate:
                reasons.append(
                    f"V2 order rejection rate {v2_error_rate:.2%} over {v2_total} orders "
                    f"exceeds {policy.max_v2_error_rate:.2%}"
                )

        drift_rate: Optional[float] = drifted / compared if compared else None
        if compared >= policy.min_audits_for_drift_rate and drift_rate is not None:
            if drift_rate > policy.max_schema_drift_rate:
                reasons.append(
                    f"Shadow schema drift rate {drift_rate:.2%} over {compared} audits "
                    f"exceeds {policy.max_schema_drift_rate:.2%}"
                )

        latency = self.latency_tracker.compare(
            max_mean_regression=policy.max_mean_latency_regression,
            max_p99_ratio=policy.max_p99_latency_ratio,
        )
        if latency.within_tolerance is False:
            reasons.extend(latency.reasons)

        return RollbackDecision(
            should_rollback=bool(reasons),
            reasons=tuple(reasons),
            v2_error_rate=v2_error_rate,
            schema_drift_rate=drift_rate,
            latency=latency,
        )

    def enforce_rollback_policy(self) -> RollbackDecision:
        """Evaluate the policy and latch ``ROLLBACK_V1`` if it is breached.

        No-ops when V2 carries no order flow (``V1_ONLY``, ``SHADOW_MODE``,
        ``ROLLBACK_V1``): there is nothing to roll back, and firing there would turn a
        shadow-phase latency blip into a spurious latch.
        """
        decision = self.evaluate_rollback()
        if not decision.should_rollback:
            return decision
        if self.get_phase() not in (MigrationPhase.CANARY_CUTOVER, MigrationPhase.V2_ONLY):
            return decision
        self.trigger_rollback("; ".join(decision.reasons))
        return decision


def _validate_ratio(value: float, name: str) -> float:
    """Reject an out-of-range ratio instead of clamping it into range."""
    try:
        numeric = float(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{name} must be a float in [0.0, 1.0], got {value!r}") from exc
    if not math.isfinite(numeric) or not 0.0 <= numeric <= 1.0:
        raise ValueError(
            f"{name} must be a fraction in [0.0, 1.0], got {value!r} "
            "(0.5 means 50%; passing 50 would have meant 5000%)"
        )
    return numeric


def _validate_version(version: str) -> str:
    normalized = str(version).strip().upper()
    if normalized not in (_V1, _V2):
        raise ValueError(f"version must be 'V1' or 'V2', got {version!r}")
    return normalized


def _stable_bucket(client_order_id: str) -> float:
    """Map an order id to a stable fraction in [0, 1).

    BLAKE2b rather than ``hash()``: CPython salts string hashing per process
    (``PYTHONHASHSEED``), so ``hash()`` would assign the same order to different
    versions in different replicas and after every restart.
    """
    digest = hashlib.blake2b(client_order_id.encode("utf-8"), digest_size=8).digest()
    return int.from_bytes(digest, "big") / float(1 << 64)


def _join(prefix: str, part: str) -> str:
    return f"{prefix}.{part}" if prefix else part


def _diff_structures(
    path: str,
    v1: Any,
    v2: Any,
    missing: List[str],
    added: List[str],
    mismatches: Dict[str, Tuple[type, type]],
    unverified: List[str],
) -> None:
    """Recursively record schema drift between two decoded JSON structures.

    ``None`` on either side yields an ``unverified`` path rather than a mismatch: JSON
    ``null`` carries no type information, and flagging a nullable field that simply
    happened to be populated in one sample would fill the drift report with noise the
    operator learns to ignore. It is reported separately so a shadow phase cannot pass
    on a payload that was mostly null.
    """
    if v1 is None or v2 is None:
        if not (v1 is None and v2 is None):
            unverified.append(path or "<root>")
        return

    if isinstance(v1, dict) and isinstance(v2, dict):
        for key in v1:
            child = _join(path, str(key))
            if key not in v2:
                missing.append(child)
                continue
            _diff_structures(child, v1[key], v2[key], missing, added, mismatches, unverified)
        for key in v2:
            if key not in v1:
                added.append(_join(path, str(key)))
        return

    if isinstance(v1, list) and isinstance(v2, list):
        child = _join(path, "[]")
        if not v1 or not v2:
            # One side carries no element to compare against. "V1 returned fills and V2
            # returned none" is exactly the drift a shadow phase must not pass silently.
            if len(v1) != len(v2):
                unverified.append(child)
            return
        # Element schemas are assumed homogeneous; the first element of each side is
        # compared. A heterogeneous array needs a schema-aware differ — see
        # `broker-api-changelog-diffing-tool`.
        _diff_structures(child, v1[0], v2[0], missing, added, mismatches, unverified)
        return

    if type(v1) is not type(v2):
        mismatches[path or "<root>"] = (type(v1), type(v2))
