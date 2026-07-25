"""
Unit tests for consumer-group-rebalance-safety skill.
"""
import unittest
from rebalance_guard import ConsumerGroupRebalanceGuard, RebalanceState


class TestConsumerGroupRebalanceGuard(unittest.TestCase):

    def setUp(self):
        self.guard = ConsumerGroupRebalanceGuard("test-group")
        # Assign partitions 0 and 1
        self.guard.on_partitions_assigned([0, 1])

    def test_partition_assignment_and_buffering(self):
        self.assertEqual(self.guard.state, RebalanceState.NORMAL)
        self.assertTrue(self.guard.buffer_in_flight_record(0, {"seq": 100, "price": 150.0}))
        self.assertTrue(self.guard.buffer_in_flight_record(1, {"seq": 200, "price": 250.0}))

        # Unassigned partition 2 -> Rejected
        self.assertFalse(self.guard.buffer_in_flight_record(2, {"seq": 300, "price": 50.0}))

    def test_revocation_flushes_batch_and_commits_offset(self):
        self.guard.buffer_in_flight_record(0, {"seq": 100, "offset": 15})
        self.guard.buffer_in_flight_record(0, {"seq": 101, "offset": 16})

        def mock_flush(partition, batch):
            # Return last committed offset
            return batch[-1]["offset"]

        report = self.guard.on_partitions_revoked([0], flush_fn=mock_flush)

        self.assertTrue(report.is_safe)
        self.assertEqual(report.flushed_records_count, 2)
        self.assertEqual(report.committed_offsets[0], 16)
        self.assertNotIn(0, self.guard.assigned_partitions)


if __name__ == "__main__":
    unittest.main()
