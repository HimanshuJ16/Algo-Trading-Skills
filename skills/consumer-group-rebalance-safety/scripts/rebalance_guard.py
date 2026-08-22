"""In-process safety guard around Kafka consumer group rebalance callbacks.

This module does **not** talk to a broker. It is the state machine that sits
between a Kafka client's rebalance listener and a trading engine, and it
enforces the four properties that stop a rebalance from double-executing
orders:

1. **Fencing** - a partition is marked inactive the instant it is revoked or
   lost, so the processing thread stops accepting work for it.
2. **Drain-then-commit ordering** - in-flight work is flushed to the executor
   *before* offsets are committed. A commit that precedes a successful flush
   silently discards work.
3. **Correct commit offsets** - Kafka's committed offset is the offset of the
   *next* message to consume, i.e. ``last_processed_offset + 1``. Committing the
   last processed offset itself replays that message on the new owner.
4. **Lost-partition handling** - ``on_partitions_lost`` fences and discards
   *without* committing, because the partitions are already owned by another
   member.

Offset commits and downstream flushes are performed through caller-supplied
callables so that the ordering and failure handling live here rather than being
re-implemented (and re-broken) in every consumer loop.
"""

from __future__ import annotations

import logging
import threading
import time
from collections import OrderedDict
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, Iterable, List, Optional, Set

logger = logging.getLogger(__name__)

# Called with {partition: next_offset_to_consume}. Must commit synchronously and
# raise on failure.
CommitFn = Callable[[Dict[int, int]], None]

# Called with (partition, buffered_messages). Must complete the downstream side
# effect synchronously and raise on failure.
FlushFn = Callable[[int, List["StreamMessage"]], None]

DEFAULT_MAX_IDEMPOTENCY_KEYS = 100_000


class RebalanceGuardError(Exception):
    """Base class for every error raised by this module."""


class PartitionRevokedException(RebalanceGuardError):
    """Raised when an application attempts to process messages on a fenced partition."""


class DuplicateMessageException(RebalanceGuardError):
    """Raised when a message is recognised as a redelivery of already-processed work."""


class OffsetRegressionError(DuplicateMessageException):
    """Raised when a message arrives at or below the last processed offset.

    Kafka offsets increase strictly within a partition, so a non-increasing
    offset means the consumer was re-fed from a stale position. Accepting it
    would drag the commit pointer backwards and replay everything after it.
    Subclasses :class:`DuplicateMessageException` because it is a redelivery.
    """


class OffsetCommitError(RebalanceGuardError):
    """Raised when the synchronous revocation flush or commit did not succeed.

    Every affected partition is still fenced and its state discarded when this
    is raised; the failure means those offsets are **not** durable and the work
    will be redelivered to whichever member takes the partitions over.
    """

    def __init__(self, failures: Dict[int, str]) -> None:
        self.failures = dict(failures)
        detail = ", ".join(f"p{p}: {err}" for p, err in sorted(self.failures.items()))
        super().__init__(
            f"revocation commit failed for {len(self.failures)} partition(s): {detail}"
        )


@dataclass
class StreamMessage:
    """One consumed record carrying an application-level idempotency key."""

    partition: int
    offset: int
    idempotency_key: str  # Unique order_id / event_id
    payload: Dict[str, Any] = field(default_factory=dict)

    def validate(self) -> None:
        """Reject structurally impossible records before they reach the guard's state."""
        if (
            not isinstance(self.partition, int)
            or isinstance(self.partition, bool)
            or self.partition < 0
        ):
            raise ValueError(f"partition must be a non-negative int, got {self.partition!r}")
        if (
            not isinstance(self.offset, int)
            or isinstance(self.offset, bool)
            or self.offset < 0
        ):
            raise ValueError(f"offset must be a non-negative int, got {self.offset!r}")
        if not isinstance(self.idempotency_key, str) or not self.idempotency_key.strip():
            raise ValueError("idempotency_key must be a non-empty string")


class ConsumerGroupRebalanceGuard:
    """Fences revoked partitions, drains in-flight work, and commits correct offsets.

    Every public method is guarded by a re-entrant lock: the rebalance callbacks
    run on the client's poll thread while ``process_message`` typically runs on a
    worker thread, and the two mutate the same state.

    The fence is checked at the *start* of ``process_message``. A message that
    passed the check can still be in flight when revocation begins - that is
    precisely why revocation drains the buffer through ``flush_fn`` instead of
    assuming the worker is idle.

    Args:
        commit_fn: Synchronous offset commit. Receives ``{partition: next_offset}``
            and must raise on failure. When ``None`` the guard performs **no**
            broker commit and logs a warning; offsets are then not durable.
        flush_fn: Synchronous drain of buffered messages, called per partition
            before its offsets are committed. When ``None`` the buffer is simply
            discarded, which is only correct if the executor is already
            synchronous with ``process_message``.
        rebalance_storm_threshold_count: Number of rebalances within the window
            that raises the storm signal (the comparison is ``>=``).
        rebalance_window_sec: Rolling window, measured on a monotonic clock.
        max_idempotency_keys: Upper bound on the dedupe cache. Oldest keys are
            evicted first; eviction reopens a duplicate window for very old keys.
    """

    def __init__(
        self,
        commit_fn: Optional[CommitFn] = None,
        flush_fn: Optional[FlushFn] = None,
        rebalance_storm_threshold_count: int = 3,
        rebalance_window_sec: float = 60.0,
        max_idempotency_keys: int = DEFAULT_MAX_IDEMPOTENCY_KEYS,
    ) -> None:
        if rebalance_storm_threshold_count < 1:
            raise ValueError("rebalance_storm_threshold_count must be >= 1")
        if rebalance_window_sec <= 0:
            raise ValueError("rebalance_window_sec must be > 0")
        if max_idempotency_keys < 1:
            raise ValueError("max_idempotency_keys must be >= 1")

        self._commit_fn = commit_fn
        self._flush_fn = flush_fn
        self.rebalance_storm_threshold_count = rebalance_storm_threshold_count
        self.rebalance_window_sec = rebalance_window_sec
        self.max_idempotency_keys = max_idempotency_keys

        self._lock = threading.RLock()
        self.active_partitions: Set[int] = set()
        # OrderedDict used as a bounded LRU; ``key in cache`` still reads naturally.
        self.processed_idempotency_keys: "OrderedDict[str, None]" = OrderedDict()
        self.in_flight_buffer: Dict[int, List[StreamMessage]] = {}
        # Highest offset handed to the executor, per partition.
        self.last_processed_offsets: Dict[int, int] = {}
        # Offsets a commit_fn confirmed durable: {partition: next_offset_to_consume}.
        self.committed_offsets: Dict[int, int] = {}
        self.rebalance_timestamps: List[float] = []
        self._rebalance_pending_assignment = False

        if commit_fn is None:
            logger.warning(
                "ConsumerGroupRebalanceGuard constructed without commit_fn: revocation will "
                "fence and drain but will NOT commit offsets. Offsets are not durable."
            )

    # ------------------------------------------------------------------
    # Rebalance storm accounting
    # ------------------------------------------------------------------
    def _prune_rebalance_window(self) -> None:
        """Drop rebalance events that have fallen outside the rolling window.

        Uses ``time.monotonic`` deliberately: a wall-clock step (NTP correction,
        VM resume) would otherwise fabricate or suppress storm alerts.
        """
        now = time.monotonic()
        self.rebalance_timestamps = [
            ts for ts in self.rebalance_timestamps if now - ts <= self.rebalance_window_sec
        ]

    def _record_rebalance(self) -> None:
        self.rebalance_timestamps.append(time.monotonic())
        self._prune_rebalance_window()

    def _storm_active(self) -> bool:
        return len(self.rebalance_timestamps) >= self.rebalance_storm_threshold_count

    def is_rebalance_storm(self) -> bool:
        """True when the rolling window holds at least the storm threshold of rebalances.

        Exposed as a value so callers can degrade (pause new orders, alert, widen
        quotes) rather than having to scrape log output.
        """
        with self._lock:
            self._prune_rebalance_window()
            return self._storm_active()

    def _signal_storm_if_needed(self) -> bool:
        storm = self._storm_active()
        if storm:
            logger.error(
                "REBALANCE STORM DETECTED: %d rebalances in the last %.1fs. Consumer group is "
                "unstable; check max.poll.interval.ms, session.timeout.ms and worker health.",
                len(self.rebalance_timestamps),
                self.rebalance_window_sec,
            )
        return storm

    # ------------------------------------------------------------------
    # Rebalance lifecycle callbacks
    # ------------------------------------------------------------------
    def on_partitions_assigned(self, partitions: Iterable[int]) -> bool:
        """Activate newly assigned partitions. Returns True if a storm is in progress.

        Counted as a rebalance only when no revocation/loss has been recorded
        since the previous assignment: under the eager protocol one rebalance
        fires revoke *and* assign, and counting both would double the rate.

        This pairing is a deliberate approximation. Under cooperative
        rebalancing a revoke-only rebalance followed later by an assign-only
        rebalance is counted once, so the counter undercounts there. It is a
        churn indicator for alerting, not an exact rebalance count - do not use
        it for billing, SLA arithmetic, or anything that must reconcile with
        broker-side group metrics.
        """
        parts = self._normalize_partitions(partitions)
        with self._lock:
            if not self._rebalance_pending_assignment:
                self._record_rebalance()
            self._rebalance_pending_assignment = False

            for p in parts:
                self.active_partitions.add(p)
                self.in_flight_buffer.setdefault(p, [])
            logger.info(
                "Partitions assigned: %s. Active: %s", parts, sorted(self.active_partitions)
            )
            return self._signal_storm_if_needed()

    def on_partitions_revoked(self, partitions: Iterable[int]) -> bool:
        """Fence, drain, then synchronously commit the revoked partitions.

        The ordering is not negotiable: every partition is fenced first, so a
        flush or commit failure can never leave a partition still accepting work.
        Only then is each partition drained via ``flush_fn`` and committed via
        ``commit_fn``. A partition whose flush fails is **not** committed - its
        work never reached the executor, so it must be redelivered.

        Returns:
            True if a rebalance storm is in progress.

        Raises:
            OffsetCommitError: if any partition failed to flush or commit. All
                partitions are fenced and pruned regardless.
        """
        parts = self._normalize_partitions(partitions)
        with self._lock:
            logger.warning("Partitions revoked: %s. Fencing, draining, committing.", parts)
            self._record_rebalance()
            self._rebalance_pending_assignment = True

            # Step 1: fence every partition before any I/O.
            for p in parts:
                self.active_partitions.discard(p)

            # Steps 2 and 3: drain, then commit.
            failures: Dict[int, str] = {}
            to_commit: Dict[int, int] = {}
            for p in parts:
                buffered = self.in_flight_buffer.get(p, [])
                if buffered and self._flush_fn is not None:
                    try:
                        self._flush_fn(p, list(buffered))
                    except Exception as exc:  # noqa: BLE001 - reported, never swallowed
                        failures[p] = f"flush failed: {exc}"
                        logger.error(
                            "Partition %d flush failed; offsets will not be committed: %s", p, exc
                        )
                        continue
                if p in self.last_processed_offsets:
                    # Kafka commits the offset of the NEXT message to consume.
                    to_commit[p] = self.last_processed_offsets[p] + 1

            if to_commit:
                if self._commit_fn is None:
                    logger.warning(
                        "No commit_fn configured; offsets %s were NOT committed for revoked "
                        "partitions. Expect redelivery on the new owner.",
                        to_commit,
                    )
                else:
                    try:
                        self._commit_fn(dict(to_commit))
                        self.committed_offsets.update(to_commit)
                        logger.info(
                            "Synchronously committed offsets on revocation: %s", to_commit
                        )
                    except Exception as exc:  # noqa: BLE001 - reported, never swallowed
                        for p in to_commit:
                            failures.setdefault(p, f"commit failed: {exc}")
                        logger.error(
                            "Synchronous revocation commit failed for %s: %s",
                            sorted(to_commit),
                            exc,
                        )

            self._discard_partition_state(parts)
            storm = self._signal_storm_if_needed()

            if failures:
                raise OffsetCommitError(failures)
            return storm

    def on_partitions_lost(self, partitions: Iterable[int]) -> bool:
        """Fence and discard partitions whose ownership was already lost.

        Deliberately performs no flush and no commit. By the time this fires the
        partitions belong to another member, so a commit is at best rejected and
        at worst overwrites the new owner's progress. Buffered work is dropped
        and will be redelivered there.
        """
        parts = self._normalize_partitions(partitions)
        with self._lock:
            dropped = sum(len(self.in_flight_buffer.get(p, [])) for p in parts)
            logger.error(
                "Partitions LOST (ownership already gone): %s. Fencing and discarding %d "
                "buffered message(s) without committing.",
                parts,
                dropped,
            )
            self._record_rebalance()
            self._rebalance_pending_assignment = True
            for p in parts:
                self.active_partitions.discard(p)
            self._discard_partition_state(parts)
            return self._signal_storm_if_needed()

    def _discard_partition_state(self, partitions: List[int]) -> None:
        """Drop per-partition state so long-lived consumers do not grow unboundedly."""
        for p in partitions:
            self.in_flight_buffer.pop(p, None)
            self.last_processed_offsets.pop(p, None)

    @staticmethod
    def _normalize_partitions(partitions: Iterable[int]) -> List[int]:
        parts = list(partitions)
        for p in parts:
            if not isinstance(p, int) or isinstance(p, bool) or p < 0:
                raise ValueError(f"partition must be a non-negative int, got {p!r}")
        return parts

    # ------------------------------------------------------------------
    # Message path
    # ------------------------------------------------------------------
    def is_partition_active(self, partition: int) -> bool:
        """True if this worker currently owns ``partition`` and may execute on it."""
        with self._lock:
            return partition in self.active_partitions

    def process_message(self, message: StreamMessage) -> None:
        """Admit one message for execution, or reject it.

        Checks run fence-first, so a revoked partition never executes even when
        the message would also have been rejected as a duplicate.

        Raises:
            ValueError: the message is structurally invalid.
            PartitionRevokedException: the partition is not currently owned.
            DuplicateMessageException: the idempotency key was already processed.
            OffsetRegressionError: the offset is at or below the last processed
                offset for that partition (a replay from a stale position).
        """
        message.validate()
        with self._lock:
            if message.partition not in self.active_partitions:
                raise PartitionRevokedException(
                    f"Cannot process message on partition {message.partition}: revoked/fenced."
                )

            if message.idempotency_key in self.processed_idempotency_keys:
                raise DuplicateMessageException(
                    f"Duplicate message for key {message.idempotency_key}; dropping to prevent "
                    "double execution."
                )

            last = self.last_processed_offsets.get(message.partition)
            if last is not None and message.offset <= last:
                raise OffsetRegressionError(
                    f"Offset {message.offset} on partition {message.partition} is not above the "
                    f"last processed offset {last}; refusing to move the commit pointer backwards."
                )

            self._remember_key(message.idempotency_key)
            self.in_flight_buffer.setdefault(message.partition, []).append(message)
            self.last_processed_offsets[message.partition] = message.offset
            logger.debug(
                "Processed %s on partition %d at offset %d",
                message.idempotency_key,
                message.partition,
                message.offset,
            )

    def _remember_key(self, key: str) -> None:
        """Record an idempotency key, evicting the oldest once the cap is reached.

        The cache is process-local: it stops *this* worker re-executing a
        redelivery and does nothing about a different worker that takes the
        partition over. Cross-worker deduplication needs a shared store.
        """
        self.processed_idempotency_keys[key] = None
        while len(self.processed_idempotency_keys) > self.max_idempotency_keys:
            evicted, _ = self.processed_idempotency_keys.popitem(last=False)
            logger.debug(
                "Evicted idempotency key %s (cache cap %d)", evicted, self.max_idempotency_keys
            )
