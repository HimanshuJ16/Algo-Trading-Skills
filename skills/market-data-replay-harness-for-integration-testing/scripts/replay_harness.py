"""
market-data-replay-harness-for-integration-testing: replays a recorded tick session
through a live strategy/OMS callback chain at a controlled speed, and reports how
faithfully it managed to do so.

What this is
------------
A *transport*. It takes ticks you already captured and pushes them into the code
under test in a reproducible order, optionally spaced out in wall-clock time. It is
the thing that lets an integration test exercise the real pipeline instead of a mock.

What this is NOT
----------------
  - Not a market simulator. The recorded book does not react to the orders your
    strategy emits, so a replay cannot show whether the strategy *contributes* to
    disorderly trading. The FCA names benchmark-only testing of this kind as poor
    practice (Algorithmic Trading Compliance in Wholesale Markets, Feb 2018, 6.12).
  - Not conformance testing. RTS 6 Art. 6 requires testing against the trading
    venue's or DEA provider's own system; a recorded file is neither.
  - Not a fill model. Nothing here matches, queues or fills an order.

Timestamp units
---------------
`ReplayTick.timestamp` is in **SECONDS** (epoch or session-relative). Sleep delays
are computed directly from it, so feeding millisecond or nanosecond ticks makes the
harness try to sleep for months. `max_projected_wall_time_sec` guards against that,
and a warning is logged whenever the projected wall time looks implausible.

Timing fidelity
---------------
Dispatch deadlines are absolute (anchored to the session start), not per-tick sleeps,
so callback cost and sleep overshoot do NOT accumulate into drift. They still show up
as *lag* on individual ticks, which is measured and reported rather than hidden:
`time.sleep()` "may be longer than requested by an arbitrary amount, because of the
scheduling of other activity in the system" (CPython docs, `time` module). Measured on
CPython 3.11 / Windows 11, sleeps of 100 us to 5 ms overshot by ~300-900 us regardless
of the requested duration. Treat sub-millisecond tick spacing as unreproducible in
this process and read `max_scheduling_lag_sec` before trusting a timed replay.

Determinism
-----------
In ASAP mode the replay is fully deterministic: same input file, same dispatch order,
same callback sequence. In timed mode the *content and order* are still deterministic
but the wall clock is not, so regression baselines must be compared on callback
outputs, never on timings.
"""
from collections.abc import Sequence as AbcSequence
from dataclasses import dataclass
import logging
import math
import time
from typing import Any, Callable, Dict, List, Optional, Sequence, Tuple

logger = logging.getLogger(__name__)

# Below this, a sleep request costs more than it delivers: the OS scheduler overshoots
# short sleeps by roughly this much anyway. The harness dispatches immediately instead
# and reports the resulting lag rather than pretending it slept.
DEFAULT_MIN_SLEEP_SEC = 0.0005

# A tick dispatched more than this far after its deadline is counted as late.
DEFAULT_LATE_TOLERANCE_SEC = 0.001

# Projected wall time above which a unit mismatch (ms/us/ns timestamps) is the more
# likely explanation than a genuinely long session.
UNIT_MISMATCH_WARN_WALL_SEC = 3600.0


class ReplayError(RuntimeError):
    """Base class for every error raised by this harness."""


class ReplayOrderingError(ReplayError):
    """Raised in strict mode when the input tick log is not in replay order."""


class ReplayCallbackError(ReplayError):
    """
    Raised when the strategy callback under test raises.

    The original exception is chained (`__cause__`) and the offending tick is attached,
    because "which tick broke the strategy" is the whole point of running a replay.
    """

    def __init__(self, message: str, tick: "ReplayTick", tick_index: int):
        super().__init__(message)
        self.tick = tick
        self.tick_index = tick_index


@dataclass
class ReplayTick:
    """
    One recorded market data event.

    timestamp is in SECONDS (see module docstring). sequence_id is the venue's own
    ordering token and is used to break timestamp ties deterministically.

    No quote sanity check is applied: locked and crossed books occur in real captures
    and a harness that rejected them could not replay the sessions most worth
    replaying. Consequently `price` is meaningful only when both sides are quoted -
    on a one-sided book (bid=0.0) the arithmetic mid is misleading, and if either side
    is NaN the mid is NaN.
    """
    symbol: str
    timestamp: float
    sequence_id: int
    bid: float
    ask: float
    volume: float

    @property
    def price(self) -> float:
        """Arithmetic mid. See the class docstring for when this is not meaningful."""
        return (self.bid + self.ask) / 2.0


@dataclass
class ReplaySessionSummary:
    """
    Outcome of ONE `replay_session` call. Counts are per-session, not cumulative over
    the harness's lifetime.

    Timing fields are unrounded so a caller can recompute any ratio from the report.

    scheduling-lag fields are only meaningful when `wall_clock_replay` is True; in
    ASAP mode there are no deadlines to miss and they are reported as 0.0.

    achieved_speed_multiplier is simulated_duration / actual_wall_time: 0.0 for a
    capture with no simulated span (a single tick, or ticks all sharing one
    timestamp), and inf when no wall time elapsed at all.
    """
    total_ticks_replayed: int
    simulated_duration_sec: float
    actual_wall_time_sec: float
    speed_multiplier: float
    emitted_orders_count: int
    wall_clock_replay: bool = False
    achieved_speed_multiplier: float = 0.0
    ticks_dispatched_late: int = 0
    max_scheduling_lag_sec: float = 0.0
    mean_scheduling_lag_sec: float = 0.0
    out_of_order_input_pairs: int = 0


def _require_positive_speed(value: float) -> float:
    """Speed must be a positive real. `inf` is allowed and means ASAP."""
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f"speed_multiplier must be a real number, got {value!r}")
    if math.isnan(value):
        raise ValueError("speed_multiplier must not be NaN")
    if value <= 0.0:
        raise ValueError(
            f"speed_multiplier must be > 0, got {value!r}. A zero or negative "
            "multiplier has no replay meaning; use asap_mode=True for zero delay.")
    return float(value)


def _require_non_negative(name: str, value: Optional[float]) -> Optional[float]:
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f"{name} must be a real number or None, got {value!r}")
    if not math.isfinite(value) or value < 0.0:
        raise ValueError(f"{name} must be a finite value >= 0, got {value!r}")
    return float(value)


def _replay_key(tick: "ReplayTick", index: int) -> Tuple[float, int]:
    """
    Sort key: (timestamp, sequence_id).

    The sequence tie-break exists for REPRODUCIBILITY, not for cross-venue accuracy.
    Within one venue's capture the sequence number is the venue's authoritative order.
    Across merged multi-venue captures the sequence spaces are independent, so the
    tie-break is arbitrary - but stable, which is what a regression baseline needs.
    Merge such captures into one monotonic sequence space before replaying.
    """
    timestamp = getattr(tick, "timestamp", None)
    sequence_id = getattr(tick, "sequence_id", None)
    if not isinstance(timestamp, (int, float)) or isinstance(timestamp, bool):
        raise ValueError(
            f"tick_log[{index}] has a non-numeric timestamp {timestamp!r}")
    if not math.isfinite(float(timestamp)):
        raise ValueError(
            f"tick_log[{index}] has a non-finite timestamp {timestamp!r}; NaN and inf "
            "timestamps sort arbitrarily and would silently randomise replay order")
    if not isinstance(sequence_id, int) or isinstance(sequence_id, bool):
        raise ValueError(
            f"tick_log[{index}] has a non-integer sequence_id {sequence_id!r}; the "
            "sequence is required to break timestamp ties deterministically")
    return (float(timestamp), sequence_id)


class MarketDataReplayHarness:
    """
    Replays a recorded tick session through a strategy callback at a controlled speed.

    Speed modes:
      - `speed_multiplier=1.0`  - original wall-clock spacing.
      - `speed_multiplier=10.0` - 10x fast-forward; a 1 s gap becomes 100 ms.
      - `asap_mode=True` (or `speed_multiplier=float('inf')`) - no delay at all.

    Constructor arguments after `speed_multiplier` are keyword-only.

    `clock` and `sleeper` are injected so the scheduler can be tested deterministically
    against a fake clock; production callers should leave them at their defaults.
    """

    def __init__(
        self,
        speed_multiplier: float = 1.0,
        *,
        strict_ordering: bool = False,
        retain_replayed_ticks: bool = True,
        min_sleep_sec: float = DEFAULT_MIN_SLEEP_SEC,
        late_tolerance_sec: float = DEFAULT_LATE_TOLERANCE_SEC,
        max_projected_wall_time_sec: Optional[float] = None,
        clock: Callable[[], float] = time.perf_counter,
        sleeper: Callable[[float], None] = time.sleep,
    ) -> None:
        """
        strict_ordering:            raise instead of silently reordering a tick log
                                    that is not already in replay order.
        retain_replayed_ticks:      keep every dispatched tick in `replayed_ticks`.
                                    Set False when replaying millions of ticks - the
                                    counts in the summary are maintained either way.
        min_sleep_sec:              sleeps shorter than this are skipped (see module
                                    docstring on achievable sleep granularity).
        late_tolerance_sec:         lag above which a tick counts as dispatched late.
        max_projected_wall_time_sec: refuse a session whose projected wall time exceeds
                                    this. The usual cause of an absurd projection is
                                    timestamps that are not in seconds.
        """
        self.speed_multiplier = _require_positive_speed(speed_multiplier)
        self.strict_ordering = bool(strict_ordering)
        self.retain_replayed_ticks = bool(retain_replayed_ticks)
        self.min_sleep_sec = float(_require_non_negative("min_sleep_sec", min_sleep_sec))
        self.late_tolerance_sec = float(
            _require_non_negative("late_tolerance_sec", late_tolerance_sec))
        self.max_projected_wall_time_sec = _require_non_negative(
            "max_projected_wall_time_sec", max_projected_wall_time_sec)
        if not callable(clock) or not callable(sleeper):
            raise ValueError("clock and sleeper must both be callables")
        self._clock = clock
        self._sleeper = sleeper

        self.replayed_ticks: List[ReplayTick] = []
        self.generated_orders: List[Dict[str, Any]] = []
        self._current_tick: Optional[ReplayTick] = None

    def simulated_now(self) -> Optional[float]:
        """
        Timestamp of the tick currently being dispatched, or None outside a dispatch.

        Strategy code under test should read replay time from here (or from the tick
        it was handed) rather than from `time.time()`, which is what makes a replay
        non-reproducible. This is advisory: the harness cannot force a callback to
        use it.
        """
        return None if self._current_tick is None else self._current_tick.timestamp

    def replay_session(
        self,
        tick_log: Sequence[ReplayTick],
        strategy_callback: Callable[[ReplayTick], Optional[Dict[str, Any]]],
        asap_mode: bool = False,
    ) -> ReplaySessionSummary:
        """
        Replay `tick_log` through `strategy_callback` and return a per-session summary.

        The callback returns a dict to emit an order, or None for no order. Any other
        return type raises TypeError - a callback returning a list of orders or a bare
        Order object is a wiring mistake that would otherwise be miscounted silently.

        Per-session state (`replayed_ticks`, `generated_orders`) is reset on entry, so
        reusing one harness across several sessions does not inflate the counts.

        Raises:
            ValueError            - invalid arguments or malformed ticks.
            ReplayOrderingError   - strict mode, input not in replay order.
            ReplayCallbackError   - the strategy callback raised (tick attached).
        """
        if not callable(strategy_callback):
            raise ValueError("strategy_callback must be callable")
        if isinstance(tick_log, (str, bytes)) or not isinstance(tick_log, AbcSequence):
            raise ValueError(
                f"tick_log must be a sequence of ReplayTick, got {type(tick_log).__name__}")

        # Reset per-session accumulators BEFORE the empty-log early return, so a second
        # session on the same harness never reports the first session's orders.
        self.replayed_ticks = []
        self.generated_orders = []
        self._current_tick = None

        if len(tick_log) == 0:
            logger.warning("Market Data Replay called with an empty tick log; nothing to replay.")
            return ReplaySessionSummary(0, 0.0, 0.0, self.speed_multiplier, 0)

        sorted_ticks, out_of_order_count = self._order_ticks(tick_log)
        total_ticks = len(sorted_ticks)

        t_start_sim = sorted_ticks[0].timestamp
        sim_duration = max(0.0, sorted_ticks[-1].timestamp - t_start_sim)

        # ASAP and an infinite multiplier are the same thing: no deadlines at all.
        timed_replay = not asap_mode and math.isfinite(self.speed_multiplier)
        if timed_replay:
            self._check_projected_wall_time(sim_duration)

        logger.info(
            f"Starting Market Data Replay: {total_ticks} ticks over {sim_duration:.6f}s "
            f"simulated time (speed={self.speed_multiplier}x, asap={not timed_replay}).")

        late_ticks = 0
        max_lag = 0.0
        lag_total = 0.0

        t_start_wall = self._clock()
        for i, tick in enumerate(sorted_ticks):
            if timed_replay:
                deadline = t_start_wall + (tick.timestamp - t_start_sim) / self.speed_multiplier
                remaining = deadline - self._clock()
                # Absolute deadlines: an overshoot on tick i does not push tick i+1.
                # Sleeps shorter than min_sleep_sec (and zero-length ones) are skipped;
                # the OS cannot deliver them and the shortfall is booked as lag below.
                if remaining > 0.0 and remaining >= self.min_sleep_sec:
                    self._sleeper(remaining)
                lag = self._clock() - deadline
                if lag > 0.0:
                    lag_total += lag
                    max_lag = max(max_lag, lag)
                    if lag > self.late_tolerance_sec:
                        late_ticks += 1

            if self.retain_replayed_ticks:
                self.replayed_ticks.append(tick)

            self._current_tick = tick
            try:
                order = strategy_callback(tick)
            except Exception as exc:  # re-raised below with the tick that caused it
                logger.error(
                    f"Strategy callback raised on tick_log index {i} "
                    f"(symbol={tick.symbol}, seq={tick.sequence_id}, ts={tick.timestamp}): {exc}")
                raise ReplayCallbackError(
                    f"strategy_callback raised on replayed tick {i} "
                    f"(symbol={tick.symbol}, seq={tick.sequence_id}, ts={tick.timestamp})",
                    tick=tick, tick_index=i) from exc
            finally:
                self._current_tick = None

            if order is None:
                continue
            if not isinstance(order, dict):
                raise TypeError(
                    f"strategy_callback must return a dict or None, got "
                    f"{type(order).__name__} on tick {i} (symbol={tick.symbol}, "
                    f"seq={tick.sequence_id})")
            self.generated_orders.append(order)

        actual_wall = self._clock() - t_start_wall
        emitted_orders = len(self.generated_orders)

        if actual_wall > 0.0:
            achieved_speed = sim_duration / actual_wall
        else:
            achieved_speed = math.inf if sim_duration > 0.0 else 0.0

        summary = ReplaySessionSummary(
            total_ticks_replayed=total_ticks,
            simulated_duration_sec=sim_duration,
            actual_wall_time_sec=actual_wall,
            speed_multiplier=self.speed_multiplier,
            emitted_orders_count=emitted_orders,
            wall_clock_replay=timed_replay,
            achieved_speed_multiplier=achieved_speed,
            ticks_dispatched_late=late_ticks,
            max_scheduling_lag_sec=max_lag,
            mean_scheduling_lag_sec=(lag_total / total_ticks) if timed_replay else 0.0,
            out_of_order_input_pairs=out_of_order_count,
        )

        logger.info(
            f"Completed Market Data Replay: {total_ticks} ticks in {actual_wall:.6f}s wall "
            f"time, {emitted_orders} orders emitted.")
        if timed_replay and late_ticks:
            logger.warning(
                f"{late_ticks}/{total_ticks} ticks were dispatched more than "
                f"{self.late_tolerance_sec}s after their deadline (max lag "
                f"{max_lag:.6f}s). The consumer could not keep up at "
                f"{self.speed_multiplier}x, so the strategy did not see the recorded "
                "arrival spacing; treat any latency-sensitive assertion as unproven.")

        return summary

    def _order_ticks(
        self, tick_log: Sequence[ReplayTick]
    ) -> Tuple[List[ReplayTick], int]:
        """
        Validate and order the tick log by (timestamp, sequence_id).

        Out-of-order input is counted and reported rather than silently repaired: a
        capture whose ticks are not in order usually means a broken recorder or two
        interleaved feeds, and that is a finding about the data, not a detail to hide.
        """
        keyed = [(_replay_key(tick, i), i, tick) for i, tick in enumerate(tick_log)]
        out_of_order = sum(
            1 for a, b in zip(keyed, keyed[1:]) if b[0] < a[0])

        if out_of_order:
            first_bad = next(
                b[1] for a, b in zip(keyed, keyed[1:]) if b[0] < a[0])
            message = (
                f"tick_log is not in replay order: {out_of_order} of {len(keyed) - 1} "
                f"adjacent pairs go backwards, first at index {first_bad}. An "
                "out-of-order capture usually means a broken recorder or two "
                "interleaved feeds.")
            if self.strict_ordering:
                raise ReplayOrderingError(message)
            logger.warning(f"{message} Sorting before replay.")

        keyed.sort(key=lambda item: item[0])
        return [item[2] for item in keyed], out_of_order

    def _check_projected_wall_time(self, sim_duration: float) -> None:
        """Catch millisecond/nanosecond timestamps before sleeping for a decade."""
        projected = sim_duration / self.speed_multiplier
        if (self.max_projected_wall_time_sec is not None
                and projected > self.max_projected_wall_time_sec):
            raise ValueError(
                f"projected replay wall time {projected:.1f}s exceeds "
                f"max_projected_wall_time_sec={self.max_projected_wall_time_sec}. "
                "ReplayTick.timestamp must be in SECONDS - millisecond or nanosecond "
                "timestamps produce projections like this.")
        if projected > UNIT_MISMATCH_WARN_WALL_SEC:
            logger.warning(
                f"Projected replay wall time is {projected:.1f}s at "
                f"{self.speed_multiplier}x. If the capture is not really that long, "
                "the timestamps are probably in milliseconds or nanoseconds rather "
                "than seconds.")
