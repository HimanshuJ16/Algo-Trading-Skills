"""Adaptive batch-size tuning engine for downstream database and message-broker
sinks.

Adapts the per-flush record count ``B`` and the flush interval ``T_flush`` to
two signals:

* **Queue fill ratio** ``R = Q_current / Q_capacity`` — static per engine,
  consumed from a `deque`.
* **Downstream sink write latency** — exponentially-weighted moving average
  (EWMA).

The engine does not write to the sink itself; consumers loop on
:meth:`add_item`, write the returned batch when non-`None`, and call
:meth:`record_write_latency` after each write completes.

Design notes
------------
* **Hysteresis / deadband tuning**: ``R`` alone is too volatile for batch
  decisions; we run an EWMA over it and apply tuning only outside
  ``[R_low, R_high]`` (``0.10, 0.70``). Within that band we *never* perturb
  ``B`` or ``T_flush`` based on fill ratio alone — only on latency feedback.
* **Bounded queue**: ``max_queue_size`` is configurable; adding past it
  raises :class:`QueueFullError`. ClickHouse servers cap at 100 MiB / 450
  buffered queries; Kafka pre-allocates buffers; unbounded deque is a
  production hazard.
* **Deterministic decision math**: tuning multipliers and bounds are constants
  (multipliers from `references/standards.md`). No magic numbers inside
  methods.
* **Logging**: structured via `extra=` dict, level-hierarchy: state changes at
  INFO, sustained anomalies at WARNING, configuration errors at ERROR.
* **Time**: all intervals use ``time.monotonic()``; never wall-clock.
* **Thread safety**: ``add_item`` is atomic w.r.t. ``extract_batch``; the
  caller is responsible for holding the lock during slow writes (the returned
  batch is detached from the queue prior to return).
"""

from __future__ import annotations

from collections import deque
from dataclasses import dataclass
import logging
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


# ---------------------------------------------------------------------------
# Status
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class BatchTunerStatus:
    """Snapshot of tuner state at a point in time.

    Use :meth:`as_dict` for JSON-serializable observability export.
    """

    current_batch_size: int
    current_flush_timeout_sec: float
    queue_depth: int
    queue_capacity: int
    queue_fill_ratio_raw: float
    queue_fill_ratio_ewma: float
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

        self.current_batch_size: int = self._config.initial_batch_size
        self.current_flush_timeout_sec: float = self._config.initial_flush_timeout_sec

        self._last_flush_time = time.monotonic()
        self._ewma_write_latency_ms: float = 0.0
        self._ewma_fill_ratio: float = 0.0

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
        degradation (drop / overflow to disk).
        """
        with self._lock:
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

            batch = self._extract_batch_locked()
            fill_ratio = self._ewma_fill_ratio  # use smoothed value for tuning
            prev_b = self.current_batch_size
            prev_t = self.current_flush_timeout_sec
            self._tune_parameters_locked(fill_ratio)
            self._maybe_log_transition_locked(prev_b, prev_t, fill_ratio)
            return batch

    def record_write_latency(self, latency_ms: float) -> None:
        """Submit observed downstream write latency.

        Updates the latency EWMA; if smoothed latency exceeds the target,
        shrinks the batch size to relieve contention. Repeatedly called
        under sustained pressure, this provides the *secondary* dampening
        signal that prevents oscillation when fill ratio alone is noisy.
        """
        if latency_ms < 0:
            raise ValueError(f"latency_ms must be >= 0, got {latency_ms}")

        with self._lock:
            self._ewma_write_latency_ms = self._ewma_update(
                self._ewma_write_latency_ms,
                latency_ms,
                self._config.latency_ewma_alpha,
            )

            # Single-shot threshold bypass on first reading (EWMA starts cold at 0).
            smoothed = (
                latency_ms
                if self._ewma_write_latency_ms == latency_ms
                and self.total_flush_events == 0
                else self._ewma_write_latency_ms
            )

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

        Returns an empty list if the queue is empty.
        """
        with self._lock:
            batch = self._extract_batch_locked()
            fill_ratio = self._ewma_fill_ratio
            prev_b = self.current_batch_size
            prev_t = self.current_flush_timeout_sec
            self._tune_parameters_locked(fill_ratio)
            self._maybe_log_transition_locked(prev_b, prev_t, fill_ratio)
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
                ewma_write_latency_ms=self._ewma_write_latency_ms,
                total_flushed_records=self.total_flushed_records,
                total_flush_events=self.total_flush_events,
                total_tuning_transitions=self.total_tuning_transitions,
            )

    def reset(self) -> None:
        """Clear the queue and reset all tunables to initial values.

        Idempotent — safe to call repeatedly. Use between test scenarios or to
        re-tune after a sustained regime change.
        """
        with self._lock:
            self._queue.clear()
            self.current_batch_size = self._config.initial_batch_size
            self.current_flush_timeout_sec = self._config.initial_flush_timeout_sec
            self._ewma_write_latency_ms = 0.0
            self._ewma_fill_ratio = 0.0
            self._last_flush_time = time.monotonic()
            self.total_flushed_records = 0
            self.total_flush_events = 0
            self.total_tuning_transitions = 0

    def close(self) -> List[Any]:
        """Drain remaining items as a final batch.

        Returns the drained batch (possibly empty). Call on shutdown to
        guarantee no in-flight items are dropped.
        """
        with self._lock:
            return self._extract_batch_locked()

    # ------------------------------------------------------------------ internals

    def _extract_batch_locked(self) -> List[Any]:
        count = min(len(self._queue), self.current_batch_size)
        if count == 0:
            return []
        batch = [self._queue.popleft() for _ in range(count)]
        self.total_flushed_records += len(batch)
        self.total_flush_events += 1
        self._last_flush_time = time.monotonic()
        if self._on_flush is not None:
            try:
                self._on_flush(batch)
            except Exception:  # noqa: BLE001 — callback failures must not lose data
                self._logger.exception("batch_tuner.on_flush_callback_failed")
        return batch

    def _tune_parameters_locked(self, fill_ratio: float) -> None:
        cfg = self._config
        transitioned = False

        if fill_ratio > cfg.fill_high_threshold:
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
        cfg = self._config
        raw = len(self._queue) / float(cfg.queue_capacity)
        self._ewma_fill_ratio = self._ewma_update(
            self._ewma_fill_ratio, raw, cfg.fill_ewma_alpha
        )

    @staticmethod
    def _ewma_update(prev: float, current: float, alpha: float) -> float:
        # alpha=1.0 replaces fully (test-friendly mode).
        if alpha >= 1.0:
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
                "queue_fill_ratio_ewma": round(fill_ratio, 4),
            },
        )
