"""
chaos-engineering-for-trading-infrastructure:
Injects controlled, reproducible faults — added latency, dropped messages, and
simulated process death — at an application-level I/O boundary, so that a trading
system's failover, timeout, and gap-recovery paths are exercised on purpose
instead of being discovered during an outage.

``ChaosInjector.execute()`` wraps a callable that stands in for a network or IPC
boundary (a FIX send, a REST call, a websocket read, a queue publish). It is a
*test harness component*, not a network-layer tool: it does not manipulate real
sockets, ``tc``/``netem`` qdiscs, or containers. Anything it cannot reach —
kernel buffering, TCP retransmission, half-open connections — is out of scope;
see "What this is not".

Five properties distinguish this from ``time.sleep()`` plus ``random.random()``,
and each one exists because the naive version produces experiments that lie:

  1. **Fail-closed activation.** Faults are injected only when the injector has
     been explicitly enabled, in code or through an environment variable that is
     absent by default. A chaos wrapper accidentally left in a code path that
     ships to production is inert and transparent: it calls the wrapped function
     and returns its result. This is the blast-radius control of last resort —
     it does not replace running experiments against an isolated environment.
  2. **The system under test keeps its own randomness.** Seeding is done on
     injector-owned ``random.Random`` instances. The v1 implementation called
     ``random.seed()``, which silently re-seeds the *global* module RNG shared by
     everything else in the process — so a system whose retry backoff, sampling,
     or simulated market data uses ``random`` had its behaviour changed by the
     act of observing it, and every client would jitter in lockstep.
  3. **Each fault channel is an independent stream.** Crash, drop, and jitter
     draw from separately seeded generators. Re-running a failed experiment with
     ``crash_probability`` turned off therefore reproduces the *same* sequence of
     drops, which is the whole point of seeding (``references/standards.md``,
     "Determinism").
  4. **A simulated crash is not ``SystemExit``.** ``SystemExit`` raised inside a
     worker thread is swallowed silently by ``threading`` — no traceback, no
     failed experiment, a green run that tested nothing — and when it does reach
     the interpreter it is indistinguishable in logs from an intentional
     shutdown. ``SimulatedProcessCrash`` derives from ``BaseException``, so it
     still bypasses the ``except Exception`` handlers a real crash would bypass,
     but it is reported, greppable, and attributable.
  5. **The experiment counts what it injected.** ``injector.stats`` records
     calls, drops, crashes and delay actually applied. A 10%-drop experiment over
     20 calls can easily inject zero drops; without the counters, "the system
     survived" and "nothing was ever injected" look identical.

What this is not:

  - **Not a production-safety mechanism.** It creates faults; it does not contain
    them. Position limits, kill switches and the pre-trade risk layer must be
    working *before* an experiment starts — see
    ``kill-switch-and-drawdown-circuit-breakers``.
  - **Not an indefinite hang.** A dropped call raises after the configured
    latency has elapsed; it does not block until the caller's own timeout fires.
    To exercise read-timeout handling, set ``latency_ms`` above the client's
    configured timeout rather than relying on the drop.
  - **Not sub-millisecond accurate.** ``time.sleep()`` resolution is
    OS-dependent (roughly 1-2 ms on Windows, finer on Linux) and it always sleeps
    *at least* the requested interval. Latency targets below ~1 ms are not
    meaningful here; use a purpose-built network emulator.
  - **Not a network partition.** Only calls that pass through ``execute()`` are
    affected. Heartbeats, DNS, and any I/O on another path continue normally,
    which is exactly the asymmetry a real partition would not have.
"""
from __future__ import annotations

import logging
import math
import numbers
import os
import random
import threading
import time
from dataclasses import dataclass
from typing import Any, Callable, Optional, TypeVar

logger = logging.getLogger(__name__)

T = TypeVar("T")

# Environment variable consulted when ``ChaosInjector(enabled=...)`` is left as
# ``None``. Absent or falsey means no faults are injected.
DEFAULT_ENABLE_ENV_VAR = "CHAOS_ENGINEERING_ENABLED"

_TRUTHY_VALUES = frozenset({"1", "true", "yes", "on", "enabled"})


class ChaosConfigError(ValueError):
    """Raised for a chaos configuration the injector refuses to operate with."""


class SimulatedProcessCrash(BaseException):
    """
    Raised in place of a hard process death.

    Derives from ``BaseException`` deliberately: a real ``SIGKILL`` is not
    something an ``except Exception`` block gets to recover from, so neither is
    this. It is *not* ``SystemExit`` — see the module docstring, property 4.
    """


def _validate_non_negative(name: str, value: Any) -> float:
    if isinstance(value, bool) or not isinstance(value, numbers.Real):
        raise ChaosConfigError(f"{name} must be a real number, got {value!r}.")
    numeric = float(value)
    if math.isnan(numeric):
        raise ChaosConfigError(f"{name} must not be NaN.")
    if math.isinf(numeric):
        raise ChaosConfigError(f"{name} must be finite, got {numeric}.")
    if numeric < 0:
        raise ChaosConfigError(f"{name} must be >= 0, got {numeric}.")
    return numeric


def _validate_probability(name: str, value: Any) -> float:
    numeric = _validate_non_negative(name, value)
    if numeric > 1.0:
        raise ChaosConfigError(
            f"{name} is a probability in [0.0, 1.0], got {numeric}. "
            "Percentages are a common mistake: 10% is 0.10, not 10.")
    return numeric


@dataclass(frozen=True)
class ChaosConfig:
    """
    Fault profile for one injection point.

    Args:
        latency_ms: Fixed delay added to every call, in milliseconds.
        jitter_ms: Additional delay drawn uniformly from ``[0, jitter_ms)`` and
            added to ``latency_ms``. One-sided by design, so a 50-200 ms link is
            expressed as ``latency_ms=50, jitter_ms=150``.
        drop_probability: Probability in ``[0.0, 1.0]`` that a call raises
            ``ConnectionAbortedError`` instead of reaching the wrapped callable.
        crash_probability: Probability in ``[0.0, 1.0]`` that a call raises
            ``SimulatedProcessCrash``. Evaluated ahead of the drop, so the
            effective drop rate is ``(1 - crash_probability) * drop_probability``.
        seed: Seeds the injector's own generators for reproducible experiments.
            ``None`` means non-reproducible — acceptable for exploratory runs,
            not for a failure you intend to debug.

    Raises:
        ChaosConfigError: On negative, non-finite, non-numeric, or out-of-range
            values. Silently accepting ``drop_probability=10`` would drop every
            message and make the experiment look like a resilience failure.
    """

    latency_ms: float = 0.0
    jitter_ms: float = 0.0
    drop_probability: float = 0.0
    crash_probability: float = 0.0
    seed: Optional[int] = None

    def __post_init__(self) -> None:
        object.__setattr__(
            self, "latency_ms", _validate_non_negative("latency_ms", self.latency_ms))
        object.__setattr__(
            self, "jitter_ms", _validate_non_negative("jitter_ms", self.jitter_ms))
        object.__setattr__(
            self, "drop_probability",
            _validate_probability("drop_probability", self.drop_probability))
        object.__setattr__(
            self, "crash_probability",
            _validate_probability("crash_probability", self.crash_probability))
        if self.seed is not None and (isinstance(self.seed, bool) or not isinstance(self.seed, int)):
            raise ChaosConfigError(f"seed must be an int or None, got {self.seed!r}.")


@dataclass(frozen=True)
class FaultStats:
    """
    Immutable snapshot of what an injector actually did.

    ``faults_injected == 0`` after an experiment means the experiment proved
    nothing, however green the run looked.
    """

    calls: int = 0
    passthrough_calls: int = 0
    delayed_calls: int = 0
    drops_injected: int = 0
    crashes_injected: int = 0
    total_delay_ms: float = 0.0

    @property
    def faults_injected(self) -> int:
        """Drops plus crashes. Added delay on its own is not counted as a fault."""
        return self.drops_injected + self.crashes_injected


def _env_flag_is_set(env_var: str) -> bool:
    return os.environ.get(env_var, "").strip().lower() in _TRUTHY_VALUES


def _bump(
    stats: FaultStats,
    *,
    calls: int = 0,
    passthrough_calls: int = 0,
    delayed_calls: int = 0,
    drops_injected: int = 0,
    crashes_injected: int = 0,
    total_delay_ms: float = 0.0,
) -> FaultStats:
    """Returns a new FaultStats with the given increments applied."""
    return FaultStats(
        calls=stats.calls + calls,
        passthrough_calls=stats.passthrough_calls + passthrough_calls,
        delayed_calls=stats.delayed_calls + delayed_calls,
        drops_injected=stats.drops_injected + drops_injected,
        crashes_injected=stats.crashes_injected + crashes_injected,
        total_delay_ms=stats.total_delay_ms + total_delay_ms,
    )


class ChaosInjector:
    """
    Wraps calls at a simulated network or IPC boundary and injects the faults
    described by a :class:`ChaosConfig`.

    Activation is fail-closed. With ``enabled=None`` (the default) the injector
    consults ``enable_env_var``; if that variable is absent or falsey the injector
    becomes transparent — ``execute()`` calls the wrapped function and returns its
    result, injecting nothing. Pass ``enabled=True`` for unit tests and explicitly
    isolated harnesses, where the decision is visible in the code itself.

    Thread safety: ``execute()`` may be called concurrently. Random draws and
    counters are taken under a lock; the delay is applied outside it so that
    concurrent callers are genuinely delayed in parallel rather than serialised.
    Note that with several threads sharing one injector the *interleaving* of
    draws is not reproducible even with a seed — use one seeded injector per
    thread if a multi-threaded run has to be replayable.
    """

    def __init__(
        self,
        config: ChaosConfig,
        *,
        enabled: Optional[bool] = None,
        enable_env_var: str = DEFAULT_ENABLE_ENV_VAR,
        name: str = "default",
    ) -> None:
        if not isinstance(config, ChaosConfig):
            raise ChaosConfigError(
                f"config must be a ChaosConfig, got {type(config).__name__}.")

        self.config = config
        self.name = name
        self.enable_env_var = enable_env_var
        # Resolved once, at construction, so activation cannot change halfway
        # through an experiment.
        self.enabled: bool = _env_flag_is_set(enable_env_var) if enabled is None else bool(enabled)

        master = random.Random(config.seed)
        # One stream per fault channel: changing one probability must not shift
        # the sequence another channel produces.
        self._crash_rng = random.Random(master.getrandbits(64))
        self._drop_rng = random.Random(master.getrandbits(64))
        self._jitter_rng = random.Random(master.getrandbits(64))

        self._lock = threading.Lock()
        self._stats = FaultStats()

        if self.enabled:
            logger.warning(
                "ChaosInjector[%s] ENABLED: latency=%.2fms jitter=%.2fms "
                "drop_p=%.4f crash_p=%.4f seed=%s",
                self.name, config.latency_ms, config.jitter_ms,
                config.drop_probability, config.crash_probability, config.seed)
            if config.seed is None and (
                    config.drop_probability or config.crash_probability or config.jitter_ms):
                logger.warning(
                    "ChaosInjector[%s] has no seed; a failing run will not be "
                    "exactly reproducible.", self.name)
        else:
            logger.info(
                "ChaosInjector[%s] disabled (set %s to enable); calls pass through untouched.",
                self.name, self.enable_env_var)

    @property
    def stats(self) -> FaultStats:
        """Snapshot of injected faults. Safe to read while an experiment runs."""
        with self._lock:
            return self._stats

    def execute(self, func: Callable[..., T], *args: Any, **kwargs: Any) -> T:
        """
        Calls ``func(*args, **kwargs)`` through the fault profile.

        Order of events, fixed so that runs are comparable: the crash and drop
        decisions and the jitter draw are taken first (keeping each stream's
        consumption independent of timing), then the delay is applied, then any
        fault is raised. A dropped message therefore costs wall-clock time before
        it fails, which is what lets a client's timeout path be exercised — an
        instantaneous drop only ever tests the error path.

        Raises:
            SimulatedProcessCrash: Simulated hard process death. Deliberately not
                catchable by ``except Exception``.
            ConnectionAbortedError: Simulated message loss / severed connection.
            TypeError: If ``func`` is not callable.
        """
        if not callable(func):
            raise TypeError(f"func must be callable, got {type(func).__name__}.")

        if not self.enabled:
            with self._lock:
                self._stats = _bump(self._stats, calls=1, passthrough_calls=1)
            return func(*args, **kwargs)

        cfg = self.config
        with self._lock:
            crash = self._crash_rng.random() < cfg.crash_probability
            drop = self._drop_rng.random() < cfg.drop_probability
            jitter_ms = self._jitter_rng.random() * cfg.jitter_ms if cfg.jitter_ms > 0 else 0.0
            delay_ms = cfg.latency_ms + jitter_ms
            self._stats = _bump(
                self._stats,
                calls=1,
                delayed_calls=1 if delay_ms > 0 else 0,
                drops_injected=1 if (drop and not crash) else 0,
                crashes_injected=1 if crash else 0,
                total_delay_ms=delay_ms,
            )

        if delay_ms > 0:
            logger.debug("ChaosInjector[%s] delaying call by %.2fms", self.name, delay_ms)
            time.sleep(delay_ms / 1000.0)

        if crash:
            logger.critical("ChaosInjector[%s] CHAOS: simulated process crash.", self.name)
            raise SimulatedProcessCrash(
                f"Simulated process crash injected by ChaosInjector[{self.name}]")

        if drop:
            logger.warning("ChaosInjector[%s] CHAOS: message dropped.", self.name)
            raise ConnectionAbortedError(
                f"Simulated network drop injected by ChaosInjector[{self.name}]")

        return func(*args, **kwargs)


class MockFixClient:
    """
    Stand-in for a FIX order gateway, used by the tests and by the worked example
    in ``SKILL.md``. Deliberately trivial: the point of the example is the
    injector's behaviour, not the client's.
    """

    def send_order(self, order_id: str) -> str:
        """Returns an acknowledgement string for ``order_id``."""
        return f"ACK-{order_id}"
