import unittest
from kafka_tick_engine import (
    KafkaTickDistributionEngine, MarketTickPayload, KafkaTickDistributionReport
)

class TestKafkaTickDistributionEngine(unittest.TestCase):

    def setUp(self):
        self.engine = KafkaTickDistributionEngine(
            num_partitions=16, max_lag_threshold_ticks=1000
        )

    def test_deterministic_symbol_key_partitioning(self):
        # Deterministic hashing: AAPL should always route to the exact same partition index!
        p1 = self.engine.get_symbol_partition_id("AAPL")
        p2 = self.engine.get_symbol_partition_id("AAPL")
        p3 = self.engine.get_symbol_partition_id("AAPL")

        self.assertEqual(p1, p2)
        self.assertEqual(p2, p3)
        self.assertGreaterEqual(p1, 0)
        self.assertLess(p1, 16)

    def test_healthy_stream_processing(self):
        ticks = [
            MarketTickPayload("AAPL", 1700000000000, 150.0, 150.05, 100, 100, 150.02, 50),
            MarketTickPayload("MSFT", 1700000000000, 310.0, 310.10, 200, 200, 310.05, 100),
            MarketTickPayload("AAPL", 1700000001000, 150.01, 150.06, 100, 100, 150.05, 20)
        ]
        # Simulate consumer committed offsets equal to log end offsets
        sim_offsets = {p: 1 for p in range(16)}
        report = self.engine.publish_and_audit_ticks(ticks, simulated_consumed_offsets=sim_offsets)

        self.assertEqual(report.status, "KAFKA_STREAM_HEALTHY")
        self.assertEqual(report.total_ticks_processed, 3)
        self.assertIn("AAPL", report.symbols_partition_map)
        self.assertIn("MSFT", report.symbols_partition_map)

    def test_consumer_lag_warning_trigger(self):
        # 2,000 ticks generated, but committed offset = 0 -> Consumer Lag = 2,000 > 1,000 threshold!
        ticks = [
            MarketTickPayload("AAPL", 1700000000000 + i, 150.0, 150.05, 100, 100, 150.02, 50)
            for i in range(2000)
        ]
        report = self.engine.publish_and_audit_ticks(ticks, simulated_consumed_offsets={0: 0})

        self.assertEqual(report.status, "CONSUMER_LAG_WARNING")
        self.assertGreater(report.max_consumer_lag_ticks, 1000)

if __name__ == '__main__':
    unittest.main()
