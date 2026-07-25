"""
Unit tests for multi-region-active-active-tick-ingestion skill.
"""
import time
import unittest
from active_active_ingest import MultiRegionActiveActiveIngestor


class TestMultiRegionActiveActiveIngestor(unittest.TestCase):

    def setUp(self):
        self.ingestor = MultiRegionActiveActiveIngestor(ttl_seconds=5.0)

    def test_first_arrival_accepted_and_duplicate_dropped(self):
        t0 = time.time()
        # Region A tick arrives first at t0
        res_a = self.ingestor.ingest_regional_tick(
            region_id="us-east-1",
            symbol="AAPL",
            sequence_id=1001,
            timestamp=t0,
            price=150.00,
            volume=10.0,
            receipt_time=t0,
        )

        self.assertFalse(res_a.is_duplicate)
        self.assertEqual(res_a.first_arrived_region, "us-east-1")

        # Region B identical tick arrives 0.005s (5ms) later at t0 + 0.005
        res_b = self.ingestor.ingest_regional_tick(
            region_id="us-west-2",
            symbol="AAPL",
            sequence_id=1001,
            timestamp=t0,
            price=150.00,
            volume=10.0,
            receipt_time=t0 + 0.005,
        )

        self.assertTrue(res_b.is_duplicate)
        self.assertEqual(res_b.first_arrived_region, "us-east-1")
        self.assertAlmostEqual(res_b.latency_delta_ms, 5.0, places=2)

    def test_regional_win_telemetry(self):
        t0 = time.time()
        # 3 ticks where us-east-1 wins, 1 tick where us-west-2 wins
        for seq in [1, 2, 3]:
            self.ingestor.ingest_regional_tick("us-east-1", "AAPL", seq, t0, 150.0, 1.0, t0)
            self.ingestor.ingest_regional_tick("us-west-2", "AAPL", seq, t0, 150.0, 1.0, t0 + 0.002)

        # 1 tick where us-west-2 arrives first
        self.ingestor.ingest_regional_tick("us-west-2", "AAPL", 4, t0, 150.0, 1.0, t0)
        self.ingestor.ingest_regional_tick("us-east-1", "AAPL", 4, t0, 150.0, 1.0, t0 + 0.002)

        stats = self.ingestor.get_regional_win_statistics()
        self.assertEqual(stats["us-east-1"]["wins"], 3)
        self.assertEqual(stats["us-west-2"]["wins"], 1)
        self.assertEqual(stats["us-east-1"]["win_percentage"], 75.0)


if __name__ == "__main__":
    unittest.main()
