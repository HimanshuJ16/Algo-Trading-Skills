"""
producer-consumer-tick-pipeline: symbol-partitioned async tick pipeline with a
zero-blocking WebSocket callback, bounded queue backpressure, in-order per-symbol
processing, and pipeline telemetry.

Threading contract (read this before wiring a broker SDK)
---------------------------------------------------------
``asyncio.Queue`` is **not thread-safe** (CPython asyncio-queue docs). Most Python
broker WebSocket SDKs invoke their tick callback on a *non-asyncio* thread:

* ``pykiteconnect``'s ``KiteTicker`` runs the Twisted reactor, and with
  ``connect(threaded=True)`` that reactor runs on a background daemon thread --
  ``on_ticks`` fires there.
* ``fyers-apiv3``'s ``FyersDataSocket`` runs its socket on a background thread and
  calls ``onmessage`` from it.
* ``alpaca-py``'s stream is asyncio-native and calls its handler *on* the loop.

Calling ``asyncio.Queue.put_nowait`` from a foreign thread does not wake the event
loop: the getter future is completed via ``loop.call_soon``, which appends to the
loop's ready queue without writing to its self-pipe. The consumer therefore does not
run until the loop wakes for some unrelated reason. Measured against the previous
version of this file, a tick pushed while the loop was parked in ``select()`` waited
2.7 s before it was processed.

``on_message`` therefore detects the calling thread and routes foreign-thread
submissions through ``loop.call_soon_threadsafe``. Use :meth:`submit_threadsafe`
when you want that path stated explicitly at the call site.

Bounding the handoff
--------------------
``call_soon_threadsafe`` has no bound of its own, so an unchecked handoff path just
relocates the unbounded queue into the loop's ready list. Producer-side admission
control caps in-flight handoffs at ``max_pending_handoffs`` (default:
``num_workers * maxsize_per_worker``) and counts overflow as a drop.

Partitioning stability
----------------------
Bucketing uses ``zlib.crc32`` rather than the builtin ``hash()``. ``hash()`` of a
``str`` is salted by ``PYTHONHASHSEED`` and differs per process, so a restart -- or a
second process in a multi-process fan-out -- would silently reshuffle symbols across
workers and break the per-symbol ordering guarantee across that boundary.
"""
import asyncio
from dataclasses import dataclass
import inspect
import logging
import threading
import time
from typing import Any, Callable, Dict, List, Optional
import zlib

logger = logging.getLogger(__name__)

__all__ = ["PipelineMetrics", "SymbolPartitionedTickPipeline", "TickPipeline"]

# Enqueued at the tail of each worker queue on graceful shutdown: every tick ahead of
# it is processed first, then the worker exits without waiting for a cancel timeout.
_SHUTDOWN = object()


@dataclass
class PipelineMetrics:
    """Counters for the ingestion path. Mutated only under the pipeline's lock."""

    total_ticks_received: int = 0
    total_ticks_processed: int = 0
    total_ticks_dropped: int = 0
    total_ticks_failed: int = 0
    total_ticks_undrained: int = 0
    max_queue_depth: int = 0
    avg_latency_ms: float = 0.0        # process_fn execution time only
    avg_queue_wait_ms: float = 0.0     # arrival -> start of processing
    max_queue_wait_ms: float = 0.0

    def to_dict(self) -> Dict[str, Any]:
        return {
            "received": self.total_ticks_received,
            "processed": self.total_ticks_processed,
            "dropped": self.total_ticks_dropped,
            "failed": self.total_ticks_failed,
            "undrained": self.total_ticks_undrained,
            "max_queue_depth": self.max_queue_depth,
            "avg_latency_ms": round(self.avg_latency_ms, 3),
            "avg_queue_wait_ms": round(self.avg_queue_wait_ms, 3),
            "max_queue_wait_ms": round(self.max_queue_wait_ms, 3),
        }


class SymbolPartitionedTickPipeline:
    """
    Tick pipeline using stable symbol partitioning across N bounded worker queues.

    Ordering guarantee: every tick for a given symbol lands on the same worker queue,
    and each queue has exactly one consumer, so per-symbol order is preserved. There is
    no ordering guarantee *across* symbols, and none for ticks that were dropped.

    ``on_message`` is the producer entry point and never blocks on a full queue: it
    drops and accounts for the tick instead, leaving the socket read loop free.
    """

    def __init__(
        self,
        maxsize_per_worker: int = 10_000,
        num_workers: int = 4,
        *,
        max_pending_handoffs: Optional[int] = None,
        drop_log_interval_s: float = 1.0,
    ) -> None:
        if num_workers < 1:
            raise ValueError(f"num_workers must be >= 1, got {num_workers}")
        # asyncio.Queue treats maxsize <= 0 as UNBOUNDED, which is precisely the
        # failure mode this skill exists to prevent. Reject it loudly.
        if maxsize_per_worker < 1:
            raise ValueError(
                "maxsize_per_worker must be >= 1 (asyncio.Queue treats <= 0 as "
                f"unbounded), got {maxsize_per_worker}"
            )
        if max_pending_handoffs is not None and max_pending_handoffs < 1:
            raise ValueError("max_pending_handoffs must be >= 1 when provided")

        self.num_workers = num_workers
        self.maxsize = maxsize_per_worker
        self.worker_queues: List[asyncio.Queue] = [
            asyncio.Queue(maxsize=maxsize_per_worker) for _ in range(num_workers)
        ]
        self.worker_tasks: List[asyncio.Task] = []
        self.metrics = PipelineMetrics()
        self.max_pending_handoffs = (
            max_pending_handoffs
            if max_pending_handoffs is not None
            else num_workers * maxsize_per_worker
        )

        self._running = False
        self._lock = threading.Lock()
        self._loop: Optional[asyncio.AbstractEventLoop] = None
        self._loop_thread_id: Optional[int] = None
        self._pending_handoffs = 0
        self._drop_log_interval_s = drop_log_interval_s
        self._last_drop_log_ts = 0.0
        self._drops_since_log = 0

    # ------------------------------------------------------------------ producer

    def _get_worker_index(self, symbol: str) -> int:
        """Stable across processes and restarts, unlike ``hash(str)``."""
        return zlib.crc32(symbol.encode("utf-8")) % self.num_workers

    def on_message(self, symbol: str, raw_tick: Any) -> bool:
        """
        WebSocket callback entry point. Non-blocking; safe to call from any thread.

        Returns ``True`` if the tick was admitted, ``False`` if it was dropped and
        counted. On the cross-thread path ``True`` means "accepted for enqueue" -- the
        worker queue could still be full by the time the handoff runs, in which case
        the drop is counted in ``metrics.total_ticks_dropped`` but is not reflected in
        this return value. Treat the metric, not the return value, as the source of
        truth for drop accounting.
        """
        if not isinstance(symbol, str):
            raise TypeError(f"symbol must be str, got {type(symbol).__name__}")

        arrival_ts = time.monotonic()
        with self._lock:
            self.metrics.total_ticks_received += 1

        if self._loop is not None and threading.get_ident() != self._loop_thread_id:
            return self._handoff(symbol, raw_tick, arrival_ts)
        return self._enqueue(symbol, raw_tick, arrival_ts)

    def submit_threadsafe(self, symbol: str, raw_tick: Any) -> bool:
        """
        Explicit cross-thread producer entry point, for broker SDKs that call back on
        their own thread (Kite's Twisted reactor, the Fyers socket thread).

        Semantics are identical to :meth:`on_message`; use it to make the cross-thread
        handoff visible at the call site. Requires ``start_consumers`` to have run, so
        that the target event loop is known.
        """
        if self._loop is None:
            raise RuntimeError(
                "submit_threadsafe requires a running pipeline; call start_consumers() "
                "from the event loop thread first"
            )
        return self.on_message(symbol, raw_tick)

    def _handoff(self, symbol: str, raw_tick: Any, arrival_ts: float) -> bool:
        """Bounded cross-thread handoff onto the event loop thread."""
        with self._lock:
            loop = self._loop
            if loop is None:
                # stop_consumers() cleared the loop between the thread check and here.
                self._record_drop_locked(symbol, None, "pipeline stopped")
                return False
            if self._pending_handoffs >= self.max_pending_handoffs:
                self._record_drop_locked(symbol, None, "handoff buffer full")
                return False
            self._pending_handoffs += 1

        try:
            loop.call_soon_threadsafe(
                self._enqueue_from_handoff, symbol, raw_tick, arrival_ts
            )
        except RuntimeError:
            # Loop closed between the check and the call -- account for it rather than
            # raising into the broker's callback thread.
            with self._lock:
                self._pending_handoffs -= 1
                self._record_drop_locked(symbol, None, "event loop closed")
            return False
        return True

    def _enqueue_from_handoff(self, symbol: str, raw_tick: Any, arrival_ts: float) -> None:
        with self._lock:
            self._pending_handoffs -= 1
        self._enqueue(symbol, raw_tick, arrival_ts)

    def _enqueue(self, symbol: str, raw_tick: Any, arrival_ts: float) -> bool:
        worker_idx = self._get_worker_index(symbol)
        q = self.worker_queues[worker_idx]
        try:
            q.put_nowait((symbol, raw_tick, arrival_ts))
        except asyncio.QueueFull:
            with self._lock:
                self._record_drop_locked(symbol, worker_idx, "worker queue full")
            return False

        # Sample depth *after* the put, so a queue sitting at capacity reports its true
        # depth rather than capacity - 1.
        depth = q.qsize()
        with self._lock:
            if depth > self.metrics.max_queue_depth:
                self.metrics.max_queue_depth = depth
        return True

    def _record_drop_locked(
        self, symbol: str, worker_idx: Optional[int], reason: str
    ) -> None:
        """
        Count a drop and log at most once per ``drop_log_interval_s``.

        A saturated queue drops every tick that arrives; logging each one turns a
        backlog into a log storm that itself slows the producer.
        """
        self.metrics.total_ticks_dropped += 1
        self._drops_since_log += 1
        now = time.monotonic()
        if self._last_drop_log_ts and now - self._last_drop_log_ts < self._drop_log_interval_s:
            return
        logger.warning(
            "Tick dropped (%s) on worker %s for '%s'; %d drop(s) since last report, "
            "%d total",
            reason,
            "n/a" if worker_idx is None else worker_idx,
            symbol,
            self._drops_since_log,
            self.metrics.total_ticks_dropped,
        )
        self._last_drop_log_ts = now
        self._drops_since_log = 0

    # ------------------------------------------------------------------ consumer

    async def _worker_loop(
        self, worker_id: int, process_fn: Callable[[str, Any], Any]
    ) -> None:
        q = self.worker_queues[worker_id]
        is_async = asyncio.iscoroutinefunction(process_fn)
        # Keep consuming while running, and keep draining a non-empty queue after
        # stop_consumers() flips _running, so a graceful shutdown loses nothing.
        while self._running or not q.empty():
            try:
                symbol, tick, arrival_ts = await q.get()
            except asyncio.CancelledError:
                break
            if symbol is _SHUTDOWN:
                q.task_done()
                break
            try:
                start_proc = time.monotonic()
                if is_async:
                    await process_fn(symbol, tick)
                else:
                    result = process_fn(symbol, tick)
                    # asyncio.iscoroutinefunction() misses a callable object whose
                    # __call__ is async. Awaiting the result keeps such a process_fn
                    # from silently never running.
                    if inspect.isawaitable(result):
                        await result
            except asyncio.CancelledError:
                q.task_done()
                raise
            except Exception:
                with self._lock:
                    self.metrics.total_ticks_failed += 1
                logger.exception(
                    "Error processing tick for '%s' on worker %d", symbol, worker_id
                )
                q.task_done()
            else:
                self._record_processed(
                    queue_wait_ms=(start_proc - arrival_ts) * 1000.0,
                    proc_latency_ms=(time.monotonic() - start_proc) * 1000.0,
                )
                # task_done() runs on every path, including the failure path --
                # skipping it leaves queue.join() waiting forever.
                q.task_done()

    def _record_processed(self, queue_wait_ms: float, proc_latency_ms: float) -> None:
        with self._lock:
            self.metrics.total_ticks_processed += 1
            n = self.metrics.total_ticks_processed
            self.metrics.avg_latency_ms = (
                self.metrics.avg_latency_ms * (n - 1) + proc_latency_ms
            ) / n
            self.metrics.avg_queue_wait_ms = (
                self.metrics.avg_queue_wait_ms * (n - 1) + queue_wait_ms
            ) / n
            if queue_wait_ms > self.metrics.max_queue_wait_ms:
                self.metrics.max_queue_wait_ms = queue_wait_ms

    def start_consumers(self, process_fn: Callable[[str, Any], Any]) -> None:
        """
        Spawn one consumer task per worker queue. Must be called from the event loop
        thread -- the loop identified here is the one cross-thread submissions are
        handed off to.
        """
        if self._running:
            raise RuntimeError("consumers already started")
        self._loop = asyncio.get_running_loop()
        self._loop_thread_id = threading.get_ident()
        self._running = True
        self.worker_tasks = [
            asyncio.create_task(self._worker_loop(i, process_fn), name=f"tick-worker-{i}")
            for i in range(self.num_workers)
        ]

    async def stop_consumers(self, drain: bool = True, drain_timeout: float = 5.0) -> None:
        """
        Stop consuming. With ``drain=True`` (the default) queued ticks are processed
        before the workers exit; anything still queued when ``drain_timeout`` expires is
        cancelled and counted in ``metrics.total_ticks_undrained``.

        Stop producing before calling this. A tick submitted afterwards is buffered in
        its worker queue with no consumer to take it: it stays there until consumers are
        started again, and is counted as undrained on the next shutdown.
        """
        if not self._running and not self.worker_tasks:
            return
        self._running = False

        sentinels = [False] * self.num_workers
        if drain and self.worker_tasks:
            for i, q in enumerate(self.worker_queues):
                try:
                    q.put_nowait((_SHUTDOWN, None, time.monotonic()))
                    sentinels[i] = True
                except asyncio.QueueFull:
                    # Backlogged queue: the worker exits on its own once it has drained,
                    # because _running is already False.
                    pass
            _, pending = await asyncio.wait(self.worker_tasks, timeout=drain_timeout)
            if pending:
                logger.warning(
                    "Drain timed out after %.1fs with %d worker(s) still busy",
                    drain_timeout,
                    len(pending),
                )

        # A worker that consumed its sentinel has finished; one still running has not,
        # so its sentinel is still queued and must not be counted as a lost tick.
        undrained = sum(
            q.qsize() - (1 if sentinels[i] and not self.worker_tasks[i].done() else 0)
            for i, q in enumerate(self.worker_queues)
        )
        if undrained:
            with self._lock:
                self.metrics.total_ticks_undrained += undrained
            logger.warning("Shutdown discarded %d unprocessed tick(s)", undrained)

        for task in self.worker_tasks:
            task.cancel()
        await asyncio.gather(*self.worker_tasks, return_exceptions=True)
        self.worker_tasks = []
        self._loop = None
        self._loop_thread_id = None


class TickPipeline:
    """
    Minimal single-queue pipeline retained for backward compatibility.

    Prefer :class:`SymbolPartitionedTickPipeline`: this class has no symbol
    partitioning, no metrics, no cross-thread handoff, and no drop logging.
    """

    def __init__(self, maxsize: int = 10_000) -> None:
        self.queue: asyncio.Queue = asyncio.Queue(maxsize=maxsize)
        self.dropped = 0

    def on_message(self, raw_tick: Any) -> None:
        try:
            self.queue.put_nowait(raw_tick)
        except asyncio.QueueFull:
            self.dropped += 1

    async def consume(self, process_fn: Callable[[Any], Any]) -> None:
        is_async = asyncio.iscoroutinefunction(process_fn)
        while True:
            tick = await self.queue.get()
            try:
                if is_async:
                    await process_fn(tick)
                else:
                    process_fn(tick)
            finally:
                self.queue.task_done()
