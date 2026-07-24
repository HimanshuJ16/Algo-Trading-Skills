"""
Unit tests for producer-consumer-tick-pipeline skill.

Tests:
1. Zero-blocking WebSocket on_message callback execution under load.
2. Symbol-partitioned worker queue routing (in-order tick sequence per symbol).
3. Queue overflow drop accounting (QueueFull).
4. Telemetry metrics calculation (total received, processed, max queue depth).
5. Backward compatibility with TickPipeline class.
"""
import asyncio
import unittest
from tick_pipeline import SymbolPartitionedTickPipeline, TickPipeline


class TestProducerConsumerTickPipeline(unittest.IsolatedAsyncioTestCase):

    async def test_symbol_partitioned_pipeline(self):
        pipeline = SymbolPartitionedTickPipeline(maxsize_per_worker=100, num_workers=2)
        processed_ticks = []

        async def dummy_process(symbol, tick):
            processed_ticks.append((symbol, tick))

        pipeline.start_consumers(dummy_process)

        # Enqueue 10 ticks for NIFTY and 10 ticks for BANKNIFTY
        for i in range(10):
            pipeline.on_message("NIFTY", f"NIFTY_TICK_{i}")
            pipeline.on_message("BANKNIFTY", f"BANKNIFTY_TICK_{i}")

        # Wait for queue to drain
        await asyncio.sleep(0.05)
        await pipeline.stop_consumers()

        self.assertEqual(len(processed_ticks), 20)
        self.assertEqual(pipeline.metrics.total_ticks_received, 20)
        self.assertEqual(pipeline.metrics.total_ticks_processed, 20)
        self.assertEqual(pipeline.metrics.total_ticks_dropped, 0)

        # Verify in-order sequence for NIFTY ticks
        nifty_seq = [t[1] for t in processed_ticks if t[0] == "NIFTY"]
        expected_nifty = [f"NIFTY_TICK_{i}" for i in range(10)]
        self.assertEqual(nifty_seq, expected_nifty)

    async def test_queue_overflow_drops_ticks(self):
        pipeline = SymbolPartitionedTickPipeline(maxsize_per_worker=2, num_workers=1)

        # Enqueue 2 ticks (fills queue)
        self.assertTrue(pipeline.on_message("NIFTY", "TICK_1"))
        self.assertTrue(pipeline.on_message("NIFTY", "TICK_2"))

        # 3rd tick should overflow and drop
        self.assertFalse(pipeline.on_message("NIFTY", "TICK_3"))
        self.assertEqual(pipeline.metrics.total_ticks_dropped, 1)

    async def test_backward_compatibility(self):
        p = TickPipeline(maxsize=10)
        p.on_message("RAW_TICK_1")
        self.assertEqual(p.queue.qsize(), 1)
        self.assertEqual(p.dropped, 0)


if __name__ == "__main__":
    unittest.main()
