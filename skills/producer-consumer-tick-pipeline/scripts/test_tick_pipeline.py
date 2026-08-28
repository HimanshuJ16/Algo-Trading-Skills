"""
Unit tests for producer-consumer-tick-pipeline skill.

Tests:
1. Symbol-partitioned worker queue routing (in-order tick sequence per symbol).
2. Cross-thread submission from a broker SDK callback thread wakes the consumer
   promptly (regression: a foreign-thread put_nowait on an asyncio.Queue left ticks
   parked until the loop happened to wake for an unrelated reason).
3. Partition index is stable across processes (regression: builtin hash(str) is
   salted per process by PYTHONHASHSEED).
4. Constructor rejects unbounded / degenerate configuration.
5. Queue overflow drop accounting, and rate-limited drop logging.
6. A failing process_fn is counted, does not kill the worker, and does not leave
   queue.join() hanging.
7. Graceful drain on shutdown vs. explicit drain=False.
8. Telemetry: max queue depth at capacity, queue-wait latency separate from
   processing latency.
9. A callable object with an async __call__ is awaited, not silently skipped.
10. Backward compatibility with TickPipeline class.
"""
import asyncio
import logging
import os
import subprocess
import sys
import threading
import time
import unittest
import zlib

from tick_pipeline import PipelineMetrics, SymbolPartitionedTickPipeline, TickPipeline


class TestProducerConsumerTickPipeline(unittest.IsolatedAsyncioTestCase):

    async def test_symbol_partitioned_pipeline(self):
        pipeline = SymbolPartitionedTickPipeline(maxsize_per_worker=100, num_workers=2)
        processed_ticks = []

        async def dummy_process(symbol, tick):
            processed_ticks.append((symbol, tick))

        pipeline.start_consumers(dummy_process)

        for i in range(10):
            pipeline.on_message("NIFTY", f"NIFTY_TICK_{i}")
            pipeline.on_message("BANKNIFTY", f"BANKNIFTY_TICK_{i}")

        await pipeline.stop_consumers()

        self.assertEqual(len(processed_ticks), 20)
        self.assertEqual(pipeline.metrics.total_ticks_received, 20)
        self.assertEqual(pipeline.metrics.total_ticks_processed, 20)
        self.assertEqual(pipeline.metrics.total_ticks_dropped, 0)
        self.assertEqual(pipeline.metrics.total_ticks_undrained, 0)

        nifty_seq = [t[1] for t in processed_ticks if t[0] == "NIFTY"]
        self.assertEqual(nifty_seq, [f"NIFTY_TICK_{i}" for i in range(10)])

    async def test_cross_thread_submission_wakes_consumer(self):
        """A tick pushed from a broker callback thread must be processed immediately.

        The event loop here is parked on ``ev.wait()`` with nothing else scheduled --
        exactly the state a live bot is in between ticks. Pushing straight onto an
        asyncio.Queue from another thread leaves the tick sitting there; only the
        call_soon_threadsafe handoff wakes the loop.
        """
        pipeline = SymbolPartitionedTickPipeline(maxsize_per_worker=10, num_workers=1)
        ev = asyncio.Event()
        seen = []

        async def proc(symbol, tick):
            seen.append((symbol, tick, time.monotonic()))
            ev.set()

        pipeline.start_consumers(proc)
        await asyncio.sleep(0.05)  # let workers park on queue.get()

        pushed = []

        def broker_callback_thread():
            time.sleep(0.1)  # fire while the loop is idle, not mid-iteration
            pushed.append(time.monotonic())
            pipeline.submit_threadsafe("RELIANCE", "TICK")

        t = threading.Thread(target=broker_callback_thread)
        t.start()
        try:
            await asyncio.wait_for(ev.wait(), timeout=2.0)
        except asyncio.TimeoutError:  # pragma: no cover - the bug this test pins
            self.fail("cross-thread tick was never processed: consumer not woken")
        finally:
            t.join()

        wake_delay = seen[0][2] - pushed[0]
        self.assertLess(
            wake_delay, 0.5, f"consumer woke {wake_delay:.3f}s after the cross-thread push"
        )
        await pipeline.stop_consumers()
        self.assertEqual(pipeline.metrics.total_ticks_processed, 1)

    async def test_partition_index_is_stable_across_processes(self):
        """hash(str) is salted per process; the partition function must not be."""
        pipeline = SymbolPartitionedTickPipeline(maxsize_per_worker=10, num_workers=4)
        # Independently derived expectation, not a restatement of the implementation.
        self.assertEqual(pipeline._get_worker_index("RELIANCE"), zlib.crc32(b"RELIANCE") % 4)

        script = (
            "import sys; sys.path.insert(0, %r);"
            "from tick_pipeline import SymbolPartitionedTickPipeline as P;"
            "p = P(maxsize_per_worker=10, num_workers=4);"
            "print(p._get_worker_index('RELIANCE'), p._get_worker_index('NIFTY24DECFUT'))"
            % os.path.dirname(os.path.abspath(__file__))
        )
        results = []
        for seed in ("0", "1", "12345"):
            env = dict(os.environ, PYTHONHASHSEED=seed)
            out = subprocess.run(
                [sys.executable, "-c", script], capture_output=True, text=True, env=env
            )
            self.assertEqual(out.returncode, 0, out.stderr)
            results.append(out.stdout.strip())
        self.assertEqual(
            len(set(results)), 1, f"partitioning differs across hash seeds: {results}"
        )

    async def test_rejects_unbounded_and_degenerate_config(self):
        # asyncio.Queue treats maxsize <= 0 as unbounded -- the OOM failure mode this
        # skill exists to prevent, so it must not be silently accepted.
        with self.assertRaises(ValueError):
            SymbolPartitionedTickPipeline(maxsize_per_worker=0, num_workers=2)
        with self.assertRaises(ValueError):
            SymbolPartitionedTickPipeline(maxsize_per_worker=-1, num_workers=2)
        with self.assertRaises(ValueError):
            SymbolPartitionedTickPipeline(maxsize_per_worker=10, num_workers=0)
        with self.assertRaises(TypeError):
            SymbolPartitionedTickPipeline(maxsize_per_worker=10, num_workers=1).on_message(
                123, "TICK"
            )

    async def test_queue_overflow_drops_ticks_and_throttles_logging(self):
        pipeline = SymbolPartitionedTickPipeline(maxsize_per_worker=2, num_workers=1)

        self.assertTrue(pipeline.on_message("NIFTY", "TICK_1"))
        self.assertTrue(pipeline.on_message("NIFTY", "TICK_2"))

        with self.assertLogs("tick_pipeline", level=logging.WARNING) as logs:
            for i in range(3, 53):
                self.assertFalse(pipeline.on_message("NIFTY", f"TICK_{i}"))

        self.assertEqual(pipeline.metrics.total_ticks_dropped, 50)
        self.assertEqual(pipeline.metrics.total_ticks_received, 52)
        # 50 drops inside one throttle window must not produce 50 log lines.
        self.assertEqual(len(logs.output), 1)

    async def test_max_queue_depth_reports_capacity(self):
        """Depth is sampled after the put, so a full queue reports its true depth."""
        pipeline = SymbolPartitionedTickPipeline(maxsize_per_worker=3, num_workers=1)
        for i in range(3):
            self.assertTrue(pipeline.on_message("NIFTY", i))
        self.assertEqual(pipeline.metrics.max_queue_depth, 3)

    async def test_failing_process_fn_is_counted_and_worker_survives(self):
        pipeline = SymbolPartitionedTickPipeline(maxsize_per_worker=10, num_workers=1)
        ok = []

        async def proc(symbol, tick):
            if tick == "BAD":
                raise ValueError("simulated strategy error")
            ok.append(tick)

        pipeline.start_consumers(proc)
        pipeline.on_message("NIFTY", "BAD")
        pipeline.on_message("NIFTY", "GOOD")

        with self.assertLogs("tick_pipeline", level=logging.ERROR):
            # task_done() must run on the failure path too, or join() never returns.
            await asyncio.wait_for(pipeline.worker_queues[0].join(), timeout=2.0)

        await pipeline.stop_consumers()
        self.assertEqual(ok, ["GOOD"])
        self.assertEqual(pipeline.metrics.total_ticks_failed, 1)
        self.assertEqual(pipeline.metrics.total_ticks_processed, 1)

    async def test_graceful_drain_processes_backlog(self):
        pipeline = SymbolPartitionedTickPipeline(maxsize_per_worker=100, num_workers=2)
        processed = []

        async def slow_proc(symbol, tick):
            await asyncio.sleep(0.001)
            processed.append(tick)

        pipeline.start_consumers(slow_proc)
        for i in range(40):
            pipeline.on_message(f"SYM{i % 5}", i)

        started = time.monotonic()
        await pipeline.stop_consumers(drain=True, drain_timeout=5.0)
        elapsed = time.monotonic() - started

        self.assertEqual(len(processed), 40)
        self.assertEqual(pipeline.metrics.total_ticks_undrained, 0)
        # Must exit as soon as the backlog clears, not sit out the drain timeout.
        self.assertLess(elapsed, 2.0)

    async def test_drain_false_accounts_undrained_ticks(self):
        pipeline = SymbolPartitionedTickPipeline(maxsize_per_worker=100, num_workers=1)

        async def slow_proc(symbol, tick):
            await asyncio.sleep(0.5)

        pipeline.start_consumers(slow_proc)
        for i in range(20):
            pipeline.on_message("NIFTY", i)
        await asyncio.sleep(0.01)

        await pipeline.stop_consumers(drain=False)
        self.assertGreater(pipeline.metrics.total_ticks_undrained, 0)
        self.assertLess(pipeline.metrics.total_ticks_processed, 20)

    async def test_queue_wait_is_measured_separately_from_processing(self):
        pipeline = SymbolPartitionedTickPipeline(maxsize_per_worker=100, num_workers=1)

        async def proc(symbol, tick):
            await asyncio.sleep(0.01)

        # Enqueue a backlog *before* consumers start, so later ticks accrue queue wait.
        for i in range(3):
            pipeline.on_message("NIFTY", i)
        pipeline.start_consumers(proc)
        await pipeline.stop_consumers()

        m = pipeline.metrics
        self.assertEqual(m.total_ticks_processed, 3)
        self.assertGreaterEqual(m.avg_latency_ms, 5.0)      # ~10ms of sleep per tick
        self.assertGreater(m.max_queue_wait_ms, 0.0)
        self.assertGreaterEqual(m.max_queue_wait_ms, m.avg_queue_wait_ms)
        self.assertIn("avg_queue_wait_ms", m.to_dict())

    async def test_handoff_buffer_is_bounded(self):
        """The cross-thread handoff must not become an unbounded queue of its own."""
        pipeline = SymbolPartitionedTickPipeline(
            maxsize_per_worker=100, num_workers=1, max_pending_handoffs=2
        )

        async def proc(symbol, tick):
            return None

        pipeline.start_consumers(proc)
        accepted = []

        def producer():
            for i in range(10):
                accepted.append(pipeline.submit_threadsafe("NIFTY", i))

        t = threading.Thread(target=producer)
        # Block the loop so queued handoffs cannot be consumed while the thread runs.
        t.start()
        time.sleep(0.2)
        t.join()

        self.assertEqual(sum(accepted), 2)
        self.assertEqual(pipeline.metrics.total_ticks_dropped, 8)
        await pipeline.stop_consumers()

    async def test_async_callable_object_is_awaited(self):
        """A callable object with an async __call__ is not caught by
        asyncio.iscoroutinefunction; awaiting the returned awaitable keeps such a
        process_fn from silently never running."""

        class Handler:
            def __init__(self):
                self.seen = []

            async def __call__(self, symbol, tick):
                self.seen.append(tick)

        handler = Handler()
        pipeline = SymbolPartitionedTickPipeline(maxsize_per_worker=10, num_workers=1)
        pipeline.start_consumers(handler)
        pipeline.on_message("NIFTY", "TICK")
        await pipeline.stop_consumers()

        self.assertEqual(handler.seen, ["TICK"])
        self.assertEqual(pipeline.metrics.total_ticks_processed, 1)
        self.assertEqual(pipeline.metrics.total_ticks_failed, 0)

    async def test_metrics_to_dict_shape(self):
        m = PipelineMetrics(total_ticks_received=5, avg_latency_ms=1.23456)
        d = m.to_dict()
        self.assertEqual(d["received"], 5)
        self.assertEqual(d["avg_latency_ms"], 1.235)
        for key in ("dropped", "failed", "undrained", "max_queue_depth", "processed"):
            self.assertIn(key, d)

    async def test_backward_compatibility(self):
        p = TickPipeline(maxsize=10)
        p.on_message("RAW_TICK_1")
        self.assertEqual(p.queue.qsize(), 1)
        self.assertEqual(p.dropped, 0)


if __name__ == "__main__":
    unittest.main()
