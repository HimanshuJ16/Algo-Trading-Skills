"""Unit tests for the adaptive batch-size tuner.

Test categories
---------------
* Control law: saturating load expands the batch, quiet load shrinks it,
  the latency throttle closes the loop, the deadband holds the middle.
* Boundary: max/min clipping, exact threshold behaviour, empty queue.
* Invalid inputs: bound ordering, alpha ranges, non-finite latency,
  mis-signed multipliers, capacity ordering.
* Shutdown / lifecycle: full drain on close, use-after-close, reset.
* Concurrency: callback re-entrancy, multi-producer item conservation.
* API ergonomics: flush_now, flush_if_due, status.as_dict serialisation.

Tests marked "regression" fail against the pre-2.0 engine and pass against
the current one; each names the defect it pins down.
"""

import json
import threading
import unittest
from contextlib import contextmanager
from unittest import mock

import batch_tuner
from batch_tuner import (
    AdaptiveBatchTunerEngine,
    QueueFullError,
    TuningConfig,
)


@contextmanager
def virtual_clock(start=1000.0):
    """Replace ``batch_tuner``'s monotonic clock with one the test drives.

    Time-dependent flush behaviour must be deterministic; sleeping in tests
    trades wall-clock for flakiness and buys nothing.
    """
    now = [start]

    def advance(seconds):
        now[0] += seconds

    with mock.patch.object(batch_tuner.time, "monotonic", lambda: now[0]):
        yield advance


# A config whose flush timeout cannot fire during a fast in-process loop, so
# every flush in the test is unambiguously threshold-triggered.
def _threshold_only_config(**overrides):
    base = dict(
        min_batch_size=10,
        max_batch_size=1000,
        initial_batch_size=100,
        min_flush_timeout_sec=1.0,
        initial_flush_timeout_sec=60.0,
        max_flush_timeout_sec=600.0,
        queue_capacity=2000,
        max_queue_size=5000,
    )
    base.update(overrides)
    return TuningConfig(**base)


class TestControlLawDirection(unittest.TestCase):
    """The engine must adapt *toward* load, not away from it.

    Regression: the pre-2.0 engine tuned on ``queue_depth / queue_capacity``.
    Because ``add_item`` drains the buffer the instant it reaches ``B``, that
    ratio is bounded by ``B / queue_capacity`` and measures the tunable rather
    than the load — under saturation it drove ``B`` down to ``B_min`` and the
    flush timeout up to ``T_max``, the exact inverse of the documented curve.
    """

    def test_saturating_load_expands_batch(self):
        cfg = _threshold_only_config()
        tuner = AdaptiveBatchTunerEngine(cfg)
        for i in range(5000):
            batch = tuner.add_item(i)
            if batch is not None:
                tuner.record_write_latency(1.0)  # fast sink, throttle inactive

        self.assertGreater(
            tuner.current_batch_size,
            cfg.initial_batch_size,
            "saturating load must expand the batch size",
        )
        self.assertEqual(tuner.current_batch_size, cfg.max_batch_size)
        self.assertAlmostEqual(tuner.get_status().batch_fill_ratio_ewma, 1.0, places=6)

    def test_saturating_load_reduces_flush_timeout(self):
        cfg = _threshold_only_config()
        tuner = AdaptiveBatchTunerEngine(cfg)
        for i in range(5000):
            if tuner.add_item(i) is not None:
                tuner.record_write_latency(1.0)
        self.assertLess(
            tuner.current_flush_timeout_sec, cfg.initial_flush_timeout_sec
        )

    def test_quiet_load_shrinks_batch(self):
        """Timeout flushes that cut near-empty batches must shrink ``B``."""
        cfg = TuningConfig(
            min_batch_size=10,
            max_batch_size=1000,
            initial_batch_size=200,
            min_flush_timeout_sec=0.05,
            initial_flush_timeout_sec=0.10,
            max_flush_timeout_sec=1.0,
            queue_capacity=2000,
            max_queue_size=5000,
        )
        with virtual_clock() as advance:
            # Built inside the patch so `_last_flush_time` uses the same clock.
            tuner = AdaptiveBatchTunerEngine(cfg)
            # One item per flush window: batches are ~0.5% full.
            for i in range(40):
                tuner.add_item(i)
                advance(cfg.max_flush_timeout_sec * 2)
                tuner.flush_if_due()

        self.assertLess(tuner.current_batch_size, cfg.initial_batch_size)
        self.assertGreater(
            tuner.current_flush_timeout_sec, cfg.initial_flush_timeout_sec
        )

    def test_latency_throttle_closes_the_loop(self):
        """Expansion must be bounded by sink latency, not just by ``B_max``.

        Sink latency scales with batch size, as a real bulk insert does, so
        the equilibrium batch size should sit below ``B_max``.
        """
        cfg = _threshold_only_config(
            target_write_latency_ms=50.0, latency_ewma_alpha=0.5
        )
        tuner = AdaptiveBatchTunerEngine(cfg)
        for i in range(60000):
            batch = tuner.add_item(i)
            if batch is not None:
                tuner.record_write_latency(5.0 + 0.15 * len(batch))

        status = tuner.get_status()
        self.assertLess(tuner.current_batch_size, cfg.max_batch_size)
        self.assertGreater(tuner.current_batch_size, cfg.min_batch_size)
        self.assertLessEqual(
            status.ewma_write_latency_ms, cfg.target_write_latency_ms
        )


class TestDeadband(unittest.TestCase):
    def test_partial_batches_inside_deadband_do_not_tune(self):
        """Batches cut at ~50% full sit in [0.10, 0.70]: hold everything."""
        cfg = TuningConfig(
            initial_batch_size=100,
            min_flush_timeout_sec=0.05,
            initial_flush_timeout_sec=0.10,
            max_flush_timeout_sec=1.0,
            fill_ewma_alpha=1.0,
            queue_capacity=2000,
            max_queue_size=5000,
        )
        with virtual_clock() as advance:
            tuner = AdaptiveBatchTunerEngine(cfg)
            for _ in range(20):
                for i in range(50):  # 50 of 100 => fullness 0.5
                    tuner.add_item(i)
                advance(cfg.max_flush_timeout_sec * 2)
                tuner.flush_if_due()

        self.assertEqual(tuner.current_batch_size, 100)
        self.assertEqual(tuner.current_flush_timeout_sec, 0.10)
        self.assertEqual(tuner.total_tuning_transitions, 0)

    def test_exact_thresholds_are_inclusive_of_the_deadband(self):
        """Boundaries are strict (`>` / `<`), so exactly 0.10 and 0.70 hold."""
        cfg = TuningConfig(fill_ewma_alpha=1.0, initial_batch_size=100)
        for fullness_num in (10, 70):
            with virtual_clock() as advance:
                tuner = AdaptiveBatchTunerEngine(cfg)
                for i in range(fullness_num):
                    tuner.add_item(i)
                advance(cfg.max_flush_timeout_sec * 2)
                tuner.flush_if_due()
            self.assertEqual(
                tuner.current_batch_size, 100,
                f"fullness {fullness_num/100} is on the deadband boundary",
            )
            self.assertEqual(tuner.total_tuning_transitions, 0)


class TestLatencyThrottle(unittest.TestCase):
    def test_throttle_shrinks_batch_above_target(self):
        tuner = AdaptiveBatchTunerEngine(
            TuningConfig(initial_batch_size=200, target_write_latency_ms=50.0,
                         latency_ewma_alpha=1.0)
        )
        tuner.record_write_latency(120.0)
        self.assertEqual(tuner.current_batch_size, 160)  # 200 * 0.8

    def test_throttle_does_not_fire_at_exactly_target(self):
        tuner = AdaptiveBatchTunerEngine(
            TuningConfig(initial_batch_size=200, target_write_latency_ms=50.0,
                         latency_ewma_alpha=1.0)
        )
        tuner.record_write_latency(50.0)  # `>` target, not `>=`
        self.assertEqual(tuner.current_batch_size, 200)

    def test_latency_ewma_is_seeded_with_first_observation(self):
        """Regression: a cold EWMA starting at 0 under-reads a slow sink.

        With alpha=0.2 the old engine reported 0.2*80 = 16 ms after a single
        80 ms write and let the batch keep growing. Seeding reports 80 ms and
        throttles immediately.
        """
        tuner = AdaptiveBatchTunerEngine(
            TuningConfig(initial_batch_size=200, target_write_latency_ms=50.0,
                         latency_ewma_alpha=0.2)
        )
        tuner.record_write_latency(80.0)
        self.assertEqual(tuner.get_status().ewma_write_latency_ms, 80.0)
        self.assertEqual(tuner.current_batch_size, 160)

    def test_ewma_smoothing_matches_hand_computed_value(self):
        tuner = AdaptiveBatchTunerEngine(
            TuningConfig(initial_batch_size=200, target_write_latency_ms=50.0,
                         latency_ewma_alpha=0.5)
        )
        tuner.record_write_latency(40.0)          # seeded -> 40.0
        self.assertEqual(tuner.get_status().ewma_write_latency_ms, 40.0)
        self.assertEqual(tuner.current_batch_size, 200)

        tuner.record_write_latency(100.0)         # 0.5*100 + 0.5*40 = 70 > 50
        self.assertEqual(tuner.get_status().ewma_write_latency_ms, 70.0)
        self.assertEqual(tuner.current_batch_size, 160)

    def test_throttle_stops_at_min_batch_size(self):
        tuner = AdaptiveBatchTunerEngine(
            TuningConfig(min_batch_size=10, initial_batch_size=20,
                         target_write_latency_ms=1.0, latency_ewma_alpha=1.0)
        )
        for _ in range(50):
            tuner.record_write_latency(500.0)
        self.assertEqual(tuner.current_batch_size, 10)


class TestShutdownDrain(unittest.TestCase):
    """Regression: ``close()`` used to return only ``current_batch_size`` items.

    The latency throttle can shrink ``B`` below the depth already buffered, so
    a shutdown drain silently stranded the remainder — data loss on an order
    log or tick sink.
    """

    def test_close_drains_entire_queue_beyond_current_batch_size(self):
        cfg = _threshold_only_config(
            initial_batch_size=100, target_write_latency_ms=50.0,
            latency_ewma_alpha=1.0,
        )
        tuner = AdaptiveBatchTunerEngine(cfg)
        for i in range(90):
            self.assertIsNone(tuner.add_item(i))
        tuner.record_write_latency(400.0)   # 100 -> 80
        tuner.record_write_latency(400.0)   # 80  -> 64
        self.assertLess(tuner.current_batch_size, 90)

        leftover = tuner.close()
        self.assertEqual(len(leftover), 90, "close() must not strand records")
        self.assertEqual(leftover, list(range(90)), "and must preserve order")

    def test_close_on_empty_queue_returns_empty_list(self):
        self.assertEqual(AdaptiveBatchTunerEngine().close(), [])

    def test_close_is_idempotent(self):
        tuner = AdaptiveBatchTunerEngine(_threshold_only_config())
        for i in range(5):
            tuner.add_item(i)
        self.assertEqual(len(tuner.close()), 5)
        self.assertEqual(tuner.close(), [])

    def test_add_after_close_raises(self):
        """Items buffered after shutdown would never be flushed."""
        tuner = AdaptiveBatchTunerEngine(_threshold_only_config())
        tuner.close()
        with self.assertRaises(RuntimeError):
            tuner.add_item("x")

    def test_reset_reopens_a_closed_engine(self):
        tuner = AdaptiveBatchTunerEngine(_threshold_only_config())
        tuner.close()
        tuner.reset()
        self.assertIsNone(tuner.add_item("x"))


class TestFlushTriggers(unittest.TestCase):
    def test_flush_if_due_flushes_an_idle_buffer(self):
        """Regression: the timeout was only evaluated inside ``add_item``.

        A producer that goes quiet left records buffered indefinitely with no
        way to get them out short of ``flush_now``/``close``.
        """
        cfg = TuningConfig(initial_batch_size=1000, max_batch_size=1000,
                           initial_flush_timeout_sec=0.2)
        with virtual_clock() as advance:
            tuner = AdaptiveBatchTunerEngine(cfg)
            for i in range(5):
                self.assertIsNone(tuner.add_item(i))
            self.assertIsNone(tuner.flush_if_due(), "not yet due")
            advance(0.5)
            batch = tuner.flush_if_due()
        self.assertEqual(batch, [0, 1, 2, 3, 4])

    def test_flush_if_due_on_empty_queue_returns_none(self):
        with virtual_clock() as advance:
            tuner = AdaptiveBatchTunerEngine()
            advance(10.0)
            self.assertIsNone(tuner.flush_if_due())

    def test_flush_now_returns_partial_batch(self):
        tuner = AdaptiveBatchTunerEngine(_threshold_only_config())
        for i in range(50):
            tuner.add_item(i)
        self.assertEqual(len(tuner.flush_now()), 50)

    def test_flush_now_is_capped_at_current_batch_size(self):
        cfg = _threshold_only_config(initial_batch_size=100,
                                     target_write_latency_ms=50.0,
                                     latency_ewma_alpha=1.0)
        tuner = AdaptiveBatchTunerEngine(cfg)
        for i in range(90):
            tuner.add_item(i)
        tuner.record_write_latency(400.0)  # 100 -> 80
        self.assertEqual(len(tuner.flush_now()), 80)

    def test_flush_now_does_not_tune(self):
        """A forced checkpoint flush is not evidence about producer speed."""
        tuner = AdaptiveBatchTunerEngine(_threshold_only_config())
        for i in range(3):
            tuner.add_item(i)
        before_b = tuner.current_batch_size
        before_t = tuner.current_flush_timeout_sec
        tuner.flush_now()
        self.assertEqual(tuner.current_batch_size, before_b)
        self.assertEqual(tuner.current_flush_timeout_sec, before_t)
        self.assertEqual(tuner.total_tuning_transitions, 0)

    def test_flush_now_on_empty_queue(self):
        self.assertEqual(AdaptiveBatchTunerEngine().flush_now(), [])


class TestBoundedQueue(unittest.TestCase):
    def test_queue_exhaustion_raises(self):
        cfg = _threshold_only_config(
            initial_batch_size=1000, max_batch_size=1000,
            queue_capacity=10, max_queue_size=10,
        )
        tuner = AdaptiveBatchTunerEngine(cfg)
        with self.assertRaises(QueueFullError) as ctx:
            for _ in range(cfg.max_queue_size + 50):
                tuner.add_item("x")
        self.assertEqual(ctx.exception.capacity, 10)
        self.assertEqual(ctx.exception.queued, 10)

    def test_item_is_not_buffered_when_queue_is_full(self):
        cfg = _threshold_only_config(
            initial_batch_size=1000, max_batch_size=1000,
            queue_capacity=5, max_queue_size=5,
        )
        tuner = AdaptiveBatchTunerEngine(cfg)
        for i in range(5):
            tuner.add_item(i)
        with self.assertRaises(QueueFullError):
            tuner.add_item("rejected")
        self.assertNotIn("rejected", tuner.close())


class TestInputValidation(unittest.TestCase):
    def test_min_greater_than_max_raises(self):
        with self.assertRaises(ValueError):
            TuningConfig(min_batch_size=1000, max_batch_size=10,
                         initial_batch_size=500)

    def test_initial_outside_range_raises(self):
        with self.assertRaises(ValueError):
            TuningConfig(min_batch_size=10, max_batch_size=100,
                         initial_batch_size=200)

    def test_initial_flush_timeout_outside_range_raises(self):
        with self.assertRaises(ValueError):
            TuningConfig(min_flush_timeout_sec=0.05, max_flush_timeout_sec=1.0,
                         initial_flush_timeout_sec=5.0)

    def test_alpha_zero_raises(self):
        with self.assertRaises(ValueError):
            TuningConfig(latency_ewma_alpha=0.0)

    def test_alpha_above_one_raises(self):
        with self.assertRaises(ValueError):
            TuningConfig(fill_ewma_alpha=1.5)

    def test_thresholds_inverted_raises(self):
        with self.assertRaises(ValueError):
            TuningConfig(fill_low_threshold=0.7, fill_high_threshold=0.1)

    def test_queue_capacity_above_hard_cap_raises(self):
        """The gauge denominator must be reachable, or alerts never fire."""
        with self.assertRaises(ValueError):
            TuningConfig(queue_capacity=100, max_queue_size=10)

    def test_non_positive_latency_target_raises(self):
        with self.assertRaises(ValueError):
            TuningConfig(target_write_latency_ms=0.0)

    def test_mis_signed_multipliers_raise(self):
        """A multiplier pointing the wrong way silently inverts the curve."""
        for kwargs in (
            {"expand_multiplier": 0.9},
            {"shrink_divisor": 0.9},
            {"latency_throttle_multiplier": 1.2},
            {"latency_timeout_reduction": 1.2},
            {"timeout_increase": 0.9},
        ):
            with self.subTest(**kwargs):
                with self.assertRaises(ValueError):
                    TuningConfig(**kwargs)

    def test_negative_latency_raises(self):
        with self.assertRaises(ValueError):
            AdaptiveBatchTunerEngine().record_write_latency(-1.0)

    def test_non_finite_latency_raises(self):
        """Regression: NaN poisoned the EWMA and silently disabled the throttle.

        ``NaN > target`` is ``False`` forever after, and ``NaN`` serialises as
        invalid JSON in the metrics export.
        """
        tuner = AdaptiveBatchTunerEngine()
        for bad in (float("nan"), float("inf"), float("-inf")):
            with self.subTest(value=bad):
                with self.assertRaises(ValueError):
                    tuner.record_write_latency(bad)
        self.assertEqual(tuner.get_status().ewma_write_latency_ms, 0.0)


class TestStatus(unittest.TestCase):
    def test_status_is_strict_json_serializable(self):
        tuner = AdaptiveBatchTunerEngine(_threshold_only_config())
        for i in range(20):
            tuner.add_item(i)
        tuner.record_write_latency(12.5)
        payload = tuner.get_status().as_dict()
        # allow_nan=False rejects NaN/Infinity, which are not valid JSON.
        json.dumps(payload, allow_nan=False)
        self.assertIsInstance(payload["current_batch_size"], int)
        self.assertIsInstance(payload["total_flush_events"], int)
        self.assertIn("batch_fill_ratio_ewma", payload)

    def test_queue_gauge_is_reported_against_capacity(self):
        cfg = _threshold_only_config(queue_capacity=1000, max_queue_size=1000)
        tuner = AdaptiveBatchTunerEngine(cfg)
        for i in range(50):
            tuner.add_item(i)
        status = tuner.get_status()
        self.assertEqual(status.queue_depth, 50)
        self.assertAlmostEqual(status.queue_fill_ratio_raw, 0.05)

    def test_reset_clears_state(self):
        tuner = AdaptiveBatchTunerEngine(_threshold_only_config(initial_batch_size=50))
        for i in range(20):
            tuner.add_item(i)
        tuner.record_write_latency(200.0)
        tuner.flush_now()
        self.assertGreater(tuner.total_flush_events, 0)

        tuner.reset()

        status = tuner.get_status()
        self.assertEqual(status.total_flush_events, 0)
        self.assertEqual(status.total_flushed_records, 0)
        self.assertEqual(status.total_tuning_transitions, 0)
        self.assertEqual(status.current_batch_size, 50)
        self.assertEqual(status.ewma_write_latency_ms, 0.0)
        self.assertEqual(status.batch_fill_ratio_ewma, 0.0)
        self.assertEqual(status.queue_depth, 0)


class TestCallbackAndConcurrency(unittest.TestCase):
    def test_on_flush_callback_may_reenter_the_engine(self):
        """Regression: the callback ran under the engine lock and deadlocked.

        Reading ``get_status()`` from a flush callback — the obvious way to
        emit metrics — hung the producer thread forever on a non-reentrant
        lock.
        """
        seen = []

        def callback(batch):
            seen.append(tuner.get_status().queue_depth)

        tuner = AdaptiveBatchTunerEngine(
            _threshold_only_config(initial_batch_size=2, min_batch_size=1),
            on_flush=callback,
        )

        worker = threading.Thread(
            target=lambda: [tuner.add_item(i) for i in range(2)], daemon=True
        )
        worker.start()
        worker.join(timeout=5.0)
        self.assertFalse(worker.is_alive(), "callback re-entrancy deadlocked")
        self.assertEqual(seen, [0])

    def test_failing_callback_does_not_lose_the_batch(self):
        def boom(batch):
            raise RuntimeError("sink exporter is down")

        tuner = AdaptiveBatchTunerEngine(
            _threshold_only_config(initial_batch_size=2, min_batch_size=1),
            on_flush=boom,
        )
        tuner.add_item(1)
        with self.assertLogs("batch_tuner", level="ERROR"):
            batch = tuner.add_item(2)
        self.assertEqual(batch, [1, 2])

    def test_callback_not_invoked_for_empty_flush(self):
        calls = []
        tuner = AdaptiveBatchTunerEngine(on_flush=calls.append)
        tuner.flush_now()
        tuner.close()
        self.assertEqual(calls, [])

    def test_multi_producer_conserves_every_item(self):
        """No item may be lost or duplicated across concurrent producers."""
        cfg = _threshold_only_config(initial_batch_size=25, max_queue_size=100000,
                                     queue_capacity=100000)
        tuner = AdaptiveBatchTunerEngine(cfg)
        collected = []
        collected_lock = threading.Lock()
        threads_count, per_thread = 8, 500

        def produce(tid):
            local = []
            for i in range(per_thread):
                batch = tuner.add_item((tid, i))
                if batch:
                    local.extend(batch)
            with collected_lock:
                collected.extend(local)

        threads = [threading.Thread(target=produce, args=(t,))
                   for t in range(threads_count)]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=30.0)
        self.assertFalse(any(t.is_alive() for t in threads))

        collected.extend(tuner.close())
        expected = {(t, i) for t in range(threads_count) for i in range(per_thread)}
        self.assertEqual(len(collected), threads_count * per_thread)
        self.assertEqual(set(collected), expected)


class TestBoundsHolding(unittest.TestCase):
    def test_batch_size_stays_within_configured_bounds(self):
        cfg = TuningConfig(min_batch_size=10, max_batch_size=1000,
                           initial_batch_size=100, fill_ewma_alpha=1.0,
                           min_flush_timeout_sec=0.05,
                           initial_flush_timeout_sec=0.2,
                           max_flush_timeout_sec=1.0)
        with virtual_clock() as advance:
            tuner = AdaptiveBatchTunerEngine(cfg)
            # Alternate saturating bursts and near-idle windows.
            for cycle in range(30):
                burst = 400 if cycle % 2 == 0 else 1
                for i in range(burst):
                    tuner.add_item(i)
                advance(cfg.max_flush_timeout_sec * 2)
                tuner.flush_if_due()
                tuner.record_write_latency(10.0 * (cycle % 5))
                self.assertGreaterEqual(tuner.current_batch_size, cfg.min_batch_size)
                self.assertLessEqual(tuner.current_batch_size, cfg.max_batch_size)
                self.assertGreaterEqual(
                    tuner.current_flush_timeout_sec, cfg.min_flush_timeout_sec)
                self.assertLessEqual(
                    tuner.current_flush_timeout_sec, cfg.max_flush_timeout_sec)


if __name__ == "__main__":
    unittest.main()
