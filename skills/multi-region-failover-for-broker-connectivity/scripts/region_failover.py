"""
multi-region-failover-for-broker-connectivity: decide when to move broker
connectivity from one region/network path to another, and refuse to move it
when the move would be unsafe.

This module is a *decision engine*. It opens no sockets, resolves no DNS, and
sends no orders. The caller supplies a health probe and performs the actual
switch; the engine decides whether a switch is warranted, whether a target is
trustworthy, and whether the outgoing path has been made safe first.

Three properties distinguish this from a generic circuit breaker:

  * A never-probed endpoint is not a healthy endpoint. Failover targets must
    have a *recent successful* probe, not merely an absence of failures.
  * A probe that raises is a failed probe. Connection refused, DNS failure and
    read timeout all surface as exceptions, and they are the common case.
  * Switching the network path does not resolve orders that were in flight when
    the path died. The engine reports the switch; reconciliation is the
    caller's, and it must happen before flow resumes.
"""
from dataclasses import dataclass, field
import logging
import threading
import time
from enum import Enum
from typing import Callable, Dict, List, Optional

logger = logging.getLogger(__name__)

__all__ = [
    "EndpointState",
    "FailoverOutcome",
    "SwitchKind",
    "BrokerEndpoint",
    "FailoverEvent",
    "FailoverDecision",
    "RegionFailoverManager",
]


class EndpointState(Enum):
    """Health state of a single broker endpoint.

    ``UNKNOWN`` is the state of a freshly registered endpoint. It is deliberately
    distinct from ``HEALTHY``: an endpoint nobody has probed has not been shown
    to work, and failing over to one is failing over blind.
    """

    UNKNOWN = "UNKNOWN"
    HEALTHY = "HEALTHY"
    DEGRADED = "DEGRADED"
    DOWN = "DOWN"


class SwitchKind(Enum):
    """Why connectivity moved."""

    FAILOVER = "FAILOVER"   # involuntary: the active path is down
    FAILBACK = "FAILBACK"   # voluntary: returning to the preferred path


class FailoverOutcome(Enum):
    """Result of an evaluation.

    Read this, never a bare ``None``. ``NO_ACTION`` ("the active path is fine")
    and ``NO_TARGET_AVAILABLE`` ("the active path is dead and there is nowhere
    to go") are the same non-event to a caller that only checks for an event,
    and they call for opposite responses: keep trading, versus halt.
    """

    NO_ACTION = "NO_ACTION"
    SWITCHED = "SWITCHED"
    FENCE_REQUIRED = "FENCE_REQUIRED"
    NO_TARGET_AVAILABLE = "NO_TARGET_AVAILABLE"
    COOLDOWN_ACTIVE = "COOLDOWN_ACTIVE"
    NOT_STABLE_YET = "NOT_STABLE_YET"
    FLAP_SUPPRESSED = "FLAP_SUPPRESSED"


@dataclass
class BrokerEndpoint:
    """One broker connectivity path (a region, a base URL, a network route)."""

    name: str
    region: str
    url: str
    is_primary: bool
    priority: int = 100
    state: EndpointState = EndpointState.UNKNOWN
    consecutive_failures: int = 0
    consecutive_successes: int = 0
    #: Wall-clock time of the last probe, for audit trails and log correlation.
    last_check_time: float = 0.0
    #: Monotonic time of the last probe. All age/interval arithmetic uses this.
    last_check_monotonic: Optional[float] = None
    last_probe_error: Optional[str] = None


@dataclass
class FailoverEvent:
    """An executed connectivity switch, appended to the audit history."""

    timestamp: float
    from_endpoint: str
    to_endpoint: str
    reason: str
    kind: SwitchKind = SwitchKind.FAILOVER
    from_state: EndpointState = EndpointState.UNKNOWN


@dataclass
class FailoverDecision:
    """The engine's answer, including the cases where it declines to switch."""

    outcome: FailoverOutcome
    reason: str
    active_endpoint: Optional[str]
    event: Optional[FailoverEvent] = None
    #: Non-fatal observations the caller should surface (e.g. a stalled probe loop).
    notes: List[str] = field(default_factory=list)

    @property
    def switched(self) -> bool:
        """True only when connectivity actually moved."""
        return self.outcome is FailoverOutcome.SWITCHED

    @property
    def requires_trading_halt(self) -> bool:
        """True when the active path is down and no eligible target exists.

        This is the only outcome that means "stop sending orders". It is not a
        general success flag: a ``FENCE_REQUIRED`` decision means the caller has
        work to do, not that trading must stop.
        """
        return self.outcome is FailoverOutcome.NO_TARGET_AVAILABLE


class RegionFailoverManager:
    """Health-probed failover between broker connectivity paths.

    Args:
        health_check_fn: Required. Called with a :class:`BrokerEndpoint`, returns
            ``True`` if the endpoint is usable. Exceptions are caught and counted
            as failures - a probe that raises is a probe that failed. There is
            deliberately no default: a manager that assumed health would report a
            permanently healthy system and never fail over.
        failure_threshold: Consecutive failed probes before an endpoint is DOWN.
        cooldown_seconds: Minimum time after a switch before a *failback* is
            allowed. Does not gate failover - see :meth:`evaluate_failover`.
        max_health_age_seconds: A probe result older than this no longer
            qualifies an endpoint as a failover target. Must be comfortably
            larger than your probe interval; the engine cannot see your
            scheduler, so it cannot check that for you.
        failback_success_threshold: Consecutive successful probes the primary
            must accumulate before failback. Cooldown alone measures elapsed
            time, not recovery.
        require_fence: When True (default), a switch is withheld until the caller
            confirms the outgoing path can no longer submit orders. Set False
            only where the broker enforces session exclusivity itself, so that
            connecting from the new region provably disconnects the old one.
        max_failbacks_per_window: Rate limit on *voluntary* switches within
            ``failback_window_seconds``. Failover is never rate limited:
            refusing a failover leaves order flow pinned to a dead path.
        failback_window_seconds: Rolling window for the failback rate limit.
    """

    def __init__(
        self,
        health_check_fn: Callable[[BrokerEndpoint], bool],
        failure_threshold: int = 3,
        cooldown_seconds: float = 60.0,
        max_health_age_seconds: float = 30.0,
        failback_success_threshold: int = 3,
        require_fence: bool = True,
        max_failbacks_per_window: int = 3,
        failback_window_seconds: float = 3600.0,
    ) -> None:
        if not callable(health_check_fn):
            raise TypeError(
                "health_check_fn is required and must be callable; a failover "
                "manager without a health probe never fails over."
            )
        if failure_threshold < 1:
            raise ValueError(f"failure_threshold must be >= 1, got {failure_threshold}")
        if cooldown_seconds < 0:
            raise ValueError(f"cooldown_seconds must be >= 0, got {cooldown_seconds}")
        if max_health_age_seconds <= 0:
            raise ValueError(
                f"max_health_age_seconds must be > 0, got {max_health_age_seconds}"
            )
        if failback_success_threshold < 1:
            raise ValueError(
                f"failback_success_threshold must be >= 1, "
                f"got {failback_success_threshold}"
            )
        if max_failbacks_per_window < 1:
            raise ValueError(
                f"max_failbacks_per_window must be >= 1, got {max_failbacks_per_window}"
            )
        if failback_window_seconds <= 0:
            raise ValueError(
                f"failback_window_seconds must be > 0, got {failback_window_seconds}"
            )

        self.health_check_fn = health_check_fn
        self.failure_threshold = failure_threshold
        self.cooldown_seconds = cooldown_seconds
        self.max_health_age_seconds = max_health_age_seconds
        self.failback_success_threshold = failback_success_threshold
        self.require_fence = require_fence
        self.max_failbacks_per_window = max_failbacks_per_window
        self.failback_window_seconds = failback_window_seconds

        self.endpoints: Dict[str, BrokerEndpoint] = {}
        self.active_endpoint: Optional[str] = None
        self.failover_history: List[FailoverEvent] = []

        # Interval arithmetic uses the monotonic clock throughout. time.time()
        # is steppable by NTP; a backward step would freeze the cooldown.
        self._last_switch_monotonic: Optional[float] = None
        self._failback_monotonic_times: List[float] = []
        self._lock = threading.RLock()

    # ------------------------------------------------------------------
    # Registration
    # ------------------------------------------------------------------

    def register_endpoint(
        self,
        name: str,
        region: str,
        url: str,
        is_primary: bool = False,
        priority: Optional[int] = None,
    ) -> BrokerEndpoint:
        """Register a broker endpoint.

        ``priority`` orders failover candidates deterministically, lowest first.
        It defaults to 0 for the primary and 100 for backups; give each backup an
        explicit value when their order matters (it usually does - the nearest
        region is not the same trade-off as the cheapest one).

        Raises:
            ValueError: on a blank field, a duplicate name, or a second primary.
        """
        name = (name or "").strip()
        region = (region or "").strip()
        url = (url or "").strip()
        if not name:
            raise ValueError("endpoint name must be a non-empty string")
        if not region:
            raise ValueError(f"endpoint '{name}': region must be a non-empty string")
        if not url:
            raise ValueError(f"endpoint '{name}': url must be a non-empty string")

        with self._lock:
            if name in self.endpoints:
                # Silently replacing would discard the live health state of an
                # endpoint that may currently be carrying order flow.
                raise ValueError(f"endpoint '{name}' is already registered")
            if is_primary:
                existing = self._primary_name_locked()
                if existing is not None:
                    raise ValueError(
                        f"endpoint '{name}': primary already registered as '{existing}'"
                    )

            endpoint = BrokerEndpoint(
                name=name,
                region=region,
                url=url,
                is_primary=is_primary,
                priority=priority if priority is not None else (0 if is_primary else 100),
            )
            self.endpoints[name] = endpoint
            if is_primary and self.active_endpoint is None:
                self.active_endpoint = name
            return endpoint

    def validate_configuration(self) -> None:
        """Startup gate. Call once before the probe loop starts.

        A configuration with no primary, or with nowhere to fail over to, does
        not error at runtime - it silently never acts, which looks exactly like
        a healthy system. Fail at startup instead.

        Raises:
            ValueError: if there is no primary, or fewer than two endpoints.
        """
        with self._lock:
            if self._primary_name_locked() is None:
                raise ValueError(
                    "no primary endpoint registered; "
                    "call register_endpoint(..., is_primary=True)"
                )
            if len(self.endpoints) < 2:
                raise ValueError(
                    "at least two endpoints are required for failover; "
                    f"only {len(self.endpoints)} registered"
                )

    # ------------------------------------------------------------------
    # Health probing
    # ------------------------------------------------------------------

    def probe_health(self, endpoint_name: str) -> EndpointState:
        """Probe one endpoint and update its state.

        An exception from ``health_check_fn`` counts as a failure. Connection
        refused, DNS failure and read timeout all arrive as exceptions, and an
        exception that escaped this method would leave the failure counter
        untouched - the endpoint would die without ever being marked DOWN.
        ``BaseException`` (KeyboardInterrupt, SystemExit) still propagates.

        The probe itself runs *outside* the lock, so a blocking network call does
        not stall the order path's reads. The counters are updated under it. Probe
        any one endpoint from a single thread: two concurrent probes of the same
        endpoint can apply their results in either order.
        """
        with self._lock:
            if endpoint_name not in self.endpoints:
                raise ValueError(f"Unknown endpoint: '{endpoint_name}'")
            endpoint = self.endpoints[endpoint_name]

        probe_error: Optional[str] = None
        try:
            healthy = bool(self.health_check_fn(endpoint))
        except Exception as exc:  # a probe that raises is a probe that failed
            healthy = False
            probe_error = f"{type(exc).__name__}: {exc}"
            logger.warning(
                "Health probe raised for endpoint '%s' (%s): %s",
                endpoint_name, endpoint.region, probe_error,
            )

        with self._lock:
            endpoint.last_check_time = time.time()
            endpoint.last_check_monotonic = time.monotonic()
            endpoint.last_probe_error = probe_error

            if healthy:
                endpoint.consecutive_failures = 0
                endpoint.consecutive_successes += 1
                endpoint.state = EndpointState.HEALTHY
            else:
                endpoint.consecutive_successes = 0
                endpoint.consecutive_failures += 1
                endpoint.state = (
                    EndpointState.DOWN
                    if endpoint.consecutive_failures >= self.failure_threshold
                    else EndpointState.DEGRADED
                )
                logger.warning(
                    "Endpoint '%s' probe failed (%d/%d consecutive) -> %s",
                    endpoint_name, endpoint.consecutive_failures,
                    self.failure_threshold, endpoint.state.value,
                )
            return endpoint.state

    def health_age_seconds(self, endpoint_name: str) -> Optional[float]:
        """Seconds since the endpoint was last probed, or None if never probed."""
        with self._lock:
            if endpoint_name not in self.endpoints:
                raise ValueError(f"Unknown endpoint: '{endpoint_name}'")
            last = self.endpoints[endpoint_name].last_check_monotonic
            return None if last is None else max(0.0, time.monotonic() - last)

    def is_health_fresh(self, endpoint_name: str) -> bool:
        """True if this endpoint has a probe result inside ``max_health_age_seconds``."""
        age = self.health_age_seconds(endpoint_name)
        return age is not None and age <= self.max_health_age_seconds

    def eligible_targets(self) -> List[BrokerEndpoint]:
        """Endpoints that could receive flow right now, best candidate first.

        Eligibility is *fresh* HEALTHY, not "not known to be broken". A
        registered-but-never-probed endpoint is UNKNOWN and never eligible.
        """
        with self._lock:
            now = time.monotonic()
            candidates = [
                ep for name, ep in self.endpoints.items()
                if name != self.active_endpoint
                and ep.state is EndpointState.HEALTHY
                and ep.last_check_monotonic is not None
                and (now - ep.last_check_monotonic) <= self.max_health_age_seconds
            ]
            return sorted(candidates, key=lambda ep: (ep.priority, ep.name))

    # ------------------------------------------------------------------
    # Decisions
    # ------------------------------------------------------------------

    def evaluate_failover(self, fence_confirmed: bool = False) -> FailoverDecision:
        """Decide whether to move flow off a failing active endpoint.

        Failover is *involuntary*: it is not gated on cooldown and is never
        suppressed by the flap limiter, because declining to move leaves order
        flow pinned to a path that is already down.

        Args:
            fence_confirmed: Evidence that the outgoing endpoint can no longer
                submit orders. Required whenever ``require_fence`` is set - a
                DOWN health probe means *this monitor* cannot reach the endpoint,
                which is not the same as the endpoint being unable to trade.

        Returns:
            A :class:`FailoverDecision`. Read ``outcome``; check
            ``requires_trading_halt`` before continuing to send orders.
        """
        with self._lock:
            active = self._require_active_locked()
            notes = self._staleness_notes_locked(active)

            if active.state is not EndpointState.DOWN:
                return FailoverDecision(
                    outcome=FailoverOutcome.NO_ACTION,
                    reason=f"active endpoint '{active.name}' is {active.state.value}",
                    active_endpoint=active.name,
                    notes=notes,
                )

            targets = self.eligible_targets()
            if not targets:
                logger.error(
                    "Active endpoint '%s' is DOWN and no endpoint has a fresh "
                    "healthy probe. HALT ORDER FLOW.", active.name,
                )
                return FailoverDecision(
                    outcome=FailoverOutcome.NO_TARGET_AVAILABLE,
                    reason=(
                        f"active endpoint '{active.name}' is DOWN and no endpoint "
                        f"has a successful probe within {self.max_health_age_seconds}s"
                    ),
                    active_endpoint=active.name,
                    notes=notes,
                )

            target = targets[0]
            if self.require_fence and not fence_confirmed:
                return FailoverDecision(
                    outcome=FailoverOutcome.FENCE_REQUIRED,
                    reason=(
                        f"'{target.name}' is ready, but '{active.name}' must be "
                        f"evidenced unable to submit orders before flow moves"
                    ),
                    active_endpoint=active.name,
                    notes=notes,
                )

            event = self._execute_switch_locked(
                active, target.name, "active endpoint DOWN", SwitchKind.FAILOVER,
            )
            return FailoverDecision(
                outcome=FailoverOutcome.SWITCHED,
                reason=f"failed over to '{target.name}' ({target.region})",
                active_endpoint=target.name,
                event=event,
                notes=notes,
            )

    def evaluate_failback(self, fence_confirmed: bool = False) -> FailoverDecision:
        """Decide whether to return flow to the primary endpoint.

        Failback is *voluntary*: a second unscheduled switch, at a moment nobody
        chose, back onto infrastructure that just failed. It is therefore gated
        three ways - cooldown elapsed, the primary stable across
        ``failback_success_threshold`` consecutive probes, and the flap limiter
        not tripped. Elapsed time alone is not recovery evidence.

        When the *active* endpoint is itself DOWN this is not the method to
        call: that is a failover, and :meth:`evaluate_failover` will select the
        primary if it is the best eligible target.
        """
        with self._lock:
            active = self._require_active_locked()
            notes = self._staleness_notes_locked(active)

            primary_name = self._primary_name_locked()
            if primary_name is None:
                return FailoverDecision(
                    outcome=FailoverOutcome.NO_ACTION,
                    reason="no primary endpoint registered",
                    active_endpoint=active.name,
                    notes=notes,
                )
            if primary_name == active.name:
                return FailoverDecision(
                    outcome=FailoverOutcome.NO_ACTION,
                    reason=f"already on the primary endpoint '{primary_name}'",
                    active_endpoint=active.name,
                    notes=notes,
                )

            primary = self.endpoints[primary_name]
            if (primary.state is not EndpointState.HEALTHY
                    or not self.is_health_fresh(primary_name)):
                return FailoverDecision(
                    outcome=FailoverOutcome.NO_ACTION,
                    reason=(
                        f"primary '{primary_name}' is {primary.state.value} "
                        f"with no fresh successful probe"
                    ),
                    active_endpoint=active.name,
                    notes=notes,
                )

            elapsed = self._seconds_since_last_switch_locked()
            if elapsed is not None and elapsed < self.cooldown_seconds:
                return FailoverDecision(
                    outcome=FailoverOutcome.COOLDOWN_ACTIVE,
                    reason=(
                        f"cooldown: {elapsed:.1f}s of {self.cooldown_seconds:.1f}s "
                        f"elapsed since the last switch"
                    ),
                    active_endpoint=active.name,
                    notes=notes,
                )

            if primary.consecutive_successes < self.failback_success_threshold:
                return FailoverDecision(
                    outcome=FailoverOutcome.NOT_STABLE_YET,
                    reason=(
                        f"primary '{primary_name}' has "
                        f"{primary.consecutive_successes} consecutive successful "
                        f"probes, needs {self.failback_success_threshold}"
                    ),
                    active_endpoint=active.name,
                    notes=notes,
                )

            if not self._failback_budget_available_locked():
                logger.error(
                    "Failback to '%s' suppressed: %d failbacks already in the "
                    "last %.0fs. The primary is flapping; escalate to an operator.",
                    primary_name, self.max_failbacks_per_window,
                    self.failback_window_seconds,
                )
                return FailoverDecision(
                    outcome=FailoverOutcome.FLAP_SUPPRESSED,
                    reason=(
                        f"{self.max_failbacks_per_window} failbacks within "
                        f"{self.failback_window_seconds:.0f}s; primary is flapping"
                    ),
                    active_endpoint=active.name,
                    notes=notes,
                )

            if self.require_fence and not fence_confirmed:
                return FailoverDecision(
                    outcome=FailoverOutcome.FENCE_REQUIRED,
                    reason=(
                        f"primary '{primary_name}' has recovered, but "
                        f"'{active.name}' must be evidenced unable to submit "
                        f"orders before flow moves"
                    ),
                    active_endpoint=active.name,
                    notes=notes,
                )

            event = self._execute_switch_locked(
                active, primary_name, "primary recovered - failback", SwitchKind.FAILBACK,
            )
            self._failback_monotonic_times.append(time.monotonic())
            return FailoverDecision(
                outcome=FailoverOutcome.SWITCHED,
                reason=f"failed back to primary '{primary_name}' ({primary.region})",
                active_endpoint=primary_name,
                event=event,
                notes=notes,
            )

    def get_active_endpoint(self) -> Optional[BrokerEndpoint]:
        """Return the endpoint currently carrying flow, or None if unconfigured."""
        with self._lock:
            if self.active_endpoint is None:
                return None
            return self.endpoints[self.active_endpoint]

    # ------------------------------------------------------------------
    # Internals - every caller below already holds self._lock
    # ------------------------------------------------------------------

    def _primary_name_locked(self) -> Optional[str]:
        for name, endpoint in self.endpoints.items():
            if endpoint.is_primary:
                return name
        return None

    def _require_active_locked(self) -> BrokerEndpoint:
        if self.active_endpoint is None:
            raise RuntimeError(
                "no active endpoint; register a primary and call "
                "validate_configuration() before evaluating failover"
            )
        return self.endpoints[self.active_endpoint]

    def _staleness_notes_locked(self, active: BrokerEndpoint) -> List[str]:
        """Warn when the active endpoint's health is stale.

        A stalled probe loop leaves the last known state frozen at HEALTHY. That
        is indistinguishable from health to anything reading ``state`` alone, and
        it is why the engine never *auto-fails-over* on staleness: the probe loop
        dying is not evidence that the endpoint died.
        """
        if active.last_check_monotonic is None:
            return [f"active endpoint '{active.name}' has never been probed"]
        age = time.monotonic() - active.last_check_monotonic
        if age > self.max_health_age_seconds:
            return [
                f"active endpoint '{active.name}' health is {age:.1f}s old "
                f"(limit {self.max_health_age_seconds:.1f}s); "
                f"the probe loop may have stalled"
            ]
        return []

    def _seconds_since_last_switch_locked(self) -> Optional[float]:
        if self._last_switch_monotonic is None:
            return None
        return max(0.0, time.monotonic() - self._last_switch_monotonic)

    def _failback_budget_available_locked(self) -> bool:
        now = time.monotonic()
        self._failback_monotonic_times = [
            t for t in self._failback_monotonic_times
            if (now - t) <= self.failback_window_seconds
        ]
        return len(self._failback_monotonic_times) < self.max_failbacks_per_window

    def _execute_switch_locked(
        self,
        from_endpoint: BrokerEndpoint,
        to_name: str,
        reason: str,
        kind: SwitchKind,
    ) -> FailoverEvent:
        event = FailoverEvent(
            timestamp=time.time(),
            from_endpoint=from_endpoint.name,
            to_endpoint=to_name,
            reason=reason,
            kind=kind,
            from_state=from_endpoint.state,
        )
        self.active_endpoint = to_name
        self._last_switch_monotonic = time.monotonic()
        self.failover_history.append(event)
        logger.warning(
            "%s: %s -> %s (reason: %s). Orders in flight at '%s' are NOT resolved "
            "by this switch; reconcile before resuming flow.",
            kind.value, from_endpoint.name, to_name, reason, from_endpoint.name,
        )
        return event
