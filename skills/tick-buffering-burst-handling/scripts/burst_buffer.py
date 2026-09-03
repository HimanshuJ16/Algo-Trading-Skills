"""
tick-buffering-burst-handling: Production-grade bounded per-symbol tick buffer manager,
empirical peak-rate capacity sizing, keep-latest-N vs drop-newest strategies,
high-water mark occupancy tracking, and structured drop audit logging.

Contract every caller must honour
---------------------------------
``push()`` reports whether the *incoming* tick was admitted. Under
``KEEP_LATEST_N`` it returns ``True`` even though an older unconsumed tick was
just evicted to make room. **A ``True`` return is not a statement that no data
was lost.** Loss is reported by :meth:`BurstBufferManager.get_occupancy_report`
(``dropped`` / ``drop_rate_pct``) and by :attr:`BurstBufferManager.drop_counts`.
Reading ``True`` as "nothing was dropped" reintroduces the silent tick loss this
skill exists to prevent.

Bounded audit log
-----------------
``drop_logs`` is a bounded ring of the most recent :class:`TickDropRecord`
objects, **not** a complete record of every drop. An unbounded audit list is
itself an OOM vector: a saturated buffer drops on every push, so one record per
drop grows without limit -- and pins the dropped tick object -- precisely during
the volatility burst this skill is meant to survive. Exact totals are kept as
integer counters in ``drop_counts``, which never lose information. Persist
records off-process if a full forensic trail is required.

Threading
---------
``collections.deque`` documents "thread-safe, memory efficient appends and pops
from either side", but that guarantee covers *individual* operations only. Every
compound sequence here -- test-membership-then-create a symbol buffer,
test-full-then-evict, read-then-update a high-water mark -- is not atomic, so all
state mutation happens under one lock. The unlocked check-then-create in this
module's previous implementation let two threads each build a ``deque`` for the
same new symbol; the second assignment won, and every tick the first thread had
appended was discarded while ``push()`` returned ``True``.

The lock is manager-wide rather than per-symbol. Under CPython the GIL already
serialises these O(1) deque operations, so per-symbol locks buy little and add a
lock-ordering hazard; revisit only if profiling shows real contention.

Bounded-deque caveat
--------------------
A ``deque(maxlen=N)`` silently discards from the opposite end when it is full and
you append -- an *implicit* drop-oldest policy applied by the data structure.
Route every push through this manager so the policy applied is the one you chose
and the loss is counted.
"""
from collections import deque
from dataclasses import dataclass
from enum import Enum
import logging
import math
import threading
import time
from typing import Any, Deque, Dict, List, Optional

logger = logging.getLogger(__name__)

__all__ = [
    "BurstBufferConfigError",
    "DropStrategy",
    "TickDropRecord",
    "BurstBufferManager",
    "SymbolBuffer",
    "DEFAULT_DROP_LOG_CAPACITY",
    "DEFAULT_MIN_CAPACITY",
]

#: Default number of recent drop records retained per manager (see "Bounded audit log").
DEFAULT_DROP_LOG_CAPACITY = 1000

#: Default floor applied by :meth:`BurstBufferManager.calculate_empirical_capacity`.
#: A buffer smaller than this cannot absorb even a single scheduler hiccup, so a
#: very low measured tick rate should not yield a buffer that overflows on the
#: first GC pause. It is a floor, not a recommendation -- size from measured peaks.
DEFAULT_MIN_CAPACITY = 50


class BurstBufferConfigError(ValueError):
    """Raised on an invalid buffer configuration.

    Configuration is rejected at construction rather than degraded at runtime: a
    capacity of ``0`` previously produced a buffer that accepted every tick,
    retained none, counted no drops, and reported 0% occupancy -- total silent
    data loss behind a clean-looking audit trail.
    """


class DropStrategy(str, Enum):
    KEEP_LATEST_N = "KEEP_LATEST_N"        # Drops oldest unconsumed tick when full (overwrite semantics)
    DROP_NEWEST_LOG = "DROP_NEWEST_LOG"    # Drops incoming tick when full and records structured audit log


@dataclass
class TickDropRecord:
    """One overflow event, retained for post-session audit analysis.

    ``timestamp`` is wall clock (:func:`time.time`) so records correlate with
    exchange tick timestamps and session logs. Do not use it to measure
    durations -- it is subject to NTP steps.
    """

    timestamp: float
    symbol: str
    drop_strategy: DropStrategy
    dropped_tick: Any
    buffer_capacity: int
    occupancy_pct: float


class BurstBufferManager:
    """
    Empirically sized bounded tick buffer manager maintaining per-symbol buffers,
    preventing memory leaks / OOM crashes during market volatility bursts.

    Thread-safe: a feed/callback thread may ``push()`` while a strategy thread
    ``pop_oldest()``/``drain()``s the same symbol.
    """

    def __init__(
        self,
        default_capacity: int = 500,
        strategy: DropStrategy = DropStrategy.KEEP_LATEST_N,
        custom_capacities: Optional[Dict[str, int]] = None,
        drop_log_capacity: int = DEFAULT_DROP_LOG_CAPACITY,
        min_warn_interval_sec: float = 1.0,
    ) -> None:
        """
        Args:
            default_capacity: Bounded capacity for symbols without an override.
            strategy: Overflow policy applied to every symbol.
            custom_capacities: Per-symbol capacity overrides. Keys are normalised
                exactly as pushed symbols are, so ``{"nifty": 5000}`` and
                ``{"NIFTY": 5000}`` are equivalent -- an un-normalised key was
                previously ignored in silence, leaving the one symbol you
                deliberately sized for a burst running on the default capacity.
            drop_log_capacity: Size of the retained drop-record ring.
            min_warn_interval_sec: Minimum seconds between overflow warnings per
                symbol. A saturated buffer drops on every push, so per-drop
                logging makes the log write the next bottleneck; warnings are
                rate-limited and carry an aggregate count instead.

        Raises:
            BurstBufferConfigError: On a non-positive or non-integer capacity, an
                invalid warning interval, or symbol keys that collide after
                normalisation with differing capacities.
        """
        self.default_capacity = self._validate_capacity(default_capacity, "default_capacity")
        self.strategy = DropStrategy(strategy)
        self.drop_log_capacity = self._validate_capacity(drop_log_capacity, "drop_log_capacity")

        if (isinstance(min_warn_interval_sec, bool)
                or not isinstance(min_warn_interval_sec, (int, float))
                or not math.isfinite(min_warn_interval_sec)
                or min_warn_interval_sec < 0):
            raise BurstBufferConfigError(
                f"min_warn_interval_sec must be a finite, non-negative number, "
                f"got {min_warn_interval_sec!r}"
            )
        self.min_warn_interval_sec = float(min_warn_interval_sec)

        self.custom_capacities: Dict[str, int] = {}
        for raw_symbol, cap in (custom_capacities or {}).items():
            symbol = self._normalize(raw_symbol)
            validated = self._validate_capacity(cap, f"custom_capacities[{raw_symbol!r}]")
            existing = self.custom_capacities.get(symbol)
            if existing is not None and existing != validated:
                raise BurstBufferConfigError(
                    f"custom_capacities has conflicting capacities for symbol {symbol!r} "
                    f"after normalisation ({existing} vs {validated})"
                )
            self.custom_capacities[symbol] = validated

        self._lock = threading.RLock()
        self.buffers: Dict[str, Deque[Any]] = {}
        self.drop_logs: Deque[TickDropRecord] = deque(maxlen=self.drop_log_capacity)
        self.high_water_marks: Dict[str, float] = {}  # {symbol: max_occupancy_pct}
        self.drop_counts: Dict[str, int] = {}         # {symbol: exact lifetime ticks lost}
        self.accept_counts: Dict[str, int] = {}       # {symbol: exact lifetime ticks admitted}
        self.offered_counts: Dict[str, int] = {}      # {symbol: exact lifetime push() calls}
        self._last_warn_ts: Dict[str, float] = {}
        self._unwarned_drops: Dict[str, int] = {}

    # ------------------------------------------------------------------ helpers

    @staticmethod
    def _normalize(symbol: str) -> str:
        """Canonical symbol key. Applied to pushes and configured overrides alike."""
        if not isinstance(symbol, str) or not symbol.strip():
            raise BurstBufferConfigError(f"symbol must be a non-empty string, got {symbol!r}")
        return symbol.strip().upper()

    @staticmethod
    def _validate_capacity(capacity: Any, field_name: str) -> int:
        if isinstance(capacity, bool) or not isinstance(capacity, int):
            raise BurstBufferConfigError(f"{field_name} must be an int, got {capacity!r}")
        if capacity < 1:
            raise BurstBufferConfigError(
                f"{field_name} must be >= 1, got {capacity}. A zero or negative capacity "
                f"discards every tick without recording a drop."
            )
        return capacity

    @staticmethod
    def calculate_empirical_capacity(
        peak_ticks_per_sec: float,
        max_lag_sec: float = 2.0,
        min_capacity: int = DEFAULT_MIN_CAPACITY,
    ) -> int:
        """Bounded buffer capacity from a *measured* peak tick rate.

        ``capacity = ceil(peak_ticks_per_sec * max_lag_sec)``, floored at
        ``min_capacity``. ``max_lag_sec`` is how far behind the consumer may fall
        before the overflow policy engages -- a lag *tolerance*, not a safety
        margin. Raising it buys queueing delay on every downstream decision, not
        reliability; a consumer that is persistently behind is a backlog, which
        belongs to ``backpressure-drop-degrade-policy``.

        Feed a peak observed on the feed you will actually consume. Venue-wide
        peaks and retail broker WebSocket rates differ by orders of magnitude
        (see ``references/standards.md``).

        Raises:
            BurstBufferConfigError: On a non-finite or non-positive rate or lag,
                or an invalid ``min_capacity``.
        """
        for label, value in (("peak_ticks_per_sec", peak_ticks_per_sec), ("max_lag_sec", max_lag_sec)):
            if isinstance(value, bool) or not isinstance(value, (int, float)):
                raise BurstBufferConfigError(f"{label} must be a number, got {value!r}")
            if not math.isfinite(value):
                raise BurstBufferConfigError(f"{label} must be finite, got {value!r}")
            if value <= 0:
                raise BurstBufferConfigError(f"{label} must be > 0, got {value!r}")
        floor = BurstBufferManager._validate_capacity(min_capacity, "min_capacity")
        return max(floor, int(math.ceil(peak_ticks_per_sec * max_lag_sec)))

    def _get_buffer_locked(self, symbol: str) -> Deque[Any]:
        """Return (creating if absent) the buffer for ``symbol``. Caller holds the lock."""
        buf = self.buffers.get(symbol)
        if buf is None:
            cap = self.custom_capacities.get(symbol, self.default_capacity)
            buf = deque(maxlen=cap)
            self.buffers[symbol] = buf
            self.high_water_marks[symbol] = 0.0
            self.drop_counts[symbol] = 0
            self.accept_counts[symbol] = 0
            self.offered_counts[symbol] = 0
        return buf

    def _record_drop_locked(
        self, symbol: str, dropped_tick: Any, capacity: int
    ) -> Optional[tuple]:
        """Count the drop exactly and retain a bounded record.

        Returns the arguments for a rate-limited warning, or ``None`` if this
        drop falls inside the current warning interval. The caller emits the
        warning *after* releasing the lock: a blocking log handler (network,
        full disk) must never stall the feed thread while holding the buffer
        lock, which would recreate the ingest stall this skill exists to avoid.
        """
        self.drop_counts[symbol] = self.drop_counts.get(symbol, 0) + 1
        self.drop_logs.append(
            TickDropRecord(
                timestamp=time.time(),
                symbol=symbol,
                drop_strategy=self.strategy,
                dropped_tick=dropped_tick,
                buffer_capacity=capacity,
                occupancy_pct=100.0,
            )
        )
        self.high_water_marks[symbol] = 100.0

        self._unwarned_drops[symbol] = self._unwarned_drops.get(symbol, 0) + 1
        now = time.monotonic()
        last = self._last_warn_ts.get(symbol)
        if last is not None and (now - last) < self.min_warn_interval_sec:
            return None
        self._last_warn_ts[symbol] = now
        pending = self._unwarned_drops.pop(symbol, 0)
        return (symbol, capacity, self.strategy.value, pending, self.drop_counts[symbol])

    @staticmethod
    def _emit_drop_warning(warning: Optional[tuple]) -> None:
        """Emit a rate-limited overflow warning. Called with the lock released."""
        if warning is None:
            return
        logger.warning(
            "Burst buffer full for %s (capacity=%d, strategy=%s): %d tick(s) dropped "
            "since last warning, %d total.",
            *warning,
        )

    # -------------------------------------------------------------------- write

    def push(self, symbol: str, tick: Any) -> bool:
        """Admit ``tick`` for ``symbol``, applying the configured overflow policy.

        Returns:
            ``True`` if the incoming tick was admitted. Under ``KEEP_LATEST_N``
            this is ``True`` even when an older tick was evicted to make room --
            see the module docstring. ``False`` only under ``DROP_NEWEST_LOG``
            when the buffer is full and the incoming tick was discarded.

        Raises:
            BurstBufferConfigError: If ``symbol`` is not a non-empty string.
        """
        sym = self._normalize(symbol)
        warning: Optional[tuple] = None
        try:
            with self._lock:
                buf = self._get_buffer_locked(sym)
                cap = buf.maxlen
                if cap is None:  # unreachable: every buffer is built with a validated maxlen
                    raise BurstBufferConfigError(f"buffer for {sym!r} is unbounded")
                self.offered_counts[sym] = self.offered_counts.get(sym, 0) + 1

                if len(buf) >= cap:
                    if self.strategy == DropStrategy.KEEP_LATEST_N:
                        warning = self._record_drop_locked(sym, buf[0], cap)
                        buf.append(tick)  # deque(maxlen) evicts buf[0] as part of the append
                        self.accept_counts[sym] = self.accept_counts.get(sym, 0) + 1
                        return True
                    warning = self._record_drop_locked(sym, tick, cap)
                    return False

                buf.append(tick)
                self.accept_counts[sym] = self.accept_counts.get(sym, 0) + 1
                current_occ = (len(buf) / cap) * 100.0
                if current_occ > self.high_water_marks[sym]:
                    self.high_water_marks[sym] = current_occ
                return True
        finally:
            self._emit_drop_warning(warning)

    # --------------------------------------------------------------------- read

    def get_latest(self, symbol: str) -> Optional[Any]:
        """Most recent tick for ``symbol``, or ``None`` if unknown or empty.

        Does not create a buffer for an unseen symbol: a monitoring loop polling
        a rotating universe would otherwise grow ``buffers`` without bound and
        pad the occupancy report with phantom symbols.
        """
        sym = self._normalize(symbol)
        with self._lock:
            buf = self.buffers.get(sym)
            return buf[-1] if buf else None

    def pop_oldest(self, symbol: str) -> Optional[Any]:
        """Consume the oldest buffered tick for ``symbol``, or ``None`` if unknown or empty."""
        sym = self._normalize(symbol)
        with self._lock:
            buf = self.buffers.get(sym)
            return buf.popleft() if buf else None

    def drain(self, symbol: str, max_items: Optional[int] = None) -> List[Any]:
        """Atomically consume up to ``max_items`` (default: all) buffered ticks, oldest first.

        Draining under a single lock acquisition avoids the read-``len()``-then-pop
        pattern, which races a concurrent consumer and pops from an emptied buffer.
        """
        sym = self._normalize(symbol)
        if max_items is not None and (
            isinstance(max_items, bool) or not isinstance(max_items, int) or max_items < 0
        ):
            raise BurstBufferConfigError(
                f"max_items must be a non-negative int or None, got {max_items!r}"
            )
        with self._lock:
            buf = self.buffers.get(sym)
            if not buf:
                return []
            limit = len(buf) if max_items is None else min(max_items, len(buf))
            return [buf.popleft() for _ in range(limit)]

    @property
    def total_drops(self) -> int:
        """Exact lifetime drop count across all symbols, unaffected by the bounded log."""
        with self._lock:
            return sum(self.drop_counts.values())

    def get_occupancy_report(self) -> Dict[str, Any]:
        """Per-symbol occupancy, high-water mark, and exact tick accounting.

        ``dropped`` and ``drop_rate_pct`` are the audit answer to "how much data
        did this burst cost us?" -- occupancy alone cannot distinguish a buffer
        that merely ran hot from one that overflowed.

        Field semantics differ by strategy, so the loss rate is always taken
        against ``offered`` (one count per ``push()``), never against
        ``accepted + dropped``:

        * ``DROP_NEWEST_LOG`` -- every push is either accepted or dropped, so
          ``accepted + dropped == offered``.
        * ``KEEP_LATEST_N`` -- every push is accepted, and a drop is the later
          *eviction* of a tick already counted in ``accepted``. Summing the two
          double-counts the burst and understates the loss rate (20 pushes into
          a 5-slot buffer is 75% loss, not 15/35 = 42.86%).
        """
        with self._lock:
            report: Dict[str, Any] = {}
            for sym, buf in self.buffers.items():
                cap = buf.maxlen or self.default_capacity
                dropped = self.drop_counts.get(sym, 0)
                offered = self.offered_counts.get(sym, 0)
                report[sym] = {
                    "current_size": len(buf),
                    "capacity": cap,
                    "occupancy_pct": round((len(buf) / cap) * 100.0, 2),
                    "high_water_mark_pct": round(self.high_water_marks.get(sym, 0.0), 2),
                    "offered": offered,
                    "accepted": self.accept_counts.get(sym, 0),
                    "dropped": dropped,
                    "drop_rate_pct": round((dropped / offered) * 100.0, 2) if offered else 0.0,
                }
            return report


class SymbolBuffer:
    """Deprecated single-symbol buffer kept for backward compatibility.

    Prefer :class:`BurstBufferManager`, which is thread-safe and reports exact
    drop counts. This class is unsynchronised; ``drop_log`` is a bounded ring for
    the same OOM reason described in the module docstring, and ``drop_count``
    carries the exact total.
    """

    def __init__(self, maxlen: int = 500, drop_log_capacity: int = DEFAULT_DROP_LOG_CAPACITY) -> None:
        self.maxlen = BurstBufferManager._validate_capacity(maxlen, "maxlen")
        self.buffer: Deque[Any] = deque(maxlen=self.maxlen)
        self.drop_log: Deque[Dict[str, Any]] = deque(
            maxlen=BurstBufferManager._validate_capacity(drop_log_capacity, "drop_log_capacity")
        )
        self.drop_count: int = 0

    def push(self, tick: Any) -> None:
        if len(self.buffer) >= self.maxlen:
            self.drop_count += 1
            self.drop_log.append({"ts": time.time(), "dropped_oldest": self.buffer[0]})
        self.buffer.append(tick)

    def latest(self) -> Optional[Any]:
        return self.buffer[-1] if self.buffer else None
