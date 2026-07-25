"""
Unit tests for memory-mapped-ring-buffer-for-ultra-low-latency skill.
"""
import unittest
from mmap_ring_buffer import MemoryMappedRingBufferEngine, MMAPTickSlot


class TestMemoryMappedRingBufferEngine(unittest.TestCase):

    def setUp(self):
        self.engine = MemoryMappedRingBufferEngine(capacity=5)

    def tearDown(self):
        self.engine.close()

    def test_push_pop_fifo_ordering(self):
        # Push 3 ticks
        self.assertTrue(self.engine.push(101, 1000000, 100.5, 100.6, 10))
        self.assertTrue(self.engine.push(102, 1000001, 100.7, 100.8, 20))

        # Pop 1st tick
        slot1 = self.engine.pop()
        self.assertIsNotNone(slot1)
        self.assertEqual(slot1.sequence_id, 101)
        self.assertAlmostEqual(slot1.bid, 100.5)

        # Pop 2nd tick
        slot2 = self.engine.pop()
        self.assertIsNotNone(slot2)
        self.assertEqual(slot2.sequence_id, 102)

        # 3rd pop on empty buffer -> None
        self.assertIsNone(self.engine.pop())

    def test_buffer_full_overflow_prevention(self):
        # Fill capacity of 5 slots
        for i in range(5):
            self.assertTrue(self.engine.push(i, 1000 + i, 10.0, 10.1, 1.0))

        # 6th push when full should return False
        self.assertFalse(self.engine.push(999, 9999, 10.0, 10.1, 1.0))


if __name__ == "__main__":
    unittest.main()
