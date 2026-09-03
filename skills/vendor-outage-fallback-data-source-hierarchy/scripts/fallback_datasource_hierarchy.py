"""
vendor-outage-fallback-data-source-hierarchy: prioritised market-data vendor
failover with staleness detection, anti-flap promotion and a bounded synthetic
cache of last resort.

The engine answers one question, continuously: **which source, if any, is this
process entitled to price off right now?**

    Priority 1 healthy                      -> PRIMARY_ACTIVE
    Priority 1 unusable, a lower one healthy-> FAILOVER_ACTIVE
    no live source, cache fresh enough      -> SYNTHETIC_CACHE_ACTIVE  (is_synthetic=True)
    no live source, cache absent or expired -> ALL_SOURCES_DOWN        (raises)

Design rules this module follows, and why:

- **A cached price never claims to be a current price.** ``MarketDataTick.timestamp``
  is the moment the price was *observed live*, never the moment the object was built,
  and ``age_seconds`` states the gap. A synthetic tick stamped with "now" defeats every
  downstream staleness check, which is the single most dangerous thing this component
  could do. ``max_synthetic_age_seconds`` bounds how long the fallback may be served at
  all; past it the engine raises rather than returning a price.
- **Intervals are measured on a monotonic clock.** Staleness and the promotion window
  are durations. Measured on a wall clock, an NTP step backwards makes a frozen feed
  read as fresh, and a step forwards makes every vendor look stale at the same instant
  and dumps the process onto the synthetic cache. ``last_heartbeat_utc`` exists for the
  audit trail and is never used for arithmetic.
- **A vendor timestamp is not a liveness measurement.** ``record_heartbeat(timestamp=)``
  records the vendor's own stamp for audit only. Subtracting it from local time measures
  clock skew between two machines, not the age of the data.
- **Fail over fast, promote slow.** Losing the active source switches immediately.
  Taking routing *back* from a working source requires the challenger to have been
  *continuously* healthy for ``recovery_cooling_seconds``. "Time since the last
  failover" is not the same rule: it lets a single heartbeat, arriving after a long
  outage, instantly recapture routing from a working source -- exactly when a recovering
  vendor is least trustworthy.
- **Anti-flap never pins routing to a dead source.** The hold applies only while the
  currently active source is itself still healthy. Holding a measurably stale feed in
  order to avoid a switch is strictly worse than switching.
- **Every node is re-evaluated on every pass.** Stopping at the first healthy node
  leaves ``status`` on the nodes below it holding a value from some earlier evaluation,
  and that field is what operators and dashboards read.
- **A quote is validated before it is trusted or cached.** A ``NaN`` price propagates
  silently through every comparison and every position size downstream, and caching it
  poisons the fallback for the remainder of the outage. Invalid quotes are charged to
  the source as an error and the engine moves on.
- **Configuration errors raise; runtime vendor faults degrade.** Bad constructor or node
  parameters raise ``ValueError`` at setup. A vendor failure in the tick path degrades
  the hierarchy, because raising inside a feed handler's loop tends to be swallowed.
- **A source is unusable until it has proven otherwise.** A registered node that has
  never produced a heartbeat is ``DISCONNECTED``, not ``HEALTHY``. Registration records
  an intent to connect; it is not evidence that the connection works.
- **Retries are bounded by construction.** Each source is attempted at most once per
  ``fetch_market_data_tick`` call, so the fallback walk terminates in at most one pass
  down the hierarchy regardless of how the vendors misbehave.

Limitations (documented, deliberate):

- **Every threshold here is an engineering default, not a standard.** No regulator or
  exchange publishes a staleness limit, a promotion window or a maximum cache age. See
  ``references/standards.md``. Calibrate them against your own measured inter-tick gaps.
- **The engine cannot detect its own absence.** Staleness is only recomputed when
  something calls in. A supervisor must invoke ``evaluate_health_and_failover`` on a
  cadence well below the tightest ``max_staleness_seconds``, or a total feed stall is
  discovered only at the next fetch.
- **Single process.** State is in-memory and guarded by one lock. Two processes keep two
  independent hierarchies, two caches and two promotion timers.
- **``fetch_func`` runs while the engine lock is held**, so it must be a fast, local read
  of a tick the feed handler has already received -- not a synchronous network call. A
  blocking ``fetch_func`` stalls every other thread's ``record_heartbeat``, and because a
  delayed heartbeat is stamped when it finally acquires the lock rather than when the
  message arrived, that makes the feed read *fresher* than it is. Keeping I/O out of the
  callback is what keeps the staleness measurement honest.
- **Prices are not reconciled across vendors.** The engine picks a source; it does not
  check that the sources agree. Cross-vendor divergence belongs to
  ``market-data-feed-arbitration-across-vendors`` and
  ``multi-source-price-reconciliation-tie-breaking``.
- **The event log is in memory and bounded.** It is operational telemetry, not the
  system of record. Persist events externally if you need them for an audit.
- **This is not a risk control.** It reports ``is_synthetic`` and ``age_seconds``; it
  does not stop trading. Wire those into
  ``graduated-response-to-data-quality-degradation`` or
  ``kill-switch-and-drawdown-circuit-breakers`` to make them act.
"""
import datetime
import logging
import math
import numbers
import threading
import time
from collections import deque
from dataclasses import dataclass
from enum import Enum
from typing import Callable, Deque, Dict, List, Optional, Sequence, Tuple

logger = logging.getLogger(__name__)

#: Seconds a challenger must be *continuously* healthy before it may take routing from
#: a still-healthy incumbent. Does not delay failover away from a failed source.
#: Engineering default.
DEFAULT_RECOVERY_COOLING_SECONDS = 30.0

#: Per-node inter-heartbeat gap beyond which a source is STALE. Engineering default;
#: set it from the feed's measured normal gap, not from a vendor SLA.
DEFAULT_MAX_STALENESS_SECONDS = 5.0

#: Consecutive-ish failures charged to a node before it is dropped to ERROR.
DEFAULT_MAX_ERROR_THRESHOLD = 3

#: Age beyond which a cached quote is refused rather than served. Engineering default.
DEFAULT_MAX_SYNTHETIC_AGE_SECONDS = 30.0

#: In-memory event ring size. Events are telemetry; persist externally for audit.
DEFAULT_MAX_EVENT_LOG_ENTRIES = 10_000

#: Reserved identifier for the cache-of-last-resort. Not a registrable source id.
SYNTHETIC_SOURCE_ID = "SYNTHETIC_CACHE"


class DataSourceStatus(Enum):
    """Measured usability of one vendor feed."""

    HEALTHY = "HEALTHY"
    STALE = "STALE"
    DISCONNECTED = "DISCONNECTED"
    ERROR = "ERROR"


class EngineState(Enum):
    """Which tier of the hierarchy the engine is currently routing to."""

    PRIMARY_ACTIVE = "PRIMARY_ACTIVE"
    FAILOVER_ACTIVE = "FAILOVER_ACTIVE"
    SYNTHETIC_CACHE_ACTIVE = "SYNTHETIC_CACHE_ACTIVE"
    ALL_SOURCES_DOWN = "ALL_SOURCES_DOWN"


class FailoverEventKind(Enum):
    """Why the active source changed.

    Separated so that an audit reader can distinguish process start-up from a genuine
    vendor incident. Recording the first selection at start-up as a "failover" makes
    every restart look like an outage.
    """

    INITIAL_SELECTION = "INITIAL_SELECTION"
    FAILOVER = "FAILOVER"
    RESTORE = "RESTORE"
    SYNTHETIC_FALLBACK = "SYNTHETIC_FALLBACK"


class FallbackEngineError(Exception):
    """Raised when no usable price can be produced, or a source is not registered."""


def _utc_now() -> datetime.datetime:
    """Timezone-aware UTC wall clock, for audit stamps only."""
    return datetime.datetime.now(datetime.timezone.utc)


def _as_utc(value: datetime.datetime) -> datetime.datetime:
    """Normalise a datetime to timezone-aware UTC.

    A naive datetime is interpreted as UTC, matching this module's own stamps. Without
    this, mixing a caller's aware timestamp with a naive internal one raises
    ``TypeError: can't subtract offset-naive and offset-aware datetimes``.
    """
    if not isinstance(value, datetime.datetime):
        raise ValueError(f"timestamp must be a datetime, got {type(value).__name__}")
    if value.tzinfo is None:
        return value.replace(tzinfo=datetime.timezone.utc)
    return value.astimezone(datetime.timezone.utc)


def _require_finite(value: object, label: str) -> float:
    """Coerce to float, rejecting bools, non-numbers, NaN and infinities."""
    if isinstance(value, bool) or not isinstance(value, numbers.Real):
        raise ValueError(f"{label} must be a real number, got {value!r}")
    out = float(value)
    if not math.isfinite(out):
        raise ValueError(f"{label} must be finite, got {out!r}")
    return out


@dataclass
class DataSourceNode:
    """One vendor feed in the hierarchy.

    A newly constructed node is ``DISCONNECTED`` and has no heartbeat: registration
    declares an intent to connect, not a working connection. Call
    :meth:`VendorFallbackHierarchyEngine.record_heartbeat` once the feed session is
    established and on every subsequent tick or heartbeat.

    ``last_heartbeat_monotonic`` is the measurement basis for staleness.
    ``last_heartbeat_utc`` is a human- and audit-readable stamp and is never used for
    arithmetic. ``healthy_since_monotonic`` is the start of the current unbroken run of
    health, and is cleared the moment the node is observed unhealthy.
    """

    source_id: str
    name: str
    priority: int
    max_staleness_seconds: float = DEFAULT_MAX_STALENESS_SECONDS
    max_error_threshold: int = DEFAULT_MAX_ERROR_THRESHOLD
    is_active: bool = True
    error_count: int = 0
    status: DataSourceStatus = DataSourceStatus.DISCONNECTED
    last_heartbeat_utc: Optional[datetime.datetime] = None
    last_heartbeat_monotonic: Optional[float] = None
    healthy_since_monotonic: Optional[float] = None

    def __post_init__(self) -> None:
        if not isinstance(self.source_id, str) or not self.source_id.strip():
            raise ValueError("source_id must be a non-empty string")
        if self.source_id == SYNTHETIC_SOURCE_ID:
            raise ValueError(
                f"source_id {SYNTHETIC_SOURCE_ID!r} is reserved for the synthetic cache"
            )
        if not isinstance(self.name, str) or not self.name.strip():
            raise ValueError("name must be a non-empty string")
        if isinstance(self.priority, bool) or not isinstance(self.priority, int):
            raise ValueError("priority must be an int (1 = highest priority)")
        if self.priority < 1:
            raise ValueError(f"priority must be >= 1, got {self.priority}")
        self.max_staleness_seconds = _require_finite(
            self.max_staleness_seconds, "max_staleness_seconds"
        )
        if self.max_staleness_seconds <= 0:
            raise ValueError(
                f"max_staleness_seconds must be > 0, got {self.max_staleness_seconds}"
            )
        if isinstance(self.max_error_threshold, bool) or not isinstance(
            self.max_error_threshold, int
        ):
            raise ValueError("max_error_threshold must be an int")
        if self.max_error_threshold < 1:
            raise ValueError(
                f"max_error_threshold must be >= 1, got {self.max_error_threshold}"
            )


@dataclass
class CachedQuote:
    """The last live quote seen for one symbol, with the clocks needed to age it.

    Both clocks are stored deliberately: ``observed_at_monotonic`` measures the age and
    ``observed_at_utc`` is what a human or an auditor reads.
    """

    symbol: str
    price: float
    volume: float
    source_id: str
    observed_at_utc: datetime.datetime
    observed_at_monotonic: float


@dataclass
class MarketDataTick:
    """A quote handed to the caller.

    ``timestamp`` is when the price was **observed live**, never when this object was
    constructed. For ``is_synthetic=True`` it is therefore the original observation time
    and ``age_seconds`` is how stale the price is. A caller that gates on tick age can
    use either field and get the same answer.
    """

    symbol: str
    price: float
    volume: float
    timestamp: datetime.datetime
    source_id: str
    is_synthetic: bool = False
    age_seconds: float = 0.0


@dataclass
class FailoverEvent:
    """One recorded change of active source."""

    event_id: str
    timestamp: datetime.datetime
    previous_source_id: Optional[str]
    new_source_id: str
    reason: str
    engine_state: EngineState
    kind: FailoverEventKind = FailoverEventKind.FAILOVER


class VendorFallbackHierarchyEngine:
    """Prioritised market-data vendor failover with anti-flap promotion.

    Thread-safe: every public method takes one reentrant lock, so a feed-handler thread
    calling :meth:`record_heartbeat` cannot interleave with a strategy thread calling
    :meth:`fetch_market_data_tick`.
    """

    def __init__(
        self,
        recovery_cooling_seconds: float = DEFAULT_RECOVERY_COOLING_SECONDS,
        max_synthetic_age_seconds: float = DEFAULT_MAX_SYNTHETIC_AGE_SECONDS,
        allow_non_positive_prices: bool = False,
        max_event_log_entries: int = DEFAULT_MAX_EVENT_LOG_ENTRIES,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        """
        :param recovery_cooling_seconds: How long a challenger must be *continuously*
            healthy before it may take routing from a still-healthy incumbent. Does not
            delay failover away from a source that has stopped being healthy. ``0.0``
            disables the hold and promotes on the first healthy observation.
        :param max_synthetic_age_seconds: Maximum age of a cached quote that may still
            be served. Past it :meth:`fetch_market_data_tick` raises rather than
            returning an obsolete price.
        :param allow_non_positive_prices: Leave ``False`` for equities, FX and crypto,
            where a price of zero or below is a bad tick. Set ``True`` only for
            instruments that can legitimately print at or below zero -- notably energy
            futures and calendar spreads. See ``references/standards.md``.
        :param max_event_log_entries: Size of the in-memory event ring.
        :param clock: Monotonic time source, injected for testability. Passing a wall
            clock reintroduces the NTP-step failures this parameter exists to avoid.
        """
        if not callable(clock):
            raise ValueError("clock must be a callable returning a monotonic float")
        self.recovery_cooling_seconds = _require_finite(
            recovery_cooling_seconds, "recovery_cooling_seconds"
        )
        if self.recovery_cooling_seconds < 0:
            raise ValueError(
                f"recovery_cooling_seconds must be >= 0, got {recovery_cooling_seconds}"
            )
        self.max_synthetic_age_seconds = _require_finite(
            max_synthetic_age_seconds, "max_synthetic_age_seconds"
        )
        if self.max_synthetic_age_seconds < 0:
            raise ValueError(
                f"max_synthetic_age_seconds must be >= 0, got {max_synthetic_age_seconds}"
            )
        if not isinstance(allow_non_positive_prices, bool):
            raise ValueError("allow_non_positive_prices must be a bool")
        if isinstance(max_event_log_entries, bool) or not isinstance(
            max_event_log_entries, int
        ):
            raise ValueError("max_event_log_entries must be an int")
        if max_event_log_entries < 1:
            raise ValueError(
                f"max_event_log_entries must be >= 1, got {max_event_log_entries}"
            )

        self.allow_non_positive_prices = allow_non_positive_prices
        self.data_sources: Dict[str, DataSourceNode] = {}
        self.active_source_id: Optional[str] = None
        self.engine_state: EngineState = EngineState.ALL_SOURCES_DOWN
        self.synthetic_cache: Dict[str, CachedQuote] = {}
        self.event_log: Deque[FailoverEvent] = deque(maxlen=max_event_log_entries)
        self.last_failover_utc: Optional[datetime.datetime] = None

        self._clock = clock
        self._lock = threading.RLock()
        self._event_sequence = 0

        logger.info(
            "Vendor fallback hierarchy initialised "
            "(promotion window=%.1fs, max synthetic age=%.1fs, non-positive prices=%s)",
            self.recovery_cooling_seconds,
            self.max_synthetic_age_seconds,
            self.allow_non_positive_prices,
        )

    # ------------------------------------------------------------------ registration

    def register_data_source(self, node: DataSourceNode) -> None:
        """Register a vendor feed.

        The node starts ``DISCONNECTED``. It becomes eligible for routing only after
        :meth:`record_heartbeat` proves the session is live.

        :raises ValueError: if ``source_id`` is already registered. Silently replacing a
            registered vendor is how a hierarchy that looks three deep turns out to be
            one deep.
        """
        if not isinstance(node, DataSourceNode):
            raise ValueError(f"node must be a DataSourceNode, got {type(node).__name__}")
        with self._lock:
            existing = self.data_sources.get(node.source_id)
            if existing is not None:
                raise ValueError(
                    f"source_id {node.source_id!r} is already registered to "
                    f"{existing.name!r}; deregister it before re-registering"
                )
            self.data_sources[node.source_id] = node
            logger.info(
                "Registered data source [%s] id=%s priority=%d max_staleness=%.1fs "
                "(status=DISCONNECTED until first heartbeat)",
                node.name,
                node.source_id,
                node.priority,
                node.max_staleness_seconds,
            )
            self._evaluate_locked()

    def deregister_data_source(self, source_id: str) -> None:
        """Remove a vendor feed from the hierarchy and re-evaluate routing."""
        with self._lock:
            node = self.data_sources.pop(source_id, None)
            if node is None:
                raise FallbackEngineError(f"Data source {source_id!r} is not registered.")
            logger.info("Deregistered data source [%s] id=%s", node.name, source_id)
            if self.active_source_id == source_id:
                self.active_source_id = None
            self._evaluate_locked()

    # ---------------------------------------------------------------- feed telemetry

    def record_heartbeat(
        self, source_id: str, timestamp: Optional[datetime.datetime] = None
    ) -> None:
        """Record a tick or heartbeat from a feed. Call this on every message.

        :param timestamp: The vendor's own stamp, recorded for audit only. Liveness is
            always measured on the injected monotonic clock, because subtracting a
            vendor timestamp from local time measures the skew between two machines
            rather than the age of the data. Naive values are read as UTC.
        """
        with self._lock:
            node = self._require_node(source_id)
            now = self._clock()
            node.last_heartbeat_monotonic = now
            node.last_heartbeat_utc = _as_utc(timestamp) if timestamp is not None else _utc_now()
            if node.status == DataSourceStatus.DISCONNECTED:
                # A message is proof of connectivity, so a heartbeat clears an explicit
                # disconnect. Without this the DISCONNECTED state would be terminal.
                logger.info(
                    "Data source [%s] reconnected: heartbeat received while DISCONNECTED",
                    node.name,
                )
                node.status = DataSourceStatus.STALE
            if node.error_count > 0:
                # Decay the error budget by one per good message, so a source that
                # recovers is not latched out of the hierarchy for want of an operator.
                # This alone would let a single heartbeat lift a node straight out of
                # ERROR; what stops that becoming a flap is the promotion window, which
                # a recovered node must still serve before it can take routing back.
                node.error_count -= 1
            logger.debug(
                "Heartbeat [%s] at monotonic=%.3f utc=%s",
                node.name,
                now,
                node.last_heartbeat_utc.isoformat(),
            )
            self._evaluate_locked()

    def record_error(self, source_id: str, error_msg: str = "") -> None:
        """Charge one failure to a feed; drop it to ERROR once its budget is spent."""
        with self._lock:
            self._require_node(source_id)
            self._record_error_locked(source_id, error_msg)
            self._evaluate_locked()

    def reset_error_count(self, source_id: str) -> None:
        """Clear a source's error budget in one step, e.g. after a supervised reconnect.

        A convenience, not a requirement: the budget also decays one per heartbeat. The
        node still has to serve the full promotion window before it can take routing
        back from a healthy incumbent.
        """
        with self._lock:
            node = self._require_node(source_id)
            node.error_count = 0
            if node.status == DataSourceStatus.ERROR:
                node.status = DataSourceStatus.STALE
                node.healthy_since_monotonic = None
                logger.info("Data source [%s] error budget cleared by operator", node.name)
            self._evaluate_locked()

    def mark_disconnected(self, source_id: str, reason: str = "") -> None:
        """Mark a feed as disconnected on a socket close, logout or session drop.

        Faster than waiting for staleness to expire: the transport already knows. The
        node is restored by the next :meth:`record_heartbeat`.
        """
        with self._lock:
            node = self._require_node(source_id)
            node.status = DataSourceStatus.DISCONNECTED
            node.healthy_since_monotonic = None
            logger.warning("Data source [%s] marked DISCONNECTED: %s", node.name, reason)
            self._evaluate_locked()

    # -------------------------------------------------------------- health & routing

    def check_node_health(
        self, node: DataSourceNode, now_monotonic: float
    ) -> DataSourceStatus:
        """Evaluate one node against its disconnect, error and staleness conditions.

        :param now_monotonic: Reading from the engine's monotonic clock, not a wall
            clock and not a ``datetime``.
        """
        if not node.is_active:
            return DataSourceStatus.DISCONNECTED
        if node.status == DataSourceStatus.DISCONNECTED:
            return DataSourceStatus.DISCONNECTED
        if node.error_count >= node.max_error_threshold:
            return DataSourceStatus.ERROR
        if node.last_heartbeat_monotonic is None:
            # Never observed. Registration is not evidence of a working connection.
            return DataSourceStatus.DISCONNECTED
        staleness = now_monotonic - node.last_heartbeat_monotonic
        if staleness > node.max_staleness_seconds:
            return DataSourceStatus.STALE
        return DataSourceStatus.HEALTHY

    def get_prioritized_sources(self) -> List[DataSourceNode]:
        """Registered sources ordered by priority, ties broken by ``source_id``.

        The tie-break is explicit so that two vendors registered at the same priority
        are ordered identically on every process and every restart.
        """
        with self._lock:
            return sorted(
                self.data_sources.values(), key=lambda n: (n.priority, n.source_id)
            )

    def evaluate_health_and_failover(
        self,
    ) -> Tuple[Optional[DataSourceNode], Optional[FailoverEvent]]:
        """Re-measure every source and select the one to route to.

        Call this on a supervisor cadence well below the tightest
        ``max_staleness_seconds``; the engine has no timer of its own and cannot notice
        a total feed stall between calls.

        :returns: ``(active_node, event)``. ``active_node`` is ``None`` when no live
            source is usable and the engine has fallen back to the synthetic cache.
        """
        with self._lock:
            return self._evaluate_locked()

    def _evaluate_locked(
        self,
    ) -> Tuple[Optional[DataSourceNode], Optional[FailoverEvent]]:
        now = self._clock()
        sources = sorted(
            self.data_sources.values(), key=lambda n: (n.priority, n.source_id)
        )

        # Refresh every node, not just those above the first healthy one: `status` is
        # the field operators and dashboards read, and a partially refreshed hierarchy
        # reports health that was measured at some unknown earlier time.
        best: Optional[DataSourceNode] = None
        for node in sources:
            previous = node.status
            node.status = self.check_node_health(node, now)
            if node.status == DataSourceStatus.HEALTHY:
                if node.healthy_since_monotonic is None:
                    node.healthy_since_monotonic = now
            else:
                node.healthy_since_monotonic = None
                if previous == DataSourceStatus.HEALTHY:
                    logger.warning(
                        "Data source [%s] degraded HEALTHY -> %s",
                        node.name,
                        node.status.value,
                    )
            if best is None and node.status == DataSourceStatus.HEALTHY:
                best = node

        previous_id = self.active_source_id
        target = best

        if target is not None and previous_id not in (None, target.source_id):
            incumbent = self.data_sources.get(previous_id)
            # Fail over fast, promote slow. While the incumbent is still healthy, no
            # challenger takes routing from it until the challenger has served the whole
            # promotion window -- which also covers two sources registered at the same
            # priority, where a priority comparison would permit an unchecked swap.
            # The moment the incumbent stops being healthy the hold lapses: pinning
            # routing to a source just measured stale, in the name of avoiding a switch,
            # would hand the strategy a dead feed while a working one sat idle.
            if (
                incumbent is not None
                and incumbent.status == DataSourceStatus.HEALTHY
                and not self._stability_satisfied(target, now)
            ):
                held_for = (
                    0.0
                    if target.healthy_since_monotonic is None
                    else now - target.healthy_since_monotonic
                )
                logger.info(
                    "Promotion of [%s] held: healthy for %.1fs of the %.1fs window; "
                    "staying on [%s]",
                    target.name,
                    held_for,
                    self.recovery_cooling_seconds,
                    incumbent.name,
                )
                target = incumbent

        if target is not None:
            state = (
                EngineState.PRIMARY_ACTIVE
                if target.priority == 1
                else EngineState.FAILOVER_ACTIVE
            )
            event = None
            if previous_id != target.source_id:
                event = self._record_transition(
                    previous_id=previous_id,
                    new_source_id=target.source_id,
                    state=state,
                    kind=self._classify(previous_id, target),
                    reason=(
                        f"Routing to priority {target.priority} source "
                        f"{target.name!r} ({target.source_id})"
                    ),
                )
            self.active_source_id = target.source_id
            self.engine_state = state
            return target, event

        # No live source is usable. Whether the cache can actually cover a given symbol
        # is resolved per-symbol at fetch time, since the cache is per-symbol.
        if self.synthetic_cache:
            new_active: Optional[str] = SYNTHETIC_SOURCE_ID
            state = EngineState.SYNTHETIC_CACHE_ACTIVE
        else:
            new_active = None
            state = EngineState.ALL_SOURCES_DOWN

        event = None
        if previous_id is not None and previous_id != SYNTHETIC_SOURCE_ID:
            # Only a transition *away from a live source* is an outage. A cold start,
            # where nothing has beaten yet, is not an incident and must not fill the
            # audit trail with one on every process restart.
            event = self._record_transition(
                previous_id=previous_id,
                new_source_id=SYNTHETIC_SOURCE_ID,
                state=state,
                kind=FailoverEventKind.SYNTHETIC_FALLBACK,
                reason="No live data source is usable; falling back to synthetic cache.",
            )
            logger.critical(
                "DATA OUTAGE: no live source usable across %d registered vendor(s). "
                "Engine state %s.",
                len(self.data_sources),
                state.value,
            )
        self.active_source_id = new_active
        self.engine_state = state
        return None, event

    def _stability_satisfied(self, node: DataSourceNode, now: float) -> bool:
        """Has ``node`` been continuously healthy for the whole promotion window?

        ``healthy_since_monotonic`` is cleared on every unhealthy observation, so any
        interruption restarts the window rather than accumulating credit across it.
        """
        if self.recovery_cooling_seconds <= 0:
            return True
        if node.healthy_since_monotonic is None:
            return False
        return (now - node.healthy_since_monotonic) >= self.recovery_cooling_seconds

    def _classify(
        self, previous_id: Optional[str], target: DataSourceNode
    ) -> FailoverEventKind:
        if previous_id is None:
            return FailoverEventKind.INITIAL_SELECTION
        if previous_id == SYNTHETIC_SOURCE_ID:
            return FailoverEventKind.RESTORE
        incumbent = self.data_sources.get(previous_id)
        if incumbent is not None and target.priority < incumbent.priority:
            return FailoverEventKind.RESTORE
        return FailoverEventKind.FAILOVER

    def _record_transition(
        self,
        previous_id: Optional[str],
        new_source_id: str,
        state: EngineState,
        kind: FailoverEventKind,
        reason: str,
    ) -> FailoverEvent:
        self._event_sequence += 1
        stamp = _utc_now()
        event = FailoverEvent(
            # Sequence-first so ids are unique and ordered even when several
            # transitions land in the same second.
            event_id=f"{kind.value}-{self._event_sequence:08d}-{stamp.isoformat()}",
            timestamp=stamp,
            previous_source_id=previous_id,
            new_source_id=new_source_id,
            reason=reason,
            engine_state=state,
            kind=kind,
        )
        if len(self.event_log) == self.event_log.maxlen:
            logger.warning(
                "Event log full (%d entries); oldest event evicted. Persist events "
                "externally if they are needed for audit.",
                self.event_log.maxlen,
            )
        self.event_log.append(event)
        if kind is not FailoverEventKind.INITIAL_SELECTION:
            self.last_failover_utc = stamp
            logger.warning("%s: %s [state=%s]", kind.value, reason, state.value)
        else:
            logger.info("%s: %s [state=%s]", kind.value, reason, state.value)
        return event

    # ------------------------------------------------------------------- tick access

    def fetch_market_data_tick(
        self,
        symbol: str,
        fetch_func: Callable[[str, str], Sequence[float]],
        allow_synthetic: bool = True,
    ) -> MarketDataTick:
        """Fetch a quote from the highest-priority usable source, or from the cache.

        Each registered source is attempted at most once per call, so the walk down the
        hierarchy terminates in a single pass no matter how the vendors fail. A raised
        exception or an invalid quote is charged to that source as an error and the
        engine moves to the next one.

        :param fetch_func: Called as ``fetch_func(symbol, source_id)``, returning
            ``(price, volume)``. It runs **while the engine lock is held**, so it must be
            a fast local read of an already-received tick rather than a synchronous
            network call -- see the module docstring.
        :param allow_synthetic: Set ``False`` where an obsolete price is worse than no
            price -- order placement, mark-to-market, risk limit checks.
        :raises FallbackEngineError: if no live source produced a valid quote and the
            cache is unusable (absent, older than ``max_synthetic_age_seconds``, or
            disallowed by ``allow_synthetic``).
        """
        if not isinstance(symbol, str) or not symbol.strip():
            raise ValueError("symbol must be a non-empty string")
        if not callable(fetch_func):
            raise ValueError("fetch_func must be callable")

        with self._lock:
            attempted: set = set()
            while True:
                active, _ = self._evaluate_locked()
                candidate = active
                if candidate is None or candidate.source_id in attempted:
                    candidate = self._next_untried_healthy(attempted)
                if candidate is None:
                    break
                attempted.add(candidate.source_id)

                try:
                    raw = fetch_func(symbol, candidate.source_id)
                except FallbackEngineError:
                    # The engine's own error type is not a vendor fault; surfacing it as
                    # one would charge an error to a source that never misbehaved.
                    raise
                except Exception as exc:  # noqa: BLE001 - any vendor client may raise
                    self._record_error_locked(
                        candidate.source_id, f"fetch raised {type(exc).__name__}: {exc}"
                    )
                    continue

                try:
                    price, volume = self._validate_quote(raw)
                except ValueError as exc:
                    self._record_error_locked(
                        candidate.source_id, f"rejected quote for {symbol!r}: {exc}"
                    )
                    continue

                now_mono = self._clock()
                observed_utc = _utc_now()
                self.synthetic_cache[symbol] = CachedQuote(
                    symbol=symbol,
                    price=price,
                    volume=volume,
                    source_id=candidate.source_id,
                    observed_at_utc=observed_utc,
                    observed_at_monotonic=now_mono,
                )
                return MarketDataTick(
                    symbol=symbol,
                    price=price,
                    volume=volume,
                    timestamp=observed_utc,
                    source_id=candidate.source_id,
                    is_synthetic=False,
                    age_seconds=0.0,
                )

            return self._serve_synthetic_locked(symbol, allow_synthetic)

    def _next_untried_healthy(self, attempted: set) -> Optional[DataSourceNode]:
        for node in sorted(
            self.data_sources.values(), key=lambda n: (n.priority, n.source_id)
        ):
            if node.source_id not in attempted and node.status == DataSourceStatus.HEALTHY:
                return node
        return None

    def _record_error_locked(self, source_id: str, message: str) -> None:
        node = self.data_sources.get(source_id)
        if node is None:
            return
        node.error_count += 1
        logger.warning(
            "Data source error [%s] (%d/%d): %s",
            node.name,
            node.error_count,
            node.max_error_threshold,
            message,
        )
        if node.error_count >= node.max_error_threshold:
            if node.status != DataSourceStatus.ERROR:
                logger.error(
                    "Data source [%s] degraded to ERROR: error budget exhausted "
                    "(%d >= %d)",
                    node.name,
                    node.error_count,
                    node.max_error_threshold,
                )
            node.status = DataSourceStatus.ERROR

    def _validate_quote(self, raw: object) -> Tuple[float, float]:
        """Unpack and validate a vendor's ``(price, volume)`` return value.

        A ``NaN`` price compares ``False`` against every threshold downstream and turns
        every derived quantity -- spread, size, P&L -- into ``NaN`` without raising, so
        it is rejected here rather than propagated or cached.
        """
        if isinstance(raw, (str, bytes, bytearray, dict, set, frozenset)):
            # These all unpack, and all unpack into something that is not a quote: a
            # 2-character string yields two characters, a dict yields its keys, and a
            # set yields its members in an order nobody controls.
            raise ValueError(
                f"fetch_func must return a (price, volume) pair, got {type(raw).__name__}"
            )
        try:
            raw_price, raw_volume = raw
        except (TypeError, ValueError):
            # Deliberately permissive about the container -- tuple, list and numpy array
            # are all normal returns from a vendor adapter -- and strict about the shape.
            raise ValueError(
                "fetch_func must return exactly 2 values (price, volume), got "
                f"{type(raw).__name__}"
            ) from None
        price = _require_finite(raw_price, "price")
        volume = _require_finite(raw_volume, "volume")
        if price <= 0 and not self.allow_non_positive_prices:
            raise ValueError(
                f"price {price} is not positive; set allow_non_positive_prices=True "
                "only for instruments that can legitimately print at or below zero"
            )
        if volume < 0:
            raise ValueError(f"volume {volume} is negative")
        return price, volume

    def _serve_synthetic_locked(
        self, symbol: str, allow_synthetic: bool
    ) -> MarketDataTick:
        cached = self.synthetic_cache.get(symbol)
        if cached is None:
            raise FallbackEngineError(
                f"Complete data outage for {symbol!r}: no live source produced a valid "
                "quote and nothing is cached."
            )
        age = self._clock() - cached.observed_at_monotonic
        if not allow_synthetic:
            raise FallbackEngineError(
                f"No live source produced a valid quote for {symbol!r}; a "
                f"{age:.1f}s-old cached price is available but allow_synthetic=False."
            )
        if age > self.max_synthetic_age_seconds:
            raise FallbackEngineError(
                f"Cached quote for {symbol!r} is {age:.1f}s old, beyond "
                f"max_synthetic_age_seconds={self.max_synthetic_age_seconds:.1f}. "
                "Refusing to serve an obsolete price."
            )
        logger.warning(
            "Serving SYNTHETIC quote for %s: price=%.6f observed %.1fs ago from %s",
            symbol,
            cached.price,
            age,
            cached.source_id,
        )
        return MarketDataTick(
            symbol=symbol,
            price=cached.price,
            # Not the cached volume: volume is a flow measured over an interval, and
            # replaying an old figure would let it be double-counted. 0.0 here means
            # "no volume information", which is why is_synthetic must be checked.
            volume=0.0,
            timestamp=cached.observed_at_utc,
            source_id=SYNTHETIC_SOURCE_ID,
            is_synthetic=True,
            age_seconds=age,
        )

    # ------------------------------------------------------------------------ helpers

    def _require_node(self, source_id: str) -> DataSourceNode:
        node = self.data_sources.get(source_id)
        if node is None:
            raise FallbackEngineError(f"Data source {source_id!r} is not registered.")
        return node

    def health_snapshot(self) -> Dict[str, object]:
        """Current routing decision and per-source health, for logging or a dashboard."""
        with self._lock:
            now = self._clock()
            return {
                "engine_state": self.engine_state.value,
                "active_source_id": self.active_source_id,
                "last_failover_utc": (
                    self.last_failover_utc.isoformat() if self.last_failover_utc else None
                ),
                "sources": [
                    {
                        "source_id": n.source_id,
                        "name": n.name,
                        "priority": n.priority,
                        "status": n.status.value,
                        "error_count": n.error_count,
                        "staleness_seconds": (
                            None
                            if n.last_heartbeat_monotonic is None
                            else round(now - n.last_heartbeat_monotonic, 3)
                        ),
                        "healthy_for_seconds": (
                            None
                            if n.healthy_since_monotonic is None
                            else round(now - n.healthy_since_monotonic, 3)
                        ),
                    }
                    for n in sorted(
                        self.data_sources.values(),
                        key=lambda n: (n.priority, n.source_id),
                    )
                ],
                "cached_symbols": len(self.synthetic_cache),
            }
