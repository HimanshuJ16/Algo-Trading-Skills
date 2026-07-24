"""
Unit tests for tick-buffering-burst-handling skill.

Tests:
1. Empirical capacity calculation based on peak tick rate.
2. KEEP_LATEST_N overwrite behavior when buffer is full.
3. DROP_NEWEST_LOG drop behavior and structured log creation.
4. High-water mark occupancy tracking.
5. Backward compatibility with SymbolBuffer class.
"""
import unittest
from burst_buffer import BurstBufferManager, DropStrategy, SymbolBuffer


class TestTickBufferingBurstHandling(unittest.TestCase):

    def setUp(self):
        self.mgr = BurstBufferManager(default_capacity=5, strategy=DropStrategy.KEEP_LATEST_N)

    def test_empirical_capacity_calculation(self):
        # 500 ticks/sec * 2.0 sec lag -> 1000 capacity
        cap = BurstBufferManager.calculate_empirical_capacity(peak_ticks_per_sec=500, max_lag_sec=2.0)
        self.assertEqual(cap, 1000)

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

    def test_drop_newest_strategy(self):
        drop_mgr = BurstBufferManager(default_capacity=3, strategy=DropStrategy.DROP_NEWEST_LOG)
        for i in range(3):
            drop_mgr.push("AAPL", f"T1_{i}")

        # 4th tick should fail and be logged
        ok = drop_mgr.push("AAPL", "T1_OVERFLOW")
        self.assertFalse(ok)
        self.assertEqual(len(drop_mgr.drop_logs), 1)
        self.assertEqual(drop_mgr.drop_logs[0].dropped_tick, "T1_OVERFLOW")

    def test_high_water_mark_reporting(self):
        self.mgr.push("BANKNIFTY", "T1")
        self.mgr.push("BANKNIFTY", "T2")

        rep = self.mgr.get_occupancy_report()
        self.assertEqual(rep["BANKNIFTY"]["current_size"], 2)
        self.assertEqual(rep["BANKNIFTY"]["high_water_mark_pct"], 40.0)

    def test_backward_compatibility(self):
        buf = SymbolBuffer(maxlen=3)
        buf.push("A")
        buf.push("B")
        buf.push("C")
        buf.push("D")  # Overflow oldest 'A'

        self.assertEqual(buf.latest(), "D")
        self.assertEqual(len(buf.drop_log), 1)
        self.assertEqual(buf.drop_log[0]["dropped_oldest"], "A")


if __name__ == "__main__":
    unittest.main()
