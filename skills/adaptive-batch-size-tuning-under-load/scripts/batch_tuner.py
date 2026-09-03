"""Adaptive batch-size tuning engine for downstream database and message-broker
sinks.

Adapts the per-flush record count ``B`` and the flush interval ``T_flush`` to
two signals:

* **Batch fill ratio at the flush boundary** ``F = depth_at_flush / B`` — how
  full the batch was when it flushed. ``F == 1.0`` means the producer filled
  ``B`` records before ``T_flush`` elapsed (the sizing is the binding
  constraint, so load is high); ``F < 1.0`` means ``T_flush`` expired first
  (time is the binding constraint, so load is low). Smoothed with an EWMA.
* **Downstream sink write latency** — exponentially-weighted moving average
  (EWMA), used as the counter-acting throttle.

The engine does not write to the sink itself; consumers loop on
:meth:`add_item`, write the returned batch when non-`None`, and call
:meth:`record_write_latency` after each write completes.

Design notes
------------
* **Why batch fullness, not queue depth**: ``add_item`` hands the batch back
  as soon as the buffer reaches ``B``, so the internal deque depth is bounded
  by ``B`` by construction and ``depth / queue_capacity`` can never rise above
  ``B / queue_capacity``. Tuning on that quantity is a positive-feedback loop
  on ``B`` itself rather than a load signal — under saturation it drives ``B``
  *down* to ``B_min``. Batch fullness at the flush boundary is the signal that
  actually distinguishes "producer outran the batch size" from "timeout
  expired half-empty". ``queue_fill_ratio_*`` is still exported for
  backpressure observability, but it does not drive tuning.
* **Hysteresis / deadband tuning**: ``F`` is smoothed by an EWMA and tuning is
  applied only outside ``[F_low, F_high]`` (``0.10, 0.70``). Within that band
  we *never* perturb ``B`` or ``T_flush`` from the fill signal — only the
  latency throttle may act.
* **Closed loop, and the latency throttle outranks the fill branch**: as ``B``
  grows, sink write latency grows; once ``EWMA(L) > L_target`` the throttle
  shrinks ``B`` *and* the fill branch is barred from expanding until the
  latency comes back under target. That gate is load-bearing rather than
  cosmetic: expansion multiplies ``B`` by 1.5 while the throttle multiplies by
  0.8, and ``1.5 * 0.8 = 1.2 > 1``, so one throttle per flush cannot undo one
  expansion. Without the gate ``B`` ratchets to ``B_max`` and pins there with
  sink latency stuck permanently above target. With it, ``B`` settles where
  sink latency sits at the target.
* **Bounded queue**: ``max_queue_size`` is configurable; adding past it
  raises :class:`QueueFullError`. An unbounded deque is a production hazard.
* **EWMA seeding**: both EWMAs are seeded with their first observation rather
  than starting at 0. A cold-start-at-zero latency EWMA biases the throttle
  low and delays it by several samples, which is precisely the wrong direction
  for a safety mechanism.
* **Deterministic decision math**: tuning multipliers and bounds are constants
  (multipliers from `references/standards.md`). No magic numbers inside
  methods.
* **Logging**: structured via `extra=` dict, level-hierarchy: state changes at
  INFO, sustained anomalies at WARNING, configuration errors at ERROR.
* **Time**: all intervals use ``time.monotonic()``; never wall-clock.
* **Thread safety**: a single lock guards the queue and the tunables. The
  returned batch is detached from the queue before return, and the optional
  ``on_flush`` callback is invoked *outside* the lock, so a slow sink write
  never blocks other producers and a callback may safely re-enter the engine.
  The trade-off is that under concurrent producers ``on_flush`` invocations
  are not ordered relative to one another; serialise inside the callback (or
  use a single consumer thread) if you need ordered writes.
* **Timeout flushes are caller-driven**: the engine owns no threads and no
  timer. ``T_flush`` is only evaluated when the caller calls into the engine,
  so a producer that goes quiet leaves records buffered indefinitely. Drive
  :meth:`flush_if_due` from a scheduler if the stream can stall.
"""

from __future__ import annotations

from collections import deque
from dataclasses import dataclass
import logging
import math
import threading
import time
from typing import Any, Callable, Deque, Dict, List, Optional

__all__ = [
    "AdaptiveBatchTunerEngine",
    "BatchTunerStatus",
    "QueueFullError",
    "TuningConfig",
]


# ---------------------------------------------------------------------------
# Default tuning curve (extracted to constants so references/standards.md and
# the code cannot drift apart).
# ---------------------------------------------------------------------------

DEFAULT_FILL_LOW_THRESHOLD: float = 0.10
DEFAULT_FILL_HIGH_THRESHOLD: float = 0.70
DEFAULT_FILL_EWMA_ALPHA: float = 0.3
DEFAULT_EXPAND_MULTIPLIER: float = 1.5
DEFAULT_SHRINK_DIVISOR: float = 1.2
DEFAULT_LATENCY_THROTTLE_MULTIPLIER: float = 0.8
DEFAULT_LATENCY_TIMEOUT_REDUCTION: float = 0.8
DEFAULT_TIMEOUT_INCREASE: float = 1.2

_LOGGER = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Public exception
# ---------------------------------------------------------------------------


class QueueFullError(RuntimeError):
    """Raised when ``add_item`` is called and the bounded deque is at capacity."""

    def __init__(self, queued: int, capacity: int):
        super().__init__(
            f"Queue capacity exceeded: {queued}/{capacity} items buffered. "
            "Sink is too slow or capacity is undersized."
        )
        self.queued = queued
        self.capacity = capacity


# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class TuningConfig:
    """Static dimensions / thresholds for the tuner.

    Frozen so callers cannot silently mutate engine state.

    ``fill_low_threshold`` / ``fill_high_threshold`` / ``fill_ewma_alpha``
    apply to the **batch fill ratio** (``depth_at_flush / current_batch_size``),
    not to queue depth against ``queue_capacity`` — see the module docstring.

    ``queue_capacity`` is the denominator for the exported backpressure gauge
    only; ``max_queue_size`` is the hard cap that raises
    :class:`QueueFullError`.
    """

    min_batch_size: int = 10
    max_batch_size: int = 1000
    initial_batch_size: int = 100
    min_flush_timeout_sec: float = 0.05
    max_flush_timeout_sec: float = 1.0
    initial_flush_timeout_sec: float = 0.2
    target_write_latency_ms: float = 50.0
    latency_ewma_alpha: float = 0.2
    queue_capacity: int = 2000
    max_queue_size: int = 5000
    fill_low_threshold: float = DEFAULT_FILL_LOW_THRESHOLD
    fill_high_threshold: float = DEFAULT_FILL_HIGH_THRESHOLD
    fill_ewma_alpha: float = DEFAULT_FILL_EWMA_ALPHA
    expand_multiplier: float = DEFAULT_EXPAND_MULTIPLIER
    shrink_divisor: float = DEFAULT_SHRINK_DIVISOR
    latency_throttle_multiplier: float = DEFAULT_LATENCY_THROTTLE_MULTIPLIER
    latency_timeout_reduction: float = DEFAULT_LATENCY_TIMEOUT_REDUCTION
    timeout_increase: float = DEFAULT_TIMEOUT_INCREASE

    def __post_init__(self) -> None:
        if self.min_batch_size <= 0:
            raise ValueError("min_batch_size must be > 0")
        if self.max_batch_size < self.min_batch_size:
            raise ValueError(
                f"max_batch_size ({self.max_batch_size}) must be >= "
                f"min_batch_size ({self.min_batch_size})"
            )
        if not (self.min_batch_size <= self.initial_batch_size <= self.max_batch_size):
            raise ValueError(
                f"initial_batch_size ({self.initial_batch_size}) outside "
                f"[{self.min_batch_size}, {self.max_batch_size}]"
            )
        if self.min_flush_timeout_sec <= 0:
            raise ValueError("min_flush_timeout_sec must be > 0")
        if self.max_flush_timeout_sec < self.min_flush_timeout_sec:
            raise ValueError(
                "max_flush_timeout_sec must be >= min_flush_timeout_sec"
            )
        if not (
            self.min_flush_timeout_sec
            <= self.initial_flush_timeout_sec
            <= self.max_flush_timeout_sec
        ):
            raise ValueError(
                f"initial_flush_timeout_sec ({self.initial_flush_timeout_sec}) "
                f"outside [{self.min_flush_timeout_sec}, "
                f"{self.max_flush_timeout_sec}]"
            )
        if self.target_write_latency_ms <= 0:
            raise ValueError("target_write_latency_ms must be > 0")
        if not (0.0 < self.latency_ewma_alpha <= 1.0):
            raise ValueError("latency_ewma_alpha must be in (0, 1]")
        if not (0.0 < self.fill_ewma_alpha <= 1.0):
            raise ValueError("fill_ewma_alpha must be in (0, 1]")
        if not (0.0 < self.fill_low_threshold < self.fill_high_threshold < 1.0):
            raise ValueError(
                "require 0 < fill_low_threshold < fill_high_threshold < 1"
            )
        if self.queue_capacity <= 0:
            raise ValueError("queue_capacity must be > 0")
        if self.max_queue_size <= 0:
            raise ValueError("max_queue_size must be > 0")
        if self.queue_capacity > self.max_queue_size:
            # Otherwise the exported fill gauge can never reach 1.0 and any
            # capacity alert is calibrated against an unreachable number.
            raise ValueError(
                f"queue_capacity ({self.queue_capacity}) must be <= "
                f"max_queue_size ({self.max_queue_size})"
            )
        # Directional sanity: a mis-signed multiplier silently inverts or
        # disables the controller rather than failing loudly.
        if self.expand_multiplier <= 1.0:
            raise ValueError("expand_multiplier must be > 1")
        if self.shrink_divisor <= 1.0:
            raise ValueError("shrink_divisor must be > 1")
        if not (0.0 < self.latency_throttle_multiplier < 1.0):
            raise ValueError("latency_throttle_multiplier must be in (0, 1)")
        if not (0.0 < self.latency_timeout_reduction < 1.0):
            raise ValueError("latency_timeout_reduction must be in (0, 1)")
        if self.timeout_increase <= 1.0:
            raise ValueError("timeout_increase must be > 1")


# ---------------------------------------------------------------------------
# Status
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class BatchTunerStatus:
    """Snapshot of tuner state at a point in time.

    Use :meth:`as_dict` for JSON-serializable observability export.

    ``batch_fill_ratio_ewma`` is the signal that drives tuning. The
    ``queue_fill_ratio_*`` fields are backpressure gauges (buffer occupancy
    against ``queue_capacity``) and do not drive tuning.
    """

    current_batch_size: int
    current_flush_timeout_sec: float
    queue_depth: int
    queue_capacity: int
    queue_fill_ratio_raw: float
    queue_fill_ratio_ewma: float
    batch_fill_ratio_ewma: float
    ewma_write_latency_ms: float
    total_flushed_records: int
    total_flush_events: int
    total_tuning_transitions: int

    def as_dict(self) -> Dict[str, Any]:
        return {
            "current_batch_size": self.current_batch_size,
            "current_flush_timeout_sec": round(self.current_flush_timeout_sec, 4),
            "queue_depth": self.queue_depth,
            "queue_capacity": self.queue_capacity,
            "queue_fill_ratio_raw": round(self.queue_fill_ratio_raw, 4),
            "queue_fill_ratio_ewma": round(self.queue_fill_ratio_ewma, 4),
            "batch_fill_ratio_ewma": round(self.batch_fill_ratio_ewma, 4),
            "ewma_write_latency_ms": round(self.ewma_write_latency_ms, 3),
            "total_flushed_records": self.total_flushed_records,
            "total_flush_events": self.total_flush_events,
            "total_tuning_transitions": self.total_tuning_transitions,
        }


# ---------------------------------------------------------------------------
# Engine
# ---------------------------------------------------------------------------


class AdaptiveBatchTunerEngine:
    """Dynamically tunes write batch sizes and flush timeouts.

    Construct once per sink; instances are thread-safe (a single internal lock
    guards the queue + tunable state). Reuse instances across producer
    threads; **do not** mint a new engine per add.

    Example::

        tuner = AdaptiveBatchTunerEngine(TuningConfig(queue_capacity=2000))

        for tick in tick_stream:
            try:
                batch = tuner.add_item(tick)
            except QueueFullError:
                handle_overload(); continue
            if batch is None:
                continue
            t0 = time.monotonic()
            try:
                sink_write(batch)
            finally:
                latency_ms = (time.monotonic() - t0) * 1000
                tuner.record_write_latency(latency_ms)
    """

    def __init__(
        self,
        config: TuningConfig | None = None,
        *,
        logger: Optional[logging.Logger] = None,
        on_flush: Optional[Callable[[List[Any]], None]] = None,
    ) -> None:
        self._config = config or TuningConfig()
        self._logger = logger or _LOGGER
        self._on_flush = on_flush

        self._queue: Deque[Any] = deque()
        self._lock = threading.Lock()
        self._closed = False

        self.current_batch_size: int = self._config.initial_batch_size
        self.current_flush_timeout_sec: float = self._config.initial_flush_timeout_sec

        self._last_flush_time = time.monotonic()
        self._ewma_write_latency_ms: float = 0.0
        self._ewma_fill_ratio: float = 0.0
        self._ewma_batch_fill_ratio: float = 0.0
        self._latency_ewma_seeded = False
        self._fill_ewma_seeded = False
        self._batch_fill_ewma_seeded = False

        self.total_flushed_records: int = 0
        self.total_flush_events: int = 0
        self.total_tuning_transitions: int = 0

    # ------------------------------------------------------------------ ingest

    def add_item(self, item: Any) -> Optional[List[Any]]:
        """Append one item, return a flush-ready batch when conditions are met.

        Returns ``None`` if the buffer has not reached ``current_batch_size``
        and ``current_flush_timeout_sec`` has not elapsed.

        Raises :class:`QueueFullError` if ``max_queue_size`` is exceeded —
        callers must decide between backpressure (block / reject) and
        degradation (drop / overflow to disk). Raises :class:`RuntimeError`
        if the engine has been closed, because anything buffered after
        shutdown would never be flushed.
        """
        with self._lock:
            if self._closed:
                raise RuntimeError(
                    "add_item() called on a closed engine; buffered items "
                    "would never be flushed. Call reset() to reuse it."
                )
            if len(self._queue) >= self._config.max_queue_size:
                raise QueueFullError(len(self._queue), self._config.max_queue_size)

            self._queue.append(item)
            self._update_fill_ewma_locked()

            elapsed = time.monotonic() - self._last_flush_time
            should_flush = (
                len(self._queue) >= self.current_batch_size
                or elapsed >= self.current_flush_timeout_sec
            )
            if not should_flush:
                return None

            batch = self._flush_and_tune_locked()

        self._notify_flush(batch)
        return batch

    def flush_if_due(self) -> Optional[List[Any]]:
        """Flush iff ``current_flush_timeout_sec`` has elapsed since the last flush.

        The engine owns no timer thread, so ``T_flush`` is otherwise only
        evaluated inside :meth:`add_item`. A producer that goes quiet — a
        stalled feed, a thin instrument, the lull after the close — would
        leave records buffered indefinitely and lose them on a crash. Drive
        this from a scheduler (or from the consumer thread) at an interval at
        or below ``min_flush_timeout_sec``.

        Returns the batch when a timeout flush occurred, otherwise ``None``.
        Unlike :meth:`flush_now`, this *is* a genuine load observation (the
        timeout beat the size threshold), so it feeds the tuning curve.
        """
        with self._lock:
            if time.monotonic() - self._last_flush_time < self.current_flush_timeout_sec:
                return None
            if not self._queue:
                # Nothing to flush; restart the window so an idle engine does
                # not report "overdue" on every poll.
                self._last_flush_time = time.monotonic()
                return None
            batch = self._flush_and_tune_locked()

        self._notify_flush(batch)
        return batch

    def record_write_latency(self, latency_ms: float) -> None:
        """Submit observed downstream write latency.

        Updates the latency EWMA; if smoothed latency exceeds the target,
        shrinks the batch size to relieve contention. This is the closing
        half of the control loop: it is what stops the fill-ratio branch from
        expanding the batch past what the sink can absorb.

        Rejects negative and non-finite values — a NaN would propagate
        through the EWMA permanently (``NaN > target`` is ``False``, silently
        disabling the throttle for the lifetime of the engine) and would
        serialise as invalid JSON in the metrics export.
        """
        if not math.isfinite(latency_ms):
            raise ValueError(f"latency_ms must be finite, got {latency_ms!r}")
        if latency_ms < 0:
            raise ValueError(f"latency_ms must be >= 0, got {latency_ms}")

        with self._lock:
            self._ewma_write_latency_ms = self._ewma_update(
                self._ewma_write_latency_ms,
                latency_ms,
                self._config.latency_ewma_alpha,
                seeded=self._latency_ewma_seeded,
            )
            self._latency_ewma_seeded = True
            smoothed = self._ewma_write_latency_ms

            if smoothed > self._config.target_write_latency_ms:
                prev = self.current_batch_size
                new_size = max(
                    self._config.min_batch_size,
                    int(self.current_batch_size * self._config.latency_throttle_multiplier),
                )
                if new_size < prev:
                    self.current_batch_size = new_size
                    self.total_tuning_transitions += 1
                    self._logger.warning(
                        "batch_tuner.latency_throttle",
                        extra={
                            "event": "latency_throttle",
                            "smoothed_write_latency_ms": round(smoothed, 2),
                            "target_write_latency_ms": self._config.target_write_latency_ms,
                            "previous_batch_size": prev,
                            "new_batch_size": new_size,
                        },
                    )

    def flush_now(self) -> List[Any]:
        """Force an immediate flush regardless of size or timeout.

        Returns up to ``current_batch_size`` items, or an empty list if the
        queue is empty.

        A forced checkpoint flush is **not** a load observation — the batch is
        partial because the caller asked for it, not because the producer is
        slow — so this does not run the tuning curve. Use
        :meth:`flush_if_due` for timeout-driven flushes that should tune.
        """
        with self._lock:
            batch = self._extract_batch_locked(self.current_batch_size)

        self._notify_flush(batch)
        return batch

    # ------------------------------------------------------------------ status

    def get_status(self) -> BatchTunerStatus:
        """Snapshot of current engine state — safe to call from any thread."""
        with self._lock:
            depth = len(self._queue)
            capacity = self._config.queue_capacity
            raw_ratio = depth / float(capacity) if capacity > 0 else 0.0
            return BatchTunerStatus(
                current_batch_size=self.current_batch_size,
                current_flush_timeout_sec=self.current_flush_timeout_sec,
                queue_depth=depth,
                queue_capacity=capacity,
                queue_fill_ratio_raw=raw_ratio,
                queue_fill_ratio_ewma=self._ewma_fill_ratio,
                batch_fill_ratio_ewma=self._ewma_batch_fill_ratio,
                ewma_write_latency_ms=self._ewma_write_latency_ms,
                total_flushed_records=self.total_flushed_records,
                total_flush_events=self.total_flush_events,
                total_tuning_transitions=self.total_tuning_transitions,
            )

    def reset(self) -> None:
        """Clear the queue and reset all tunables to initial values.

        Idempotent — safe to call repeatedly. Also reopens a closed engine.
        Use between test scenarios or to re-tune after a sustained regime
        change. Any items still buffered are **discarded**; call
        :meth:`close` first if they must be persisted.
        """
        with self._lock:
            self._queue.clear()
            self._closed = False
            self.current_batch_size = self._config.initial_batch_size
            self.current_flush_timeout_sec = self._config.initial_flush_timeout_sec
            self._ewma_write_latency_ms = 0.0
            self._ewma_fill_ratio = 0.0
            self._ewma_batch_fill_ratio = 0.0
            self._latency_ewma_seeded = False
            self._fill_ewma_seeded = False
            self._batch_fill_ewma_seeded = False
            self._last_flush_time = time.monotonic()
            self.total_flushed_records = 0
            self.total_flush_events = 0
            self.total_tuning_transitions = 0

    def close(self) -> List[Any]:
        """Drain **every** remaining item as a final batch and close the engine.

        Returns the drained batch (possibly empty). Unlike :meth:`flush_now`
        this is not capped at ``current_batch_size`` — a shutdown drain that
        returned only part of the buffer would silently strand the remainder,
        which for an order-log or tick sink is data loss.

        The returned batch may therefore be larger than the sink accepts in
        one call; chunk it caller-side if so. Subsequent :meth:`add_item`
        calls raise ``RuntimeError`` until :meth:`reset` is called. Idempotent:
        closing twice returns ``[]`` the second time.
        """
        with self._lock:
            self._closed = True
            batch = self._extract_batch_locked(len(self._queue))

        self._notify_flush(batch)
        return batch

    # ------------------------------------------------------------------ internals

    def _flush_and_tune_locked(self) -> List[Any]:
        """Extract a batch and run the tuning curve on the resulting fullness.

        Caller must hold the lock. The fill signal is sampled *before*
        extraction, because that depth is what the producer actually managed
        to accumulate within the flush window.
        """
        depth_before = len(self._queue)
        batch = self._extract_batch_locked(self.current_batch_size)
        if not batch:
            return batch

        # Fullness of the batch we just cut: 1.0 means the size threshold
        # fired (producer outran B); < 1.0 means the timeout fired first.
        fullness = min(1.0, depth_before / float(self.current_batch_size))
        self._ewma_batch_fill_ratio = self._ewma_update(
            self._ewma_batch_fill_ratio,
            fullness,
            self._config.fill_ewma_alpha,
            seeded=self._batch_fill_ewma_seeded,
        )
        self._batch_fill_ewma_seeded = True

        prev_b = self.current_batch_size
        prev_t = self.current_flush_timeout_sec
        self._tune_parameters_locked(self._ewma_batch_fill_ratio)
        self._maybe_log_transition_locked(prev_b, prev_t, self._ewma_batch_fill_ratio)
        return batch

    def _extract_batch_locked(self, limit: int) -> List[Any]:
        count = min(len(self._queue), max(0, limit))
        if count == 0:
            return []
        batch = [self._queue.popleft() for _ in range(count)]
        self.total_flushed_records += len(batch)
        self.total_flush_events += 1
        self._last_flush_time = time.monotonic()
        return batch

    def _notify_flush(self, batch: List[Any]) -> None:
        """Invoke the optional flush callback **outside** the engine lock.

        Running it under the lock would let a slow sink write block every
        producer, and would deadlock outright if the callback re-entered the
        engine (e.g. to read ``get_status()`` for metrics).
        """
        if self._on_flush is None or not batch:
            return
        try:
            self._on_flush(batch)
        except Exception:  # noqa: BLE001 — callback failures must not lose data
            self._logger.exception("batch_tuner.on_flush_callback_failed")

    def _tune_parameters_locked(self, fill_ratio: float) -> None:
        cfg = self._config
        transitioned = False

        # The latency throttle outranks the fill branch. Without this guard the
        # controller ratchets: expansion multiplies B by 1.5 while the throttle
        # only multiplies by 0.8, and 1.5 * 0.8 = 1.2 > 1, so one throttle per
        # flush can never claw back one expansion. B climbs to B_max and pins
        # there with sink latency stuck above target.
        has_latency_headroom = (
            self._ewma_write_latency_ms <= cfg.target_write_latency_ms
        )

        if fill_ratio > cfg.fill_high_threshold and has_latency_headroom:
            new_b = min(
                cfg.max_batch_size,
                int(self.current_batch_size * cfg.expand_multiplier),
            )
            new_t = max(
                cfg.min_flush_timeout_sec,
                self.current_flush_timeout_sec * cfg.latency_timeout_reduction,
            )
            if new_b != self.current_batch_size or new_t != self.current_flush_timeout_sec:
                self.current_batch_size = new_b
                self.current_flush_timeout_sec = new_t
                transitioned = True
        elif fill_ratio < cfg.fill_low_threshold:
            new_b = max(
                cfg.min_batch_size,
                int(self.current_batch_size / cfg.shrink_divisor),
            )
            new_t = min(
                cfg.max_flush_timeout_sec,
                self.current_flush_timeout_sec * cfg.timeout_increase,
            )
            if new_b != self.current_batch_size or new_t != self.current_flush_timeout_sec:
                self.current_batch_size = new_b
                self.current_flush_timeout_sec = new_t
                transitioned = True

        if transitioned:
            self.total_tuning_transitions += 1

    def _update_fill_ewma_locked(self) -> None:
        """Maintain the buffer-occupancy gauge (observability, not tuning)."""
        cfg = self._config
        raw = len(self._queue) / float(cfg.queue_capacity)
        self._ewma_fill_ratio = self._ewma_update(
            self._ewma_fill_ratio,
            raw,
            cfg.fill_ewma_alpha,
            seeded=self._fill_ewma_seeded,
        )
        self._fill_ewma_seeded = True

    @staticmethod
    def _ewma_update(
        prev: float, current: float, alpha: float, *, seeded: bool = True
    ) -> float:
        """Standard EWMA step, seeded with the first observation.

        Seeding matters: starting from 0 biases the estimate low for the first
        ~``1/alpha`` samples, which for the latency throttle means the safety
        mechanism stays asleep while the sink is already slow.
        """
        if not seeded or alpha >= 1.0:
            return current
        return alpha * current + (1.0 - alpha) * prev

    def _maybe_log_transition_locked(
        self, prev_b: int, prev_t: float, fill_ratio: float
    ) -> None:
        if self.current_batch_size == prev_b and self.current_flush_timeout_sec == prev_t:
            return
        self._logger.info(
            "batch_tuner.tuned",
            extra={
                "event": "tuning_transition",
                "previous_batch_size": prev_b,
                "new_batch_size": self.current_batch_size,
                "previous_flush_timeout_sec": round(prev_t, 4),
                "new_flush_timeout_sec": round(self.current_flush_timeout_sec, 4),
                "batch_fill_ratio_ewma": round(fill_ratio, 4),
            },
        )
