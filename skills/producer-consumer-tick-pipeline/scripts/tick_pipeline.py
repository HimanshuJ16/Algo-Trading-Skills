"""
producer-consumer-tick-pipeline: Production-grade symbol-partitioned async tick pipeline,
zero-blocking WebSocket callback handler, bounded queue backpressure protection,
in-order tick processing guarantees, and pipeline telemetry metrics.
"""
import asyncio
from dataclasses import dataclass, field
import logging
import time
from typing import Any, Callable, Dict, List, Optional

logger = logging.getLogger(__name__)


@dataclass
class PipelineMetrics:
    total_ticks_received: int = 0
    total_ticks_processed: int = 0
    total_ticks_dropped: int = 0
    max_queue_depth: int = 0
    avg_latency_ms: float = 0.0

    def to_dict(self) -> Dict[str, Any]:
        return {
            "received": self.total_ticks_received,
            "processed": self.total_ticks_processed,
            "dropped": self.total_ticks_dropped,
            "max_queue_depth": self.max_queue_depth,
            "avg_latency_ms": round(self.avg_latency_ms, 3),
        }


class SymbolPartitionedTickPipeline:
    """
    Production tick pipeline using symbol-hash partitioning across N bounded worker queues.
    Guarantees strict in-order tick processing for any single symbol while scaling CPU throughput.
    """

    def __init__(self, maxsize_per_worker: int = 10_000, num_workers: int = 4):
        self.num_workers = num_workers
        self.maxsize = maxsize_per_worker
        self.worker_queues: List[asyncio.Queue] = [
            asyncio.Queue(maxsize=maxsize_per_worker) for _ in range(num_workers)
        ]
        self.worker_tasks: List[asyncio.Task] = []
        self.metrics = PipelineMetrics()
        self._running = False

    def _get_worker_index(self, symbol: str) -> int:
        return abs(hash(symbol)) % self.num_workers

    def on_message(self, symbol: str, raw_tick: Any) -> bool:
        """
        WebSocket callback entry point. Must be non-blocking (< 0.1ms).
        Enqueues raw tick to symbol-partitioned worker queue.
        """
        self.metrics.total_ticks_received += 1
        worker_idx = self._get_worker_index(symbol)
        q = self.worker_queues[worker_idx]

        depth = q.qsize()
        if depth > self.metrics.max_queue_depth:
            self.metrics.max_queue_depth = depth

        try:
            # Timestamp arrival for latency calculation
            payload = (symbol, raw_tick, time.monotonic())
            q.put_nowait(payload)
            return True
        except asyncio.QueueFull:
            self.metrics.total_ticks_dropped += 1
            logger.warning(f"Tick Pipeline Queue FULL on worker {worker_idx}! Dropped tick for '{symbol}'")
            return False

    async def _worker_loop(self, worker_id: int, process_fn: Callable[[str, Any], Any]):
        q = self.worker_queues[worker_id]
        while self._running:
            try:
                symbol, tick, arrival_ts = await q.get()
                start_proc = time.monotonic()

                # Execute strategy tick processing
                if asyncio.iscoroutinefunction(process_fn):
                    await process_fn(symbol, tick)
                else:
                    process_fn(symbol, tick)

                proc_lat = (time.monotonic() - start_proc) * 1000.0
                self.metrics.total_ticks_processed += 1

                # Update rolling average latency
                n = self.metrics.total_ticks_processed
                self.metrics.avg_latency_ms = (
                    (self.metrics.avg_latency_ms * (n - 1) + proc_lat) / n if n > 0 else proc_lat
                )

                q.task_done()
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"Error processing tick on worker {worker_id}: {e}")

    def start_consumers(self, process_fn: Callable[[str, Any], Any]):
        self._running = True
        self.worker_tasks = [
            asyncio.create_task(self._worker_loop(i, process_fn)) for i in range(self.num_workers)
        ]

    async def stop_consumers(self):
        self._running = False
        for task in self.worker_tasks:
            task.cancel()
        await asyncio.gather(*self.worker_tasks, return_exceptions=True)


# Backward compatibility class
class TickPipeline:

    def __init__(self, maxsize=10_000):
        self.queue: asyncio.Queue = asyncio.Queue(maxsize=maxsize)
        self.dropped = 0

    def on_message(self, raw_tick):
        try:
            self.queue.put_nowait(raw_tick)
        except asyncio.QueueFull:
            self.dropped += 1

    async def consume(self, process_fn):
        while True:
            tick = await self.queue.get()
            if asyncio.iscoroutinefunction(process_fn):
                await process_fn(tick)
            else:
                process_fn(tick)
            self.queue.task_done()
