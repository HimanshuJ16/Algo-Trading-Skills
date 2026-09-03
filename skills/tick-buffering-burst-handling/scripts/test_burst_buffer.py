"""
Unit tests for tick-buffering-burst-handling skill.

Tests:
1. Empirical capacity calculation, its floor, and rejection of invalid rates.
2. KEEP_LATEST_N overwrite behavior when buffer is full.
3. DROP_NEWEST_LOG drop behavior and structured log creation.
4. High-water mark occupancy tracking and exact accepted/dropped accounting.
5. Backward compatibility with SymbolBuffer class.
6. Regressions: bounded drop log, capacity validation, symbol-key normalisation,
   non-mutating read paths, and concurrent-push tick conservation.
"""
import logging
import threading
import unittest

from burst_buffer import (
    BurstBufferConfigError,
    BurstBufferManager,
    DropStrategy,
    SymbolBuffer,
)


class TestTickBufferingBurstHandling(unittest.TestCase):

    def setUp(self):
        self.mgr = BurstBufferManager(default_capacity=5, strategy=DropStrategy.KEEP_LATEST_N)

    def test_empirical_capacity_calculation(self):
        # 500 ticks/sec * 2.0 sec lag -> 1000 capacity
        cap = BurstBufferManager.calculate_empirical_capacity(peak_ticks_per_sec=500, max_lag_sec=2.0)
        self.assertEqual(cap, 1000)

    def test_empirical_capacity_rounds_up_and_applies_floor(self):
        # 0.5 ticks/sec * 3 sec = 1.5 -> ceil 2, raised to the documented floor of 50.
        self.assertEqual(
            BurstBufferManager.calculate_empirical_capacity(0.5, 3.0), 50
        )
        # Fractional product above the floor rounds up rather than truncating:
        # 333.3 * 1.5 = 499.95 -> 500, not 499.
        self.assertEqual(
            BurstBufferManager.calculate_empirical_capacity(333.3, 1.5), 500
        )

    def test_empirical_capacity_rejects_invalid_inputs(self):
        # A NaN/zero/negative peak rate must not silently produce the 50-tick floor
        # and present it as an empirically sized buffer.
        for rate in (0, -1, float("nan"), float("inf")):
            with self.subTest(rate=rate):
                with self.assertRaises(BurstBufferConfigError):
                    BurstBufferManager.calculate_empirical_capacity(rate, 2.0)
        with self.assertRaises(BurstBufferConfigError):
            BurstBufferManager.calculate_empirical_capacity(100.0, 0)

    def test_keep_latest_n_overwrite(self):
        # Push 5 ticks (fills buffer of capacity 5)
        for i in range(5):
            self.mgr.push("NIFTY", f"TICK_{i}")

        self.assertEqual(self.mgr.get_latest("NIFTY"), "TICK_4")

        # Push 6th tick -> overwrites oldest TICK_0 and logs drop
        self.mgr.push("NIFTY", "TICK_5")
        self.assertEqual(self.mgr.get_latest("NIFTY"), "TICK_5")
        self.assertEqual(len(self.mgr.drop_logs), 1)
        self.assertEqual(self.mgr.drop_logs[0].dropped_tick, "TICK_0")
        # Buffer holds exactly the newest 5 ticks, oldest first.
        self.assertEqual(
            self.mgr.drain("NIFTY"),
            ["TICK_1", "TICK_2", "TICK_3", "TICK_4", "TICK_5"],
        )

    def test_drop_newest_strategy(self):
        drop_mgr = BurstBufferManager(default_capacity=3, strategy=DropStrategy.DROP_NEWEST_LOG)
        for i in range(3):
            drop_mgr.push("AAPL", f"T1_{i}")

        # 4th tick should fail and be logged
        ok = drop_mgr.push("AAPL", "T1_OVERFLOW")
        self.assertFalse(ok)
        self.assertEqual(len(drop_mgr.drop_logs), 1)
        self.assertEqual(drop_mgr.drop_logs[0].dropped_tick, "T1_OVERFLOW")
        # The retained buffer is untouched by the rejected tick.
        self.assertEqual(drop_mgr.drain("AAPL"), ["T1_0", "T1_1", "T1_2"])

    def test_high_water_mark_reporting(self):
        self.mgr.push("BANKNIFTY", "T1")
        self.mgr.push("BANKNIFTY", "T2")

        rep = self.mgr.get_occupancy_report()
        self.assertEqual(rep["BANKNIFTY"]["current_size"], 2)
        self.assertEqual(rep["BANKNIFTY"]["high_water_mark_pct"], 40.0)
        self.assertEqual(rep["BANKNIFTY"]["offered"], 2)
        self.assertEqual(rep["BANKNIFTY"]["accepted"], 2)
        self.assertEqual(rep["BANKNIFTY"]["dropped"], 0)
        self.assertEqual(rep["BANKNIFTY"]["drop_rate_pct"], 0.0)

    def test_high_water_mark_survives_drain(self):
        # Draining the buffer must not erase the evidence that it ran hot --
        # the high-water mark is the sizing signal for the next session.
        for i in range(5):
            self.mgr.push("NIFTY", i)
        self.mgr.drain("NIFTY")
        rep = self.mgr.get_occupancy_report()
        self.assertEqual(rep["NIFTY"]["current_size"], 0)
        self.assertEqual(rep["NIFTY"]["high_water_mark_pct"], 100.0)

    def test_report_quantifies_loss_exactly(self):
        # 20 ticks offered into a 5-slot buffer, never consumed: 5 survive and
        # 15 are evicted, so 75% of this symbol's session data never reached the
        # consumer. Independently: loss = offered - capacity = 20 - 5 = 15.
        for i in range(20):
            self.mgr.push("NIFTY", i)
        rep = self.mgr.get_occupancy_report()["NIFTY"]
        self.assertEqual(rep["offered"], 20)
        self.assertEqual(rep["dropped"], 15)
        self.assertEqual(rep["drop_rate_pct"], 75.0)
        self.assertEqual(self.mgr.total_drops, 15)
        # The 5 survivors are the newest 5, so the loss is genuinely the oldest 15.
        self.assertEqual(self.mgr.drain("NIFTY"), [15, 16, 17, 18, 19])

    def test_drop_rate_denominator_is_pushes_not_accepted_plus_dropped(self):
        """Regression: under KEEP_LATEST_N a drop is the eviction of a tick already
        counted as accepted, so ``accepted + dropped`` double-counts the burst and
        understates the loss rate (42.86% instead of the true 75%)."""
        for i in range(20):
            self.mgr.push("NIFTY", i)
        rep = self.mgr.get_occupancy_report()["NIFTY"]
        self.assertEqual(rep["offered"], 20)
        self.assertNotEqual(rep["offered"], rep["accepted"] + rep["dropped"])
        self.assertEqual(rep["drop_rate_pct"], 75.0)

        # Under DROP_NEWEST_LOG the two definitions coincide, and must still agree.
        drop_mgr = BurstBufferManager(default_capacity=5, strategy=DropStrategy.DROP_NEWEST_LOG)
        for i in range(20):
            drop_mgr.push("NIFTY", i)
        rep2 = drop_mgr.get_occupancy_report()["NIFTY"]
        self.assertEqual(rep2["offered"], rep2["accepted"] + rep2["dropped"])
        self.assertEqual(rep2["drop_rate_pct"], 75.0)

    # ------------------------------------------------------------- regressions

    def test_drop_log_is_bounded_but_counts_are_exact(self):
        """Regression: the audit log was an unbounded list holding every dropped tick.

        A saturated buffer drops on every push, so the log -- not the buffer --
        became the OOM vector during exactly the burst this skill must survive.
        """
        mgr = BurstBufferManager(default_capacity=10, drop_log_capacity=25)
        for i in range(5000):
            mgr.push("NIFTY", {"seq": i})

        self.assertEqual(len(mgr.drop_logs), 25)          # bounded ring
        self.assertEqual(mgr.drop_counts["NIFTY"], 4990)  # exact total preserved
        self.assertEqual(mgr.total_drops, 4990)
        # The ring retains the most recent drops, not the oldest.
        self.assertEqual(mgr.drop_logs[-1].dropped_tick["seq"], 4989)

    def test_zero_or_negative_capacity_is_rejected(self):
        """Regression: capacity 0 discarded every tick and reported 0% occupancy, 0 drops."""
        for bad in (0, -1, 2.5, "500", True):
            with self.subTest(capacity=bad):
                with self.assertRaises(BurstBufferConfigError):
                    BurstBufferManager(default_capacity=bad)
        with self.assertRaises(BurstBufferConfigError):
            BurstBufferManager(default_capacity=500, custom_capacities={"X": 0})

    def test_custom_capacity_key_is_normalised(self):
        """Regression: a lowercase override key was ignored, silently leaving the
        symbol you sized for a burst on the default capacity."""
        mgr = BurstBufferManager(default_capacity=500, custom_capacities={"nifty": 5000})
        mgr.push("NIFTY", "t")
        self.assertEqual(mgr.buffers["NIFTY"].maxlen, 5000)
        self.assertEqual(mgr.get_occupancy_report()["NIFTY"]["capacity"], 5000)

    def test_conflicting_normalised_capacity_keys_rejected(self):
        with self.assertRaises(BurstBufferConfigError):
            BurstBufferManager(custom_capacities={"nifty": 5000, "NIFTY": 900})

    def test_reads_do_not_create_buffers(self):
        """Regression: get_latest/pop_oldest created a buffer for any symbol asked
        about, so a monitoring loop over a rotating universe grew state without
        bound and padded the occupancy report with phantom symbols."""
        mgr = BurstBufferManager(default_capacity=10)
        for i in range(500):
            self.assertIsNone(mgr.get_latest(f"GHOST_{i}"))
            self.assertIsNone(mgr.pop_oldest(f"GHOST_{i}"))
            self.assertEqual(mgr.drain(f"GHOST_{i}"), [])
        self.assertEqual(mgr.buffers, {})
        self.assertEqual(mgr.get_occupancy_report(), {})

    def test_invalid_symbol_rejected(self):
        for bad in ("", "   ", None, 42):
            with self.subTest(symbol=bad):
                with self.assertRaises(BurstBufferConfigError):
                    self.mgr.push(bad, "tick")

    def test_symbol_normalisation_is_consistent_across_operations(self):
        self.mgr.push(" nifty ", "T1")
        self.assertEqual(self.mgr.get_latest("NIFTY"), "T1")
        self.assertEqual(self.mgr.pop_oldest("Nifty"), "T1")

    def test_concurrent_pushes_conserve_every_tick(self):
        """Regression: the unlocked check-then-create in _get_buffer let two
        threads each build a deque for the same new symbol. The second assignment
        won and the first thread's ticks vanished while push() returned True.

        Measured against the previous implementation this lost a mean of 31.6 of
        1200 ticks per trial (197 of 200 trials lost at least one tick).
        """
        threads, symbols, per_thread = 6, 200, 1
        mgr = BurstBufferManager(default_capacity=100_000)
        barrier = threading.Barrier(threads)

        def worker() -> None:
            barrier.wait()
            for s in range(symbols):
                for _ in range(per_thread):
                    mgr.push(f"S{s}", 1)

        workers = [threading.Thread(target=worker) for _ in range(threads)]
        for t in workers:
            t.start()
        for t in workers:
            t.join()

        expected = threads * symbols * per_thread
        self.assertEqual(sum(len(b) for b in mgr.buffers.values()), expected)
        self.assertEqual(sum(mgr.accept_counts.values()), expected)
        self.assertEqual(mgr.total_drops, 0)

    def test_concurrent_push_and_drain_conserve_ticks(self):
        """A feed thread pushing while a strategy thread drains must neither lose
        a tick nor raise: every tick is either still buffered or was consumed."""
        mgr = BurstBufferManager(default_capacity=50_000)
        total = 20_000
        consumed: list = []
        errors: list = []
        done = threading.Event()

        def producer() -> None:
            try:
                for i in range(total):
                    mgr.push("NIFTY", i)
            except Exception as exc:  # pragma: no cover - failure path
                errors.append(exc)
            finally:
                done.set()

        def consumer() -> None:
            try:
                while not done.is_set():
                    consumed.extend(mgr.drain("NIFTY"))
                consumed.extend(mgr.drain("NIFTY"))
            except Exception as exc:  # pragma: no cover - failure path
                errors.append(exc)

        threads = [threading.Thread(target=producer), threading.Thread(target=consumer)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        self.assertEqual(errors, [])
        self.assertEqual(mgr.total_drops, 0)
        self.assertEqual(consumed, list(range(total)))  # in order, none lost

    def test_overflow_warnings_are_rate_limited(self):
        """Regression: one warning per dropped tick made the log the next
        bottleneck under saturation (the buffer drops on every push)."""
        mgr = BurstBufferManager(default_capacity=2, min_warn_interval_sec=3600.0)
        with self.assertLogs("burst_buffer", level=logging.WARNING) as captured:
            for i in range(1000):
                mgr.push("NIFTY", i)
        self.assertEqual(len(captured.records), 1)
        self.assertEqual(mgr.drop_counts["NIFTY"], 998)

    def test_backward_compatibility(self):
        buf = SymbolBuffer(maxlen=3)
        buf.push("A")
        buf.push("B")
        buf.push("C")
        buf.push("D")  # Overflow oldest 'A'

        self.assertEqual(buf.latest(), "D")
        self.assertEqual(len(buf.drop_log), 1)
        self.assertEqual(buf.drop_log[0]["dropped_oldest"], "A")
        self.assertEqual(buf.drop_count, 1)

    def test_backward_compatible_drop_log_is_bounded(self):
        buf = SymbolBuffer(maxlen=2, drop_log_capacity=10)
        for i in range(500):
            buf.push(i)
        self.assertEqual(len(buf.drop_log), 10)
        self.assertEqual(buf.drop_count, 498)


if __name__ == "__main__":
    unittest.main()
