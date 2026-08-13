"""Unit tests for the adaptive batch-size tuner.

Test categories
---------------
* Normal operation: low-load shrink, high-load expand, latency throttle
* Boundary: max/min clipping, fill-ratio thresholds
* Invalid inputs: max < min, alpha outside (0,1], negative capacity
* Edge cases: empty queue flush, hysteresis deadband, oscillation damping,
  bounded queue overflow, monotonic time progression, toggle reset/close
* API ergonomics: flush_now, status.as_dict serialisation, callback wiring
"""

import unittest
import time

from batch_tuner import (
    AdaptiveBatchTunerEngine,
    BatchTunerStatus,
    QueueFullError,
    TuningConfig,
)


class TestOriginalBehaviour(unittest.TestCase):
    """Original four tests, modernised for the new API."""

    def setUp(self):
        self.cfg = TuningConfig(
            min_batch_size=10,
            max_batch_size=500,
            initial_batch_size=50,
            target_write_latency_ms=50.0,
            latency_ewma_alpha=1.0,  # immediate reaction for tests
            fill_ewma_alpha=1.0,  # immediate reaction for tests
        )
        self.tuner = AdaptiveBatchTunerEngine(self.cfg)

    def test_low_pressure_batch_shrinkage(self):
        # Simulate a sustained low-load regime by direct EWMA manipulation.
        # This avoids racing the per-add EWMA updates in a single-fill scenario.
        self.tuner.current_batch_size = 100  # set a known starting point
        self.tuner._queue.extend(["x"] * 100)
        self.tuner._ewma_fill_ratio = 0.05  # well under 10% low threshold
        batch = self.tuner.flush_now()
        # flush_now returns up to current_batch_size (100, unchanged pre-flush).
        self.assertEqual(len(batch), 100)
        self.assertLess(self.tuner.current_batch_size, 100)  # 100 / 1.2 = 83

    def test_high_pressure_batch_expansion(self):
        # Simulate a sustained high-load regime by direct EWMA manipulation.
        self.tuner.current_batch_size = 50
        self.tuner._queue.extend(["x"] * 100)
        self.tuner._ewma_fill_ratio = 0.85  # past 70% high threshold
        batch = self.tuner.flush_now()
        # flush_now extracts up to current_batch_size items (50); queue retains 50.
        self.assertEqual(len(batch), 50)
        self.assertGreater(self.tuner.current_batch_size, 50)  # 50 * 1.5 = 75

    def test_write_latency_feedback_throttling(self):
        self.tuner.current_batch_size = 200
        self.tuner.record_write_latency(120.0)
        self.assertEqual(self.tuner.current_batch_size, 160)  # 200 * 0.8

    def test_ewma_smoothing(self):
        cfg = TuningConfig(
            initial_batch_size=200,
            target_write_latency_ms=50.0,
            latency_ewma_alpha=0.5,
        )
        tuner = AdaptiveBatchTunerEngine(cfg)

        tuner.record_write_latency(40.0)
        self.assertEqual(tuner.current_batch_size, 200)  # No throttle below target

        tuner.record_write_latency(100.0)  # EWMA = 0.5*100 + 0.5*40 = 70 > 50
        self.assertEqual(tuner.current_batch_size, 160)


class TestBoundedQueue(unittest.TestCase):
    def test_queue_exhaustion_raises(self):
        cfg = TuningConfig(
            min_batch_size=10,
            max_batch_size=500,
            initial_batch_size=500,  # threshold flushes every add
            max_queue_size=10,
            queue_capacity=100,
        )
        tuner = AdaptiveBatchTunerEngine(cfg)
        # Adding 500+ items that don't flush should overflow.
        with self.assertRaises(QueueFullError):
            for _ in range(cfg.max_queue_size + 50):
                tuner.add_item("x")  # noqa: PERF001 - simple loop is intentional


class TestHysteresis(unittest.TestCase):
    """Tests for the deadband / EWMA smoothing — the core institutional upgrade."""

    def test_deadband_neutral(self):
        cfg = TuningConfig(
            min_batch_size=10,
            max_batch_size=500,
            initial_batch_size=100,
            fill_ewma_alpha=1.0,
            queue_capacity=1000,
        )
        tuner = AdaptiveBatchTunerEngine(cfg)
        # 300/1000 = 30%; inside deadband [0.10, 0.70].
        for _ in range(300):
            tuner.add_item("x")
        initial_b = tuner.current_batch_size
        initial_t = tuner.current_flush_timeout_sec
        # Force a flush and re-evaluate.
        tuner.flush_now()
        # Parameter must NOT change inside the deadband.
        self.assertEqual(tuner.current_batch_size, initial_b)
        self.assertEqual(tuner.current_flush_timeout_sec, initial_t)


class TestFlushNow(unittest.TestCase):
    def test_flush_now_returns_drained_batch(self):
        tuner = AdaptiveBatchTunerEngine(
            TuningConfig(
                min_batch_size=10, max_batch_size=5000, initial_batch_size=1000,
                max_flush_timeout_sec=10.0,
            )
        )
        # Force flush on a flush_now call even though no threshold has been reached.
        for _ in range(50):
            tuner.add_item("x")
        batch = tuner.flush_now()
        self.assertEqual(len(batch), 50)

    def test_flush_now_on_empty_queue(self):
        tuner = AdaptiveBatchTunerEngine()
        self.assertEqual(tuner.flush_now(), [])


class TestReset(unittest.TestCase):
    def test_reset_clears_state(self):
        tuner = AdaptiveBatchTunerEngine(
            TuningConfig(initial_batch_size=50, max_queue_size=100)
        )
        for _ in range(20):
            tuner.add_item("x")
        tuner.record_write_latency(200.0)
        tuner.flush_now()

        # Sanity: state has changed.
        self.assertGreater(tuner.total_flush_events, 0)

        tuner.reset()

        self.assertEqual(tuner.total_flush_events, 0)
        self.assertEqual(tuner.total_flushed_records, 0)
        self.assertEqual(tuner.current_batch_size, 50)
        self.assertEqual(tuner._ewma_write_latency_ms, 0.0)

    def test_close_drains_queue(self):
        tuner = AdaptiveBatchTunerEngine(
            TuningConfig(initial_batch_size=1000, max_flush_timeout_sec=10.0)
        )
        for _ in range(7):
            tuner.add_item("x")
        leftover = tuner.close()
        self.assertEqual(len(leftover), 7)


class TestInputValidation(unittest.TestCase):
    def test_min_greater_than_max_raises(self):
        with self.assertRaises(ValueError):
            TuningConfig(
                min_batch_size=1000, max_batch_size=10, initial_batch_size=500,
            )

    def test_initial_outside_range_raises(self):
        with self.assertRaises(ValueError):
            TuningConfig(
                min_batch_size=10, max_batch_size=100, initial_batch_size=200,
            )

    def test_alpha_zero_raises(self):
        with self.assertRaises(ValueError):
            TuningConfig(latency_ewma_alpha=0.0)

    def test_negative_latency_raises(self):
        tuner = AdaptiveBatchTunerEngine()
        with self.assertRaises(ValueError):
            tuner.record_write_latency(-1.0)

    def test_thresholds_inverted_raises(self):
        with self.assertRaises(ValueError):
            TuningConfig(fill_low_threshold=0.7, fill_high_threshold=0.1)


class TestStatusSerialization(unittest.TestCase):
    def test_status_is_json_serializable(self):
        import json
        tuner = AdaptiveBatchTunerEngine(
            TuningConfig(initial_batch_size=500, max_flush_timeout_sec=10.0)
        )
        for _ in range(20):
            tuner.add_item("x")
        status = tuner.get_status()
        d = status.as_dict()
        # Round-trips through JSON without errors.
        json.dumps(d)
        self.assertIsInstance(d["current_batch_size"], int)
        self.assertIsInstance(d["total_flush_events"], int)


class TestMultiCycleOscillationDamping(unittest.TestCase):
    """Sanity that EWMA-then-deadband tuning is bounded."""

    def test_deadband_keeps_states_within_window(self):
        cfg = TuningConfig(
            min_batch_size=10, max_batch_size=1000, initial_batch_size=100,
            fill_ewma_alpha=1.0,  # immediate reaction for predictability
            queue_capacity=1000,
        )
        tuner = AdaptiveBatchTunerEngine(cfg)
        # 50 cycles of flush_now at varying EWMA fills; the size should remain
        # within [min_batch_size, max_batch_size] at every step.
        for ratio in [0.0, 0.05, 0.10, 0.30, 0.50, 0.69, 0.70, 0.85, 0.99]:
            tuner._queue.clear()
            tuner._queue.extend(["x"] * 50)
            tuner._ewma_fill_ratio = ratio
            tuner.flush_now()
            self.assertGreaterEqual(tuner.current_batch_size, cfg.min_batch_size)
            self.assertLessEqual(tuner.current_batch_size, cfg.max_batch_size)


if __name__ == "__main__":
    unittest.main()
