"""
graceful-shutdown-draining-in-flight-ticks: OS signal trap handler, ingress gate,
and in-flight queue drain manager for zero-data-loss shutdowns.

Design rules encoded here (see references/standards.md for sources):

* The drain deadline is measured on a monotonic clock. Wall-clock time can step
  (NTP correction, DST, VM resume); a stepped clock either aborts the drain early
  or overruns the platform grace period, after which the supervisor sends SIGKILL.
* Items leave the queue only once the sink has accepted them. A failed flush puts
  the batch back so a transient sink outage cannot be reported as a clean exit.
* Consumer offsets are committed only after the sink flush has fully succeeded.
  Committing first converts a crash into silent data loss (at-most-once); flushing
  first yields at-least-once, where a restart replays rather than skips ticks.
"""
from dataclasses import dataclass
import logging
import signal
import threading
import time
from enum import Enum
from typing import Any, Callable, List, Optional

logger = logging.getLogger(__name__)

# Deterministic process exit codes (referenced by references/standards.md).
EXIT_CLEAN = 0
EXIT_INCOMPLETE_DRAIN = 1

# Default time a supervisor allows between SIGTERM and SIGKILL, in seconds.
# Kubernetes: terminationGracePeriodSeconds defaults to 30.
# Docker: "docker stop" defaults to 10 (Linux containers).
# systemd: DefaultTimeoutStopSec defaults to 90.
PLATFORM_GRACE_PERIOD_DEFAULTS_SEC = {
    "kubernetes": 30.0,
    "docker": 10.0,
    "systemd": 90.0,
}


class ShutdownState(Enum):
    RUNNING = "RUNNING"
    DRAINING = "DRAINING"
    FLUSHED = "FLUSHED"
    TERMINATED = "TERMINATED"


@dataclass
class ShutdownReport:
    state: ShutdownState
    initial_queue_size: int
    drained_items_count: int
    drain_duration_sec: float
    is_clean_exit: bool
    message: str
    undrained_items_count: int = 0
    flush_failure_count: int = 0
    offsets_committed: bool = False
    exit_code: int = EXIT_CLEAN


def resolve_drain_timeout(
    grace_period_sec: float,
    pre_stop_sec: float = 0.0,
    exit_overhead_sec: float = 1.0,
) -> float:
    """
    Return the largest drain timeout that still finishes before the supervisor
    escalates to SIGKILL.

    The supervisor's clock, not the process's, decides when the kill lands. On
    Kubernetes the grace-period countdown starts *before* the preStop hook runs
    and the hook must finish before SIGTERM is delivered, so preStop time is
    spent out of the same budget as the drain.

    Args:
        grace_period_sec: Supervisor budget between shutdown initiation and SIGKILL.
        pre_stop_sec: Seconds consumed by a preStop hook (Kubernetes) before SIGTERM.
        exit_overhead_sec: Reserve for offset commit, connection close and interpreter exit.

    Returns:
        Positive drain budget in seconds.

    Raises:
        ValueError: If the inputs leave no room to drain at all.
    """
    if grace_period_sec <= 0:
        raise ValueError(f"grace_period_sec must be positive, got {grace_period_sec}")
    if pre_stop_sec < 0 or exit_overhead_sec < 0:
        raise ValueError("pre_stop_sec and exit_overhead_sec must be non-negative")

    budget = grace_period_sec - pre_stop_sec - exit_overhead_sec
    if budget <= 0:
        raise ValueError(
            f"No drain budget: grace period {grace_period_sec}s is fully consumed by "
            f"preStop ({pre_stop_sec}s) + exit overhead ({exit_overhead_sec}s). "
            f"Raise terminationGracePeriodSeconds or shorten the preStop hook."
        )
    return budget


class GracefulShutdownManager:
    """
    Traps termination signals (SIGTERM, SIGINT), closes the ingress gate, and
    drains in-flight tick queues to the sink before exiting.

    Threading contract:
        Python delivers signals only to the main thread of the main interpreter,
        so register_signal_handlers() must be called from that thread. The handler
        itself only sets flags; the drain runs on whichever thread calls
        drain_queue_and_flush(). If producer threads mutate the queue concurrently,
        pass the lock they hold as queue_lock so batches are detached atomically.
    """

    def __init__(
        self,
        max_drain_timeout_sec: float = 5.0,
        retry_interval_sec: float = 0.05,
    ):
        if max_drain_timeout_sec <= 0:
            raise ValueError(
                f"max_drain_timeout_sec must be positive, got {max_drain_timeout_sec}"
            )
        if retry_interval_sec < 0:
            raise ValueError(
                f"retry_interval_sec must be non-negative, got {retry_interval_sec}"
            )
        self.max_drain_timeout_sec = max_drain_timeout_sec
        self.retry_interval_sec = retry_interval_sec
        self.state = ShutdownState.RUNNING
        self.is_shutdown_requested = False
        self.force_immediate_exit = False
        self._signal_received: Optional[int] = None
        self._signal_count = 0
        self._state_lock = threading.RLock()

    # ------------------------------------------------------------------ signals

    def register_signal_handlers(self) -> bool:
        """
        Register OS SIGINT and SIGTERM listeners.

        Returns:
            True if both handlers were installed, False if this process cannot
            install them (non-main thread, or platform without the signal).
        """
        try:
            signal.signal(signal.SIGINT, self._handle_signal)
            signal.signal(signal.SIGTERM, self._handle_signal)
        except ValueError as exc:
            # signal.signal() raises ValueError outside the main thread of the
            # main interpreter. The caller keeps running unsupervised, so this is
            # a deployment defect worth an ERROR rather than a debug note.
            logger.error(
                "Signal handlers NOT registered (must be the main thread of the "
                "main interpreter): %s. Shutdown will not be graceful.", exc
            )
            return False
        except OSError as exc:
            logger.error("Signal handlers NOT registered by the OS: %s", exc)
            return False

        logger.info("Graceful Shutdown Manager: OS signal handlers registered (SIGINT, SIGTERM).")
        return True

    def _handle_signal(self, signum: int, frame: Any) -> None:
        """Signal handler: records intent only. Never blocks or does I/O-heavy work."""
        try:
            sig_name = signal.Signals(signum).name
        except ValueError:
            sig_name = f"SIG{signum}"
        with self._state_lock:
            self._signal_received = signum
            self._signal_count += 1
            repeat = self._signal_count
            self.is_shutdown_requested = True
            if self.state is ShutdownState.RUNNING:
                self.state = ShutdownState.DRAINING
            if repeat > 1:
                self.force_immediate_exit = True

        if repeat > 1:
            logger.error(
                "Second termination signal %s received during drain - operator "
                "requested immediate exit. In-flight items may be lost.", sig_name
            )
        else:
            logger.warning(
                "Termination signal %s (%d) received! Initiating graceful shutdown...",
                sig_name, signum,
            )

    def trigger_shutdown_manual(self) -> None:
        """Manually triggers graceful shutdown for testing or programmatically."""
        logger.warning("Manual shutdown triggered. Initiating graceful drain...")
        with self._state_lock:
            self.is_shutdown_requested = True
            self.state = ShutdownState.DRAINING

    def is_accepting_ingress(self) -> bool:
        """
        False once shutdown has been requested. Ingress callbacks must consult this
        and reject new ticks; on Kubernetes, endpoint removal happens concurrently
        with SIGTERM, so ticks can still arrive after the signal.
        """
        return not self.is_shutdown_requested

    # -------------------------------------------------------------------- drain

    def _detach_batch(
        self,
        item_queue: List[Any],
        queue_lock: Optional[threading.Lock],
    ) -> List[Any]:
        """Atomically remove and return every currently queued item."""
        if queue_lock is None:
            batch = list(item_queue)
            del item_queue[: len(batch)]
            return batch
        with queue_lock:
            batch = list(item_queue)
            del item_queue[: len(batch)]
            return batch

    def _restore_batch(
        self,
        item_queue: List[Any],
        batch: List[Any],
        queue_lock: Optional[threading.Lock],
    ) -> None:
        """Return an unflushed batch to the head of the queue, preserving order."""
        if queue_lock is None:
            item_queue[:0] = batch
            return
        with queue_lock:
            item_queue[:0] = batch

    def drain_queue_and_flush(
        self,
        item_queue: List[Any],
        flush_callback: Callable[[List[Any]], None],
        commit_offsets_callback: Optional[Callable[[], None]] = None,
        queue_lock: Optional[threading.Lock] = None,
    ) -> ShutdownReport:
        """
        Drain in-flight items from item_queue into flush_callback until the queue is
        empty or max_drain_timeout_sec expires, then commit consumer offsets.

        A batch is removed from the queue only once flush_callback returns without
        raising. If it raises, the batch is restored at the head of the queue and
        retried until the deadline, so a transient sink outage never silently
        destroys ticks and never reports a clean exit.

        commit_offsets_callback runs only if the queue drained completely. Leaving
        offsets uncommitted makes a restart replay the unflushed ticks (at-least-once)
        instead of skipping them.

        Args:
            item_queue: In-flight items as a list, mutated in place. A collections.deque
                is deliberately not accepted: it satisfies MutableSequence but supports
                neither slice deletion nor slice assignment, so batches could not be
                detached or restored atomically. Drain a deque or queue.Queue into a
                list first, or pass that list as the worker's buffer.
            flush_callback: Sink writer. Must raise on failure to be detected.
            commit_offsets_callback: Optional consumer-offset commit, run after a full flush.
            queue_lock: Lock shared with producer threads, if any.

        Returns:
            ShutdownReport describing what was drained, lost, and committed.
        """
        with self._state_lock:
            self.state = ShutdownState.DRAINING
        t_start = time.monotonic()
        initial_size = len(item_queue)
        drained_count = 0
        flush_failure_count = 0
        last_error: Optional[BaseException] = None

        logger.info("Draining queue (%d in-flight items)...", initial_size)

        while len(item_queue) > 0:
            if self.force_immediate_exit:
                logger.error(
                    "Immediate-exit requested; abandoning drain with %d items in flight.",
                    len(item_queue),
                )
                break

            elapsed = time.monotonic() - t_start
            if elapsed >= self.max_drain_timeout_sec:
                logger.error(
                    "Max drain timeout (%ss) exceeded! Remaining %d items were not "
                    "flushed and will be replayed on restart if offsets are uncommitted.",
                    self.max_drain_timeout_sec, len(item_queue),
                )
                break

            batch_to_flush = self._detach_batch(item_queue, queue_lock)
            if not batch_to_flush:
                continue

            try:
                flush_callback(batch_to_flush)
            except Exception as exc:
                # Put the batch back: it is not durable, so it must not be counted
                # as drained nor allowed to vanish.
                self._restore_batch(item_queue, batch_to_flush, queue_lock)
                flush_failure_count += 1
                last_error = exc
                logger.error(
                    "Shutdown flush failed (attempt %d, %d items held): %s",
                    flush_failure_count, len(batch_to_flush), exc,
                )
                remaining = self.max_drain_timeout_sec - (time.monotonic() - t_start)
                if remaining <= 0:
                    break
                time.sleep(min(self.retry_interval_sec, remaining))
            else:
                drained_count += len(batch_to_flush)

        drain_duration = time.monotonic() - t_start
        undrained = len(item_queue)
        is_clean = undrained == 0

        offsets_committed = False
        if is_clean and commit_offsets_callback is not None:
            try:
                commit_offsets_callback()
                offsets_committed = True
            except Exception as exc:
                last_error = exc
                is_clean = False
                logger.error(
                    "Sink flush succeeded but offset commit failed: %s. Ticks are "
                    "durable; the restart will replay them (duplicates possible).", exc,
                )
        elif not is_clean and commit_offsets_callback is not None:
            logger.error(
                "Skipping offset commit: %d items never reached the sink. Committing "
                "now would turn an incomplete drain into permanent data loss.", undrained,
            )

        with self._state_lock:
            self.state = ShutdownState.FLUSHED if is_clean else ShutdownState.TERMINATED

        exit_code = EXIT_CLEAN if is_clean else EXIT_INCOMPLETE_DRAIN
        msg = (
            f"Shutdown completed: Drained {drained_count}/{initial_size} items in "
            f"{drain_duration:.3f}s."
        )
        if not is_clean:
            msg += f" {undrained} items UNDRAINED"
            if last_error is not None:
                msg += f" (last error: {last_error})"
            msg += "."
            logger.error(msg)
        else:
            logger.info(msg)

        return ShutdownReport(
            state=self.state,
            initial_queue_size=initial_size,
            drained_items_count=drained_count,
            drain_duration_sec=round(drain_duration, 4),
            is_clean_exit=is_clean,
            message=msg,
            undrained_items_count=undrained,
            flush_failure_count=flush_failure_count,
            offsets_committed=offsets_committed,
            exit_code=exit_code,
        )
