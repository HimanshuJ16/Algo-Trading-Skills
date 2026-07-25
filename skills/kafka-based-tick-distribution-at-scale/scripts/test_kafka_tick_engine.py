"""
Unit tests for kafka-based-tick-distribution-at-scale skill.
"""
import unittest
from kafka_tick_engine import KafkaTickProducerConsumerEngine


class TestKafkaTickProducerConsumerEngine(unittest.TestCase):

    def setUp(self):
        self.engine = KafkaTickProducerConsumerEngine(topic_name="market-ticks", num_partitions=4)

    def test_deterministic_symbol_partitioning(self):
        # Ticks for AAPL must always map to the same partition
        p1 = self.engine.get_partition_for_symbol("AAPL")
        p2 = self.engine.get_partition_for_symbol("AAPL")
        self.assertEqual(p1, p2)

        msg1 = self.engine.produce_tick("AAPL", 150.0, 150.05, 100)
        msg2 = self.engine.produce_tick("AAPL", 150.1, 150.15, 200)

        self.assertEqual(msg1.partition, p1)
        self.assertEqual(msg2.partition, p1)
        # Sequential offsets within partition
        self.assertEqual(msg1.offset, 0)
        self.assertEqual(msg2.offset, 1)

    def test_batch_production_and_consumer_groups(self):
        batch = [
            {"symbol": "AAPL", "bid": 150.0, "ask": 150.05},
            {"symbol": "TSLA", "bid": 250.0, "ask": 250.10},
            {"symbol": "NVDA", "bid": 120.0, "ask": 120.02},
            {"symbol": "AAPL", "bid": 150.1, "ask": 150.15},
        ]
        self.engine.produce_batch(batch)

        # Consumer Group 'strategy-group' reads partition for AAPL
        aapl_partition = self.engine.get_partition_for_symbol("AAPL")
        msgs = self.engine.consume_partition_batch("strategy-group", aapl_partition, max_messages=10)

        self.assertGreaterEqual(len(msgs), 2)
        self.assertEqual(msgs[0].symbol, "AAPL")

        # Commit offset after reading
        last_offset = msgs[-1].offset + 1
        self.engine.commit_offset("strategy-group", aapl_partition, last_offset)

        # Subsequent fetch starts after committed offset
        new_msgs = self.engine.consume_partition_batch("strategy-group", aapl_partition, max_messages=10)
        self.assertEqual(len(new_msgs), 0)


if __name__ == "__main__":
    unittest.main()
