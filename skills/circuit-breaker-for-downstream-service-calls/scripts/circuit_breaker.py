"""
circuit-breaker-for-downstream-service-calls:
A thread-safe software circuit breaker for calls a trading system makes to
*downstream* services -- reference-data APIs, historical-data vendors,
sentiment/alt-data endpoints, internal microservices -- where failing fast is
better than blocking a trading thread on a service that is already sick.

The breaker owns exactly one thing: **whether this call is allowed to be
attempted right now, and what its outcome means for the next call**. It does not
retry, does not cache, does not provide a fallback value, and does not cancel or
interrupt a call that is already in flight. Those belong to the caller.

Three states, following the canonical pattern (Nygard, "Release It!"; Microsoft
Azure Architecture Center, "Circuit Breaker pattern"):

  - ``CLOSED``    : calls pass through. Consecutive failures are counted; a
                    success resets the count. Reaching ``failure_threshold``
                    opens the circuit.
  - ``OPEN``      : calls are rejected immediately with
                    ``CircuitBreakerOpenException``. No network I/O is attempted.
  - ``HALF_OPEN`` : after the recovery timeout, at most ``half_open_max_calls``
                    probe calls are admitted concurrently.
                    ``half_open_success_threshold`` consecutive probe successes
                    close the circuit; a single probe failure re-opens it and
                    escalates the backoff.

Seven properties distinguish this from the textbook sketch, and each exists
because the sketch misbehaves in a live trading process:

  1. **It is thread-safe, and it does not serialise traffic.** All state
     transitions happen under a lock; the wrapped call is *never* invoked while
     the lock is held. Without the lock, every thread that arrives during
     HALF_OPEN becomes a probe and the recovering service is flooded by exactly
     the herd the pattern exists to prevent.
  2. **It uses a monotonic clock.** ``time.time()`` is a wall clock: an NTP step
     or an operator correcting the host clock can make the recovery timeout
     appear to have elapsed instantly, or never. ``time.monotonic()`` cannot go
     backwards (Python ``time`` module documentation).
  3. **The closed-state failure count is consecutive, and optionally ages out.**
     A counter that only ever increments trips on three unrelated failures
     scattered across a session -- that is not a broken dependency, that is
     normal life. ``failure_window_sec`` additionally discards failure evidence
     older than the window.
  4. **The recovery timeout escalates.** A flapping dependency otherwise gets
     probed every ``recovery_timeout_sec`` forever. Each re-open multiplies the
     wait by ``backoff_multiplier`` up to ``max_recovery_timeout_sec``, and
     ``jitter_ratio`` de-synchronises a fleet of processes that would otherwise
     all probe the same instant.
  5. **Slow calls can count as failures.** A dependency that answers correctly
     in 30 seconds is, for a trading loop, down. ``slow_call_duration_sec``
     turns latency into a failure signal; without it a degraded-but-not-erroring
     service never trips the breaker.
  6. **Results from a superseded circuit generation are ignored.** A probe that
     returns after the circuit has already moved on must not close a circuit
     another thread has just re-opened.
  7. **``CircuitBreakerOpenException`` is never counted as a failure**, whatever
     ``expected_exceptions`` says. Otherwise a breaker wrapping a breaker
     cascades: the inner circuit opening trips the outer one.

What this is not:

  - **Not a timeout.** The breaker cannot interrupt a call already in flight.
    If the downstream client has no timeout, the first ``failure_threshold``
    threads still block indefinitely and the breaker never trips, because it is
    never told anything failed. Set a client-side connect/read timeout -- the
    breaker is what stops you *making* the call, not what stops it hanging.
    (Azure Architecture Center lists this under "Inappropriate time-outs on
    external services".)
  - **Not for the order path.** Fast-failing a *cancel* or a kill-switch
    instruction is strictly worse than failing slowly: it converts a slow
    dependency into an uncancelled live order. See ``references/standards.md``.
  - **Not a retry policy.** Retry and circuit breaking compose, but the retry
    loop must treat ``CircuitBreakerOpenException`` as terminal, not as another
    attempt to burn.
  - **Not shared across processes.** Each process holds its own state. A fleet
    of N processes will send up to N probes per recovery window, not one.
  - **Not per-shard aware.** One breaker per *independently failing* resource.
    A single breaker over several venues or shards blocks healthy ones because
    an unhealthy one failed.
"""
from __future__ import annotations

import functools
import logging
import math
import random
import threading
import time
from contextlib import contextmanager
from dataclasses import dataclass
from enum import Enum
from typing import Any, Callable, Iterator, List, Optional, Tuple, Type

logger = logging.getLogger(__name__)

__all__ = [
    "CircuitState",
    "CircuitBreakerOpenException",
    "CircuitBreakerSnapshot",
    "CircuitBreaker",
]


class CircuitState(Enum):
    CLOSED = "CLOSED"
    OPEN = "OPEN"
    HALF_OPEN = "HALF_OPEN"


class CircuitBreakerOpenException(Exception):
    """
    Raised instead of attempting the call, because the circuit is OPEN (or is
    HALF_OPEN and the probe allowance is already taken).

    ``retry_after_sec`` is the caller's best estimate of how long until a probe
    will be admitted. It is an estimate, not a promise: another thread may take
    the probe slot first, and a failed probe pushes the time out further.
    """

    def __init__(self, name: str, state: "CircuitState", retry_after_sec: float) -> None:
        super().__init__(
            f"Circuit '{name}' is {state.value}; failing fast without calling "
            f"downstream. Estimated retry in {retry_after_sec:.3f}s."
        )
        self.name = name
        self.state = state
        self.retry_after_sec = retry_after_sec


@dataclass(frozen=True)
class CircuitBreakerSnapshot:
    """Point-in-time view of a breaker, for metrics gauges and alerting."""

    name: str
    state: CircuitState
    failure_count: int
    half_open_successes: int
    half_open_in_flight: int
    current_recovery_timeout_sec: float
    retry_after_sec: float
    total_calls: int
    total_successes: int
    total_failures: int
    total_slow_calls: int
    total_short_circuits: int


@dataclass(frozen=True)
class _Permit:
    """Permission to attempt one call, tied to the generation that issued it."""

    state: CircuitState
    generation: int
    started_at: float


def _is_finite(value: Any) -> bool:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return False
    return math.isfinite(number)


def _is_positive_finite(value: Any) -> bool:
    return _is_finite(value) and float(value) > 0.0


class CircuitBreaker:
    """
    Thread-safe circuit breaker around a single downstream dependency.

    Args:
        failure_threshold: Consecutive failures in CLOSED that open the circuit.
        recovery_timeout_sec: Base time to stay OPEN before admitting a probe.
        expected_exceptions: Exception types that count as a *failure of the
            dependency*. Everything else propagates without touching the
            circuit. The default ``(TimeoutError, ConnectionError)`` is the
            builtin socket-level pair; note that these do **not** cover the
            ``requests`` library, whose exceptions derive from
            ``RequestException(OSError)`` and not from the builtins. With
            ``requests``, pass ``(requests.Timeout, requests.ConnectionError)``
            -- and not ``requests.RequestException`` or ``OSError``, both of
            which subsume ``requests.HTTPError`` and would let a deterministic
            HTTP 400 open the circuit.
        half_open_max_calls: Probe calls admitted concurrently in HALF_OPEN.
        half_open_success_threshold: Consecutive probe successes needed to close.
        backoff_multiplier: Factor applied to the recovery timeout on each
            re-open from HALF_OPEN. ``1.0`` disables escalation.
        max_recovery_timeout_sec: Ceiling for the escalated timeout.
        jitter_ratio: Fractional jitter applied to each recovery timeout, in
            ``[0, 1)``. Non-zero de-synchronises probes across processes; keep
            it ``0.0`` when you need deterministic behaviour in tests.
        failure_window_sec: If set, closed-state failure evidence older than
            this is discarded before counting a new failure.
        slow_call_duration_sec: If set, a call that *succeeds* but takes longer
            than this is recorded as a failure instead.
        name: Identifier used in logs, metrics and the raised exception.
        on_state_change: Optional callback invoked with a snapshot after every
            state transition, outside the lock. Exceptions from it are logged
            and swallowed -- an alerting bug must not take the trading loop down.
        clock: Monotonic time source in seconds. Injectable for deterministic
            tests; must be monotonic.
        rng: Random source for jitter. Injectable for deterministic tests.

    Raises:
        ValueError: On any invalid configuration. A misconfigured breaker fails
            at construction, not at 3am under load.
    """

    def __init__(
        self,
        failure_threshold: int = 3,
        recovery_timeout_sec: float = 10.0,
        expected_exceptions: Tuple[Type[BaseException], ...] = (TimeoutError, ConnectionError),
        *,
        half_open_max_calls: int = 1,
        half_open_success_threshold: int = 1,
        backoff_multiplier: float = 2.0,
        max_recovery_timeout_sec: float = 300.0,
        jitter_ratio: float = 0.0,
        failure_window_sec: Optional[float] = None,
        slow_call_duration_sec: Optional[float] = None,
        name: str = "downstream",
        on_state_change: Optional[Callable[[CircuitBreakerSnapshot], None]] = None,
        clock: Callable[[], float] = time.monotonic,
        rng: Optional[random.Random] = None,
    ) -> None:
        if isinstance(failure_threshold, bool) or not isinstance(failure_threshold, int):
            raise ValueError("failure_threshold must be an int")
        if failure_threshold < 1:
            raise ValueError("failure_threshold must be >= 1")
        if not _is_positive_finite(recovery_timeout_sec):
            raise ValueError("recovery_timeout_sec must be a positive finite number")
        if not _is_positive_finite(max_recovery_timeout_sec):
            raise ValueError("max_recovery_timeout_sec must be a positive finite number")
        if float(max_recovery_timeout_sec) < float(recovery_timeout_sec):
            raise ValueError("max_recovery_timeout_sec must be >= recovery_timeout_sec")
        if not isinstance(expected_exceptions, tuple) or not expected_exceptions:
            raise ValueError("expected_exceptions must be a non-empty tuple of exception types")
        for exc in expected_exceptions:
            if not (isinstance(exc, type) and issubclass(exc, BaseException)):
                raise ValueError(f"expected_exceptions entry {exc!r} is not an exception type")
        if isinstance(half_open_max_calls, bool) or not isinstance(half_open_max_calls, int):
            raise ValueError("half_open_max_calls must be an int")
        if half_open_max_calls < 1:
            raise ValueError("half_open_max_calls must be >= 1")
        if isinstance(half_open_success_threshold, bool) or not isinstance(
            half_open_success_threshold, int
        ):
            raise ValueError("half_open_success_threshold must be an int")
        if half_open_success_threshold < 1:
            raise ValueError("half_open_success_threshold must be >= 1")
        if not _is_finite(backoff_multiplier) or float(backoff_multiplier) < 1.0:
            raise ValueError("backoff_multiplier must be a finite number >= 1.0")
        if not _is_finite(jitter_ratio) or not 0.0 <= float(jitter_ratio) < 1.0:
            raise ValueError("jitter_ratio must be in [0.0, 1.0)")
        if failure_window_sec is not None and not _is_positive_finite(failure_window_sec):
            raise ValueError("failure_window_sec must be a positive finite number or None")
        if slow_call_duration_sec is not None and not _is_positive_finite(slow_call_duration_sec):
            raise ValueError("slow_call_duration_sec must be a positive finite number or None")
        if not isinstance(name, str) or not name.strip():
            raise ValueError("name must be a non-empty string")
        if not callable(clock):
            raise ValueError("clock must be callable")
        if on_state_change is not None and not callable(on_state_change):
            raise ValueError("on_state_change must be callable or None")

        for exc in expected_exceptions:
            if exc in (Exception, BaseException):
                logger.warning(
                    "CircuitBreaker[%s]: expected_exceptions includes %s. The breaker will "
                    "now trip on deterministic business errors (bad request, insufficient "
                    "funds), which retrying will never fix. Narrow it to infrastructure "
                    "faults.",
                    name,
                    exc.__name__,
                )

        self.name = name
        self.failure_threshold = failure_threshold
        self.recovery_timeout_sec = float(recovery_timeout_sec)
        self.expected_exceptions = expected_exceptions
        self.half_open_max_calls = half_open_max_calls
        self.half_open_success_threshold = half_open_success_threshold
        self.backoff_multiplier = float(backoff_multiplier)
        self.max_recovery_timeout_sec = float(max_recovery_timeout_sec)
        self.jitter_ratio = float(jitter_ratio)
        self.failure_window_sec = (
            None if failure_window_sec is None else float(failure_window_sec)
        )
        self.slow_call_duration_sec = (
            None if slow_call_duration_sec is None else float(slow_call_duration_sec)
        )

        self._on_state_change = on_state_change
        self._clock = clock
        self._rng = rng if rng is not None else random.Random()
        self._lock = threading.RLock()

        self._state = CircuitState.CLOSED
        self._generation = 0
        self._failure_count = 0
        self._last_failure_time: Optional[float] = None
        self._opened_at: Optional[float] = None
        self._backoff_base_sec = self.recovery_timeout_sec
        self._current_recovery_timeout_sec = self.recovery_timeout_sec
        self._half_open_successes = 0
        self._half_open_in_flight = 0

        self._total_calls = 0
        self._total_successes = 0
        self._total_failures = 0
        self._total_slow_calls = 0
        self._total_short_circuits = 0

    # ------------------------------------------------------------------ state

    @property
    def state(self) -> CircuitState:
        """
        Current state. This is a *read*: it does not perform the OPEN ->
        HALF_OPEN transition, which happens when a call actually arrives. A
        breaker whose recovery timeout has elapsed with no traffic still reads
        OPEN, which is the truth -- nothing has been probed yet.
        """
        with self._lock:
            return self._state

    @property
    def failure_count(self) -> int:
        with self._lock:
            return self._failure_count

    def snapshot(self) -> CircuitBreakerSnapshot:
        """Consistent point-in-time view, safe to publish as a metrics gauge."""
        with self._lock:
            return self._snapshot_locked()

    # ------------------------------------------------------------------ calls

    def call(self, func: Callable[..., Any], *args: Any, **kwargs: Any) -> Any:
        """
        Execute ``func(*args, **kwargs)`` through the breaker.

        Raises:
            CircuitBreakerOpenException: The call was not attempted at all.
            Exception: Anything ``func`` raised, propagated unchanged, whether
                or not it counted as a circuit failure.
        """
        if not callable(func):
            raise ValueError("func must be callable")

        permit = self._acquire_permit()
        try:
            result = func(*args, **kwargs)
        except CircuitBreakerOpenException:
            # A *nested* breaker opened. No work happened downstream, so this is
            # not evidence about this dependency and must not trip this circuit.
            self._release_permit(permit)
            raise
        except self.expected_exceptions:
            self._record_failure(permit)
            raise
        except BaseException:
            # Not an expected infrastructure fault: give back the probe slot but
            # leave the failure counters alone.
            self._release_permit(permit)
            raise
        self._record_success(permit)
        return result

    def decorate(self, func: Callable[..., Any]) -> Callable[..., Any]:
        """Wrap ``func`` so that every invocation goes through this breaker."""

        @functools.wraps(func)
        def wrapper(*args: Any, **kwargs: Any) -> Any:
            return self.call(func, *args, **kwargs)

        return wrapper

    @contextmanager
    def guard(self) -> Iterator[None]:
        """
        Context-manager form, for call sites that are not a single callable::

            with breaker.guard():
                response = session.get(url, timeout=2.0)

        Same semantics as :meth:`call`: entering may raise
        ``CircuitBreakerOpenException``, and only ``expected_exceptions``
        escaping the block count as failures.
        """
        permit = self._acquire_permit()
        try:
            yield
        except CircuitBreakerOpenException:
            self._release_permit(permit)
            raise
        except self.expected_exceptions:
            self._record_failure(permit)
            raise
        except BaseException:
            self._release_permit(permit)
            raise
        self._record_success(permit)

    # --------------------------------------------------------- manual control

    def reset(self) -> None:
        """
        Manual override: force the circuit CLOSED and clear all failure state,
        including the escalated backoff. For an operator who knows the
        dependency is healthy again and does not want to wait out the timeout.
        """
        events: List[CircuitBreakerSnapshot] = []
        with self._lock:
            self._close_locked(events)
        self._emit(events)

    def force_open(self) -> None:
        """
        Manual override: force the circuit OPEN for a full, un-escalated
        recovery timeout. For taking a known-bad dependency out of the path
        without a deployment.
        """
        events: List[CircuitBreakerSnapshot] = []
        with self._lock:
            self._open_locked(self._clock(), escalate=False, events=events)
        self._emit(events)

    # -------------------------------------------------------------- internals

    def _acquire_permit(self) -> _Permit:
        events: List[CircuitBreakerSnapshot] = []
        exc: Optional[CircuitBreakerOpenException] = None
        permit: Optional[_Permit] = None

        with self._lock:
            now = self._clock()
            self._maybe_half_open_locked(now, events)

            if self._state is CircuitState.OPEN:
                self._total_short_circuits += 1
                exc = CircuitBreakerOpenException(
                    self.name, CircuitState.OPEN, self._retry_after_locked(now)
                )
            elif (
                self._state is CircuitState.HALF_OPEN
                and self._half_open_in_flight >= self.half_open_max_calls
            ):
                # The probe allowance is already taken by another thread. Keep
                # failing fast rather than joining the herd on a service that
                # has not yet proved it recovered.
                self._total_short_circuits += 1
                exc = CircuitBreakerOpenException(self.name, CircuitState.HALF_OPEN, 0.0)
            else:
                if self._state is CircuitState.HALF_OPEN:
                    self._half_open_in_flight += 1
                self._total_calls += 1
                permit = _Permit(self._state, self._generation, now)

        self._emit(events)
        if exc is not None:
            raise exc
        if permit is None:  # unreachable: exactly one branch above runs
            raise RuntimeError("CircuitBreaker failed to issue or refuse a permit")
        return permit

    def _release_permit(self, permit: _Permit) -> None:
        """Give back a probe slot without recording success or failure."""
        with self._lock:
            if (
                permit.state is CircuitState.HALF_OPEN
                and permit.generation == self._generation
                and self._half_open_in_flight > 0
            ):
                self._half_open_in_flight -= 1

    def _record_success(self, permit: _Permit) -> None:
        events: List[CircuitBreakerSnapshot] = []
        with self._lock:
            now = self._clock()
            elapsed = now - permit.started_at
            if (
                self.slow_call_duration_sec is not None
                and elapsed > self.slow_call_duration_sec
            ):
                self._total_slow_calls += 1
                self._total_failures += 1
                logger.warning(
                    "CircuitBreaker[%s]: call succeeded but took %.3fs (> %.3fs); "
                    "counting it as a failure.",
                    self.name,
                    elapsed,
                    self.slow_call_duration_sec,
                )
                self._record_failure_locked(permit, now, events)
            else:
                self._total_successes += 1
                self._record_success_locked(permit, events)
        self._emit(events)

    def _record_success_locked(
        self, permit: _Permit, events: List[CircuitBreakerSnapshot]
    ) -> None:
        if permit.generation != self._generation:
            # Result of a superseded generation: it counts in the totals but it
            # must not close a circuit that has since been re-opened.
            return
        if permit.state is CircuitState.HALF_OPEN:
            if self._half_open_in_flight > 0:
                self._half_open_in_flight -= 1
            self._half_open_successes += 1
            if self._half_open_successes >= self.half_open_success_threshold:
                self._close_locked(events)
        else:
            self._failure_count = 0
            self._last_failure_time = None

    def _record_failure(self, permit: _Permit) -> None:
        events: List[CircuitBreakerSnapshot] = []
        with self._lock:
            now = self._clock()
            self._total_failures += 1
            self._record_failure_locked(permit, now, events)
        self._emit(events)

    def _record_failure_locked(
        self, permit: _Permit, now: float, events: List[CircuitBreakerSnapshot]
    ) -> None:
        if permit.generation != self._generation:
            return

        if permit.state is CircuitState.HALF_OPEN:
            if self._half_open_in_flight > 0:
                self._half_open_in_flight -= 1
            logger.warning(
                "CircuitBreaker[%s]: probe call failed; re-opening and escalating backoff.",
                self.name,
            )
            self._open_locked(now, escalate=True, events=events)
            return

        if (
            self.failure_window_sec is not None
            and self._last_failure_time is not None
            and now - self._last_failure_time > self.failure_window_sec
        ):
            self._failure_count = 0

        self._failure_count += 1
        self._last_failure_time = now
        if self._failure_count >= self.failure_threshold:
            logger.critical(
                "CircuitBreaker[%s]: tripped to OPEN after %d consecutive failures.",
                self.name,
                self._failure_count,
            )
            self._open_locked(now, escalate=False, events=events)

    def _maybe_half_open_locked(
        self, now: float, events: List[CircuitBreakerSnapshot]
    ) -> None:
        if self._state is not CircuitState.OPEN or self._opened_at is None:
            return
        if now - self._opened_at >= self._current_recovery_timeout_sec:
            self._state = CircuitState.HALF_OPEN
            self._generation += 1
            self._half_open_successes = 0
            self._half_open_in_flight = 0
            logger.info(
                "CircuitBreaker[%s]: recovery timeout elapsed; HALF_OPEN.", self.name
            )
            events.append(self._snapshot_locked())

    def _open_locked(
        self, now: float, escalate: bool, events: List[CircuitBreakerSnapshot]
    ) -> None:
        if escalate:
            self._backoff_base_sec = min(
                self._backoff_base_sec * self.backoff_multiplier,
                self.max_recovery_timeout_sec,
            )
        else:
            self._backoff_base_sec = self.recovery_timeout_sec
        self._current_recovery_timeout_sec = self._apply_jitter(self._backoff_base_sec)
        self._state = CircuitState.OPEN
        self._generation += 1
        self._opened_at = now
        self._half_open_successes = 0
        self._half_open_in_flight = 0
        events.append(self._snapshot_locked())

    def _close_locked(self, events: List[CircuitBreakerSnapshot]) -> None:
        self._state = CircuitState.CLOSED
        self._generation += 1
        self._failure_count = 0
        self._last_failure_time = None
        self._opened_at = None
        self._half_open_successes = 0
        self._half_open_in_flight = 0
        self._backoff_base_sec = self.recovery_timeout_sec
        self._current_recovery_timeout_sec = self.recovery_timeout_sec
        logger.info(
            "CircuitBreaker[%s]: CLOSED; downstream considered healthy.", self.name
        )
        events.append(self._snapshot_locked())

    def _apply_jitter(self, timeout_sec: float) -> float:
        if self.jitter_ratio == 0.0:
            return timeout_sec
        factor = 1.0 + self._rng.uniform(-self.jitter_ratio, self.jitter_ratio)
        return max(0.0, timeout_sec * factor)

    def _retry_after_locked(self, now: float) -> float:
        if self._opened_at is None:
            return 0.0
        return max(0.0, self._opened_at + self._current_recovery_timeout_sec - now)

    def _snapshot_locked(self) -> CircuitBreakerSnapshot:
        return CircuitBreakerSnapshot(
            name=self.name,
            state=self._state,
            failure_count=self._failure_count,
            half_open_successes=self._half_open_successes,
            half_open_in_flight=self._half_open_in_flight,
            current_recovery_timeout_sec=self._current_recovery_timeout_sec,
            retry_after_sec=self._retry_after_locked(self._clock()),
            total_calls=self._total_calls,
            total_successes=self._total_successes,
            total_failures=self._total_failures,
            total_slow_calls=self._total_slow_calls,
            total_short_circuits=self._total_short_circuits,
        )

    def _emit(self, events: List[CircuitBreakerSnapshot]) -> None:
        """Fire state-change callbacks outside the lock."""
        if self._on_state_change is None:
            return
        for snapshot in events:
            try:
                self._on_state_change(snapshot)
            except Exception:
                logger.exception(
                    "CircuitBreaker[%s]: on_state_change callback raised; ignoring.",
                    self.name,
                )
