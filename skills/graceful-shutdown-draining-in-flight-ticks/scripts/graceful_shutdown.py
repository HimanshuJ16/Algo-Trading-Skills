"""
graceful-shutdown-draining-in-flight-ticks: OS signal trap handler, ingress stream closer,
and in-flight queue drain manager for zero-data-loss shutdowns.
"""
from dataclasses import dataclass, field
import logging
import queue
import signal
import sys
import time
from enum import Enum
from typing import Any, Callable, Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)


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


class GracefulShutdownManager:
    """
    Traps termination signals (SIGTERM, SIGINT), halts ingress streams,
    and drains in-flight tick queues to completion before exiting.
    """

    def __init__(self, max_drain_timeout_sec: float = 5.0):
        self.max_drain_timeout_sec = max_drain_timeout_sec
        self.state = ShutdownState.RUNNING
        self.is_shutdown_requested = False
        self._signal_received: Optional[int] = None

    def register_signal_handlers() -> None:
        """Registers OS SIGINT and SIGTERM signal listeners."""
        try:
            signal.signal(signal.SIGINT, self._handle_signal)
            signal.signal(signal.SIGTERM, self._handle_signal)
            logger.info("Graceful Shutdown Manager: OS signal handlers registered (SIGINT, SIGTERM).")
        except Exception as exc:
            logger.warning(f"Could not register signal handlers (e.g. non-main thread): {exc}")

    def _handle_signal(self, signum: int, frame: Any) -> None:
        sig_name = "SIGTERM" if signum == signal.SIGTERM else "SIGINT"
        logger.warning(f"Termination signal {sig_name} ({signum}) received! Initiating graceful shutdown...")
        self.is_shutdown_requested = True
        self.state = ShutdownState.DRAINING

    def trigger_shutdown_manual(self) -> None:
        """Manually triggers graceful shutdown for testing or programmatically."""
        logger.warning("Manual shutdown triggered. Initiating graceful drain...")
        self.is_shutdown_requested = True
        self.state = ShutdownState.DRAINING

    def drain_queue_and_flush(
        self,
        item_queue: List[Any],
        flush_callback: Callable[[List[Any]], None],
    ) -> ShutdownReport:
        """
        Drains in-flight items from item_queue into flush_callback until queue is empty
        or max_drain_timeout_sec expires.
        """
        self.state = ShutdownState.DRAINING
        t_start = time.time()
        initial_size = len(item_queue)
        drained_count = 0

        logger.info(f"Draining queue ({initial_size} in-flight items)...")

        while len(item_queue) > 0:
            elapsed = time.time() - t_start
            if elapsed >= self.max_drain_timeout_sec:
                logger.error(
                    f"Max drain timeout ({self.max_drain_timeout_sec}s) exceeded! "
                    f"Remaining {len(item_queue)} items will be forcibly dropped."
                )
                break

            # Drain batch
            batch_to_flush = list(item_queue)
            item_queue.clear()
            try:
                flush_callback(batch_to_flush)
                drained_count += len(batch_to_flush)
            except Exception as exc:
                logger.error(f"Error during shutdown flush callback: {exc}")

        t_end = time.time()
        drain_duration = t_end - t_start
        is_clean = (len(item_queue) == 0)

        self.state = ShutdownState.FLUSHED if is_clean else ShutdownState.TERMINATED
        msg = f"Shutdown completed: Drained {drained_count}/{initial_size} items in {drain_duration:.3f}s."
        logger.info(msg)

        return ShutdownReport(
            state=self.state,
            initial_queue_size=initial_size,
            drained_items_count=drained_count,
            drain_duration_sec=round(drain_duration, 4),
            is_clean_exit=is_clean,
            message=msg,
        )
