import unittest
from historical_tick_data_storage_and_compaction import (
    HistoricalTickStorageCompactionEngine, RawTickRecord, TickStorageCompactionReport
)

class TestHistoricalTickStorageCompactionEngine(unittest.TestCase):

    def setUp(self):
        self.engine = HistoricalTickStorageCompactionEngine(target_min_compression_ratio=5.0)
        # Generate 1,000 synthetic ticks
        base_time = 1_700_000_000_000_000_000 # nanoseconds
        self.ticks = [
            RawTickRecord(
                timestamp_nanos=base_time + (i * 10_000_000), # 10ms increments
                price=150.00 + ((i % 10) * 0.05),
                quantity=100 + (i % 50),
                side="BUY" if i % 2 == 0 else "SELL"
            )
            for i in range(1000)
        ]

    def test_delta_encoding_and_compaction_ratio(self):
        report = self.engine.compact_and_archive_ticks("AAPL", self.ticks, age_days=100)

        self.assertEqual(report.total_ticks_processed, 1000)
        self.assertEqual(report.storage_tier, "COLD_TIER")
        self.assertGreater(report.compression_ratio, 5.0) # > 5x compression!
        self.assertGreater(report.space_savings_pct, 80.0) # > 80% space savings

    def test_hot_tier_assignment(self):
        report = self.engine.compact_and_archive_ticks("AAPL", self.ticks, age_days=3)
        self.assertEqual(report.storage_tier, "HOT_TIER")

if __name__ == '__main__':
    unittest.main()
