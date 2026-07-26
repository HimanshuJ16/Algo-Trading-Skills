import unittest
from rebalance_guard import (
    ConsumerGroupRebalanceGuard, StreamMessage,
    PartitionRevokedException, DuplicateMessageException
)

class TestConsumerGroupRebalanceGuard(unittest.TestCase):

    def setUp(self):
        self.guard = ConsumerGroupRebalanceGuard()
        self.guard.on_partitions_assigned([0, 1])

    def test_normal_message_processing(self):
        msg = StreamMessage(partition=0, offset=100, idempotency_key="ORD_101", payload={"symbol": "AAPL"})
        self.guard.process_message(msg)
        
        self.assertIn("ORD_101", self.guard.processed_idempotency_keys)
        self.assertEqual(self.guard.committed_offsets[0], 100)

    def test_duplicate_message_prevention(self):
        msg = StreamMessage(partition=0, offset=100, idempotency_key="ORD_101", payload={"symbol": "AAPL"})
        self.guard.process_message(msg)
        
        # Second attempt with same idempotency key -> throws DuplicateMessageException
        with self.assertRaises(DuplicateMessageException):
            self.guard.process_message(msg)

    def test_partition_fencing_on_revocation(self):
        msg1 = StreamMessage(partition=0, offset=101, idempotency_key="ORD_102", payload={"symbol": "MSFT"})
        self.guard.process_message(msg1)
        
        # Revoke partition 0
        self.guard.on_partitions_revoked([0])
        self.assertNotIn(0, self.guard.active_partitions)
        
        # Attempting to process message on revoked partition 0 throws PartitionRevokedException
        msg2 = StreamMessage(partition=0, offset=102, idempotency_key="ORD_103", payload={"symbol": "MSFT"})
        with self.assertRaises(PartitionRevokedException):
            self.guard.process_message(msg2)

    def test_rebalance_storm_detection(self):
        # 3 rebalances in short window
        self.guard.on_partitions_assigned([0])
        self.guard.on_partitions_assigned([1])
        self.guard.on_partitions_assigned([2])
        self.assertGreaterEqual(len(self.guard.rebalance_timestamps), 3)

if __name__ == '__main__':
    unittest.main()
