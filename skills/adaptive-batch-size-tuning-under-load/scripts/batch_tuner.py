"""
adaptive-batch-size-tuning-under-load: Dynamic batch size scaling engine and write latency
feedback tuner for high-throughput database sinks.
"""
from dataclasses import dataclass, field
import logging
import math
import time
from typing import Any, Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)


@dataclass
class BatchTunerStatus:
    current_batch_size: int
    current_flush_timeout_sec: float
    queue_fill_ratio: float
    last_write_latency_ms: float
    total_flushed_records: int
    total_flush_events: int


class AdaptiveBatchTunerEngine:
    """
    Dynamically tunes write batch sizes and flush timeouts based on queue fill pressure
    and downstream sink write latency feedback.
    """

    def __init__(
        self,
        min_batch_size: int = 10,
        max_batch_size: int = 1000,
        initial_batch_size: int = 100,
        min_flush_timeout_sec: float = 0.05,
        max_flush_timeout_sec: float = 1.0,
        target_write_latency_ms: float = 50.0,
    ):
        self.min_batch_size = min_batch_size
        self.max_batch_size = max_batch_size
        self.current_batch_size = initial_batch_size

        self.min_flush_timeout_sec = min_flush_timeout_sec
        self.max_flush_timeout_sec = max_flush_timeout_sec
        self.current_flush_timeout_sec = 0.2

        self.target_write_latency_ms = target_write_latency_ms

        self.queue: List[Any] = []
        self.last_flush_time = time.time()
        self.last_write_latency_ms = 0.0
        self.total_flushed_records = 0
        self.total_flush_events = 0

    def add_item(self, item: Any, queue_capacity: int = 2000) -> Optional[List[Any]]:
        """
        Adds an item to the buffer. Evaluates if batch size or timeout condition is met,
        and returns batch for flushing if ready.
        """
        self.queue.append(item)
        now = time.time()

        queue_fill_ratio = len(self.queue) / float(queue_capacity)
        elapsed_sec = now - self.last_flush_time

        # Check if flush threshold is reached
        should_flush = (
            len(self.queue) >= self.current_batch_size
            or elapsed_sec >= self.current_flush_timeout_sec
        )

        if should_flush and len(self.queue) > 0:
            batch = self._extract_batch()
            self._tune_parameters(queue_fill_ratio)
            return batch

        return None

    def record_write_latency(self, latency_ms: float) -> None:
        """Records downstream sink write latency to apply feedback throttling."""
        self.last_write_latency_ms = latency_ms

        # If DB latency is high, reduce batch size to relieve DB lock contention
        if latency_ms > self.target_write_latency_ms:
            new_size = max(self.min_batch_size, int(self.current_batch_size * 0.8))
            logger.warning(
                f"DB Latency Breach ({latency_ms:.1f}ms > {self.target_write_latency_ms:.1f}ms). "
                f"Throttling batch size {self.current_batch_size} -> {new_size}"
            )
            self.current_batch_size = new_size

    def _extract_batch(self) -> List[Any]:
        count = min(len(self.queue), self.current_batch_size)
        batch = self.queue[:count]
        self.queue = self.queue[count:]

        self.total_flushed_records += len(batch)
        self.total_flush_events += 1
        self.last_flush_time = time.time()
        return batch

    def _tune_parameters(self, queue_fill_ratio: float) -> None:
        """Adapts batch size and flush timeout based on queue fill ratio."""
        if queue_fill_ratio > 0.70:
            # High queue pressure -> Expand batch size, reduce timeout to maximize throughput
            self.current_batch_size = min(self.max_batch_size, int(self.current_batch_size * 1.5))
            self.current_flush_timeout_sec = max(self.min_flush_timeout_sec, self.current_flush_timeout_sec * 0.8)
        elif queue_fill_ratio < 0.10:
            # Low queue pressure -> Shrink batch size, extend timeout to reduce latency & idle writes
            self.current_batch_size = max(self.min_batch_size, int(self.current_batch_size / 1.2))
            self.current_flush_timeout_sec = min(self.max_flush_timeout_sec, self.current_flush_timeout_sec * 1.2)

    def get_status(self, queue_capacity: int = 2000) -> BatchTunerStatus:
        ratio = len(self.queue) / float(queue_capacity)
        return BatchTunerStatus(
            current_batch_size=self.current_batch_size,
            current_flush_timeout_sec=round(self.current_flush_timeout_sec, 4),
            queue_fill_ratio=round(ratio, 4),
            last_write_latency_ms=self.last_write_latency_ms,
            total_flushed_records=self.total_flushed_records,
            total_flush_events=self.total_flush_events,
        )
