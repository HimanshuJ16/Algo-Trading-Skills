"""
Unit tests for adaptive-batch-size-tuning-under-load skill.
"""
import unittest
import time
from batch_tuner import AdaptiveBatchTunerEngine


class TestAdaptiveBatchTunerEngine(unittest.TestCase):

    def setUp(self):
        self.tuner = AdaptiveBatchTunerEngine(
            min_batch_size=10,
            max_batch_size=500,
            initial_batch_size=50,
            target_write_latency_ms=50.0,
            latency_ewma_alpha=1.0, # Set to 1.0 for immediate reaction in tests
        )

    def test_low_pressure_batch_shrinkage(self):
        # Low fill ratio (<10%) -> Batch size shrinks down to min_batch_size
        for _ in range(5):
            # Fill only 5 items in a 1000-cap queue (0.5% fill ratio)
            self.tuner.queue.extend(["tick"] * 50)
            self.tuner.add_item("tick", queue_capacity=1000)

        self.assertLessEqual(self.tuner.current_batch_size, 50)

    def test_high_pressure_batch_expansion(self):
        # High fill ratio (>70%) -> Batch size expands
        initial_size = self.tuner.current_batch_size
        self.tuner.queue.extend(["tick"] * 800)
        self.tuner.add_item("tick", queue_capacity=1000)  # 80% fill ratio

        self.assertGreater(self.tuner.current_batch_size, initial_size)

    def test_write_latency_feedback_throttling(self):
        self.tuner.current_batch_size = 200
        # High write latency of 120ms (>50ms target) -> Throttles batch size
        self.tuner.record_write_latency(120.0)

        self.assertEqual(self.tuner.current_batch_size, 160)  # 200 * 0.8
        
    def test_ewma_smoothing(self):
        tuner_ewma = AdaptiveBatchTunerEngine(
            initial_batch_size=200,
            target_write_latency_ms=50.0,
            latency_ewma_alpha=0.5, # Test smoothing
        )
        
        tuner_ewma.record_write_latency(40.0) # EWMA = 40.0
        self.assertEqual(tuner_ewma.current_batch_size, 200) # No throttle
        
        tuner_ewma.record_write_latency(100.0) # EWMA = 0.5*100 + 0.5*40 = 70.0 > 50
        self.assertEqual(tuner_ewma.current_batch_size, 160) # Throttled


if __name__ == "__main__":
    unittest.main()
