"""
Unit tests for memory-mapped-ring-buffer-for-ultra-low-latency skill.
"""
import math
import os
import struct
import tempfile
import unittest

from mmap_ring_buffer import (
    MemoryMappedRingBufferEngine,
    MMAPTickSlot,
    RingBufferError,
)


class TestLayout(unittest.TestCase):
    """The on-wire layout is asserted independently of the format strings."""

    def test_slot_is_40_bytes(self):
        self.assertEqual(MemoryMappedRingBufferEngine.SLOT_SIZE, 40)

    def test_header_is_32_bytes_and_index_offsets_are_aligned(self):
        self.assertEqual(MemoryMappedRingBufferEngine.HEADER_SIZE, 32)
        # magic(4) version(4) capacity(8) head(8) tail(8)
        self.assertEqual(MemoryMappedRingBufferEngine._HEAD_OFFSET, 16)
        self.assertEqual(MemoryMappedRingBufferEngine._TAIL_OFFSET, 24)
        self.assertEqual(MemoryMappedRingBufferEngine._HEAD_OFFSET % 8, 0)
        self.assertEqual(MemoryMappedRingBufferEngine._TAIL_OFFSET % 8, 0)

    def test_formats_declare_endianness_explicitly(self):
        # Without a '>' prefix, native alignment would insert padding and
        # silently change both sizes.
        self.assertTrue(MemoryMappedRingBufferEngine.HEADER_FORMAT.startswith(">"))
        self.assertTrue(MemoryMappedRingBufferEngine.SLOT_FORMAT.startswith(">"))

    def test_total_buffer_size_matches_capacity(self):
        with MemoryMappedRingBufferEngine(capacity=7) as ring:
            self.assertEqual(ring.total_buffer_size, 32 + 7 * 40)
            self.assertEqual(os.path.getsize(ring.filepath), 32 + 7 * 40)


class TestMemoryMappedRingBufferEngine(unittest.TestCase):

    def setUp(self):
        self.engine = MemoryMappedRingBufferEngine(capacity=5)

    def tearDown(self):
        self.engine.close()

    def test_push_pop_fifo_ordering(self):
        self.assertTrue(self.engine.push(101, 1000000, 100.5, 100.6, 10))
        self.assertTrue(self.engine.push(102, 1000001, 100.7, 100.8, 20))

        slot1 = self.engine.pop()
        self.assertIsNotNone(slot1)
        self.assertEqual(slot1.sequence_id, 101)
        self.assertAlmostEqual(slot1.bid, 100.5)

        slot2 = self.engine.pop()
        self.assertIsNotNone(slot2)
        self.assertEqual(slot2.sequence_id, 102)

        # Third pop on an empty buffer -> None
        self.assertIsNone(self.engine.pop())

    def test_all_five_fields_round_trip(self):
        # The original suite asserted only sequence_id and bid, so a swapped
        # ask/volume packing order would have gone unnoticed.
        self.engine.push(7, 1_700_000_000_123_456_789, 99.25, 99.75, 1234.5)
        slot = self.engine.pop()
        self.assertEqual(slot.sequence_id, 7)
        self.assertEqual(slot.timestamp_ns, 1_700_000_000_123_456_789)
        self.assertEqual(slot.bid, 99.25)
        self.assertEqual(slot.ask, 99.75)
        self.assertEqual(slot.volume, 1234.5)

    def test_doubles_round_trip_bit_exactly(self):
        # IEEE-754 doubles must survive verbatim; no float32 narrowing.
        bid = 0.1 + 0.2
        self.engine.push(1, 1, bid, 1e308, 5e-324)
        slot = self.engine.pop()
        self.assertEqual(slot.bid, bid)
        self.assertEqual(slot.ask, 1e308)
        self.assertEqual(slot.volume, 5e-324)

    def test_uint64_boundary_values_round_trip(self):
        max_u64 = 2**64 - 1
        self.engine.push(max_u64, max_u64, 0.0, 0.0, 0.0)
        slot = self.engine.pop()
        self.assertEqual(slot.sequence_id, max_u64)
        self.assertEqual(slot.timestamp_ns, max_u64)

    def test_buffer_full_overflow_prevention(self):
        for i in range(5):
            self.assertTrue(self.engine.push(i, 1000 + i, 10.0, 10.1, 1.0))

        # 6th push when full should return False
        self.assertFalse(self.engine.push(999, 9999, 10.0, 10.1, 1.0))

    def test_dropped_push_does_not_corrupt_the_oldest_slot(self):
        for i in range(5):
            self.engine.push(i, 1000 + i, 10.0, 10.1, 1.0)
        self.assertFalse(self.engine.push(999, 9999, 77.0, 77.0, 77.0))

        # The rejected tick must not have overwritten the tail slot.
        self.assertEqual(self.engine.pop().sequence_id, 0)
        self.assertEqual(len(self.engine), 4)

    def test_wraparound_past_capacity_preserves_fifo(self):
        # The core ring behaviour: pushing far more than `capacity` ticks while
        # draining must keep strict FIFO and reuse slots correctly.
        popped = []
        for i in range(23):  # 23 is not a multiple of capacity 5
            self.assertTrue(self.engine.push(i, i, float(i), float(i) + 0.5, 1.0))
            slot = self.engine.pop()
            self.assertIsNotNone(slot)
            popped.append(slot.sequence_id)
        self.assertEqual(popped, list(range(23)))
        self.assertIsNone(self.engine.pop())

    def test_wraparound_with_partial_drain_preserves_fifo(self):
        # Fill, drain 3, refill 3 so the writes straddle the modulo boundary.
        for i in range(5):
            self.engine.push(i, i, 1.0, 1.0, 1.0)
        self.assertEqual([self.engine.pop().sequence_id for _ in range(3)], [0, 1, 2])
        for i in range(5, 8):
            self.assertTrue(self.engine.push(i, i, 1.0, 1.0, 1.0))
        self.assertFalse(self.engine.push(99, 99, 1.0, 1.0, 1.0))  # full again
        self.assertEqual(
            [self.engine.pop().sequence_id for _ in range(5)], [3, 4, 5, 6, 7])

    def test_len_reports_backlog(self):
        self.assertEqual(len(self.engine), 0)
        self.engine.push(1, 1, 1.0, 1.0, 1.0)
        self.engine.push(2, 2, 1.0, 1.0, 1.0)
        self.assertEqual(len(self.engine), 2)
        self.engine.pop()
        self.assertEqual(len(self.engine), 1)

    def test_pop_on_fresh_buffer_returns_none(self):
        self.assertIsNone(self.engine.pop())

    def test_len_clamps_and_logs_when_indices_are_inconsistent(self):
        # head < tail can only arise from a torn read or a rogue second writer.
        # len() must not blow up the monitoring path with ValueError:
        # __len__() should return >= 0.
        self.engine._INDEX_STRUCT.pack_into(
            self.engine.mm, self.engine._TAIL_OFFSET, 5)
        with self.assertLogs("mmap_ring_buffer", level="ERROR") as captured:
            self.assertEqual(len(self.engine), 0)
        self.assertIn("inconsistent", captured.output[0])
        # And the ring still fails closed on the read side.
        self.assertIsNone(self.engine.pop())

    def test_nan_and_inf_round_trip_unchanged(self):
        # Documented behaviour: the ring is a transport and does not sanitise
        # payloads. Pinning this stops a future "helpful" coercion landing
        # silently, and reminds the consumer it must validate.
        self.engine.push(1, 1, float("nan"), float("inf"), float("-inf"))
        slot = self.engine.pop()
        self.assertTrue(math.isnan(slot.bid))
        self.assertEqual(slot.ask, float("inf"))
        self.assertEqual(slot.volume, float("-inf"))

    def test_slot_uses_slots_not_dict(self):
        slot = MMAPTickSlot(1, 2, 3.0, 4.0, 5.0)
        self.assertFalse(hasattr(slot, "__dict__"))


class TestIndexOwnership(unittest.TestCase):
    """
    Regression tests for the lost-update defect.

    The previous implementation rewrote the whole header on every push and pop.
    A consumer committing `read_tail` therefore also rewrote `write_head` from
    its own stale snapshot (and vice versa), so a concurrent peer's committed
    index was silently reverted and already-published ticks were lost. Each side
    must now touch only the index it owns.

    These tests inject the peer's operation at the exact instant between the
    caller reading the indices and committing its own, which is the interleaving
    that made the old design lose ticks. They fail against the whole-header
    design and pass against the split-index one.
    """

    def setUp(self):
        self.path = os.path.join(tempfile.gettempdir(), "ring_ownership_probe.bin")
        if os.path.exists(self.path):
            os.remove(self.path)
        self.producer = MemoryMappedRingBufferEngine(
            capacity=8, backing_filepath=self.path)
        self.consumer = MemoryMappedRingBufferEngine.attach(self.path)

    def tearDown(self):
        self.consumer.close()
        self.producer.close()
        if os.path.exists(self.path):
            os.remove(self.path)

    def test_producer_push_during_consumer_pop_is_not_lost(self):
        self.producer.push(1, 1, 1.0, 1.0, 1.0)
        self.producer.push(2, 2, 2.0, 2.0, 2.0)

        # The producer publishes tick 3 after the consumer has read its indices
        # but before the consumer commits its new tail.
        original_read_head = self.consumer._read_head
        fired = []

        def read_head_then_let_producer_publish():
            value = original_read_head()
            if not fired:
                fired.append(True)
                self.producer.push(3, 3, 3.0, 3.0, 3.0)
            return value

        self.consumer._read_head = read_head_then_let_producer_publish
        first = self.consumer.pop()
        self.consumer._read_head = original_read_head

        self.assertEqual(first.sequence_id, 1)
        _, head, tail = self.producer._get_header()
        self.assertEqual(head, 3, "producer's committed head must survive the pop")
        self.assertEqual(tail, 1)
        # Ticks 2 and 3 are still readable, still in order, still intact.
        self.assertEqual([self.consumer.pop().sequence_id for _ in range(2)], [2, 3])
        self.assertIsNone(self.consumer.pop())

    def test_consumer_pop_during_producer_push_is_not_lost(self):
        for i in range(1, 4):
            self.producer.push(i, i, float(i), float(i), 1.0)

        # The consumer drains a tick after the producer has read its indices but
        # before the producer commits its new head.
        original_read_tail = self.producer._read_tail
        fired = []

        def read_tail_then_let_consumer_drain():
            value = original_read_tail()
            if not fired:
                fired.append(True)
                self.consumer.pop()
            return value

        self.producer._read_tail = read_tail_then_let_consumer_drain
        self.assertTrue(self.producer.push(4, 4, 4.0, 4.0, 1.0))
        self.producer._read_tail = original_read_tail

        _, head, tail = self.producer._get_header()
        self.assertEqual(head, 4)
        self.assertEqual(tail, 1, "consumer's committed tail must survive the push")
        self.assertEqual(
            [self.consumer.pop().sequence_id for _ in range(3)], [2, 3, 4])


class TestCapacityValidation(unittest.TestCase):

    def test_zero_capacity_is_rejected(self):
        # Previously this mapped fine and then dropped 100% of pushes with only
        # a warning -- total silent tick loss.
        with self.assertRaises(RingBufferError):
            MemoryMappedRingBufferEngine(capacity=0)

    def test_negative_capacity_is_rejected(self):
        with self.assertRaises(RingBufferError):
            MemoryMappedRingBufferEngine(capacity=-5)

    def test_non_integer_capacity_is_rejected(self):
        with self.assertRaises(RingBufferError):
            MemoryMappedRingBufferEngine(capacity=10.5)

    def test_capacity_one_works(self):
        with MemoryMappedRingBufferEngine(capacity=1) as ring:
            self.assertTrue(ring.push(1, 1, 1.0, 1.0, 1.0))
            self.assertFalse(ring.push(2, 2, 2.0, 2.0, 2.0))
            self.assertEqual(ring.pop().sequence_id, 1)
            self.assertTrue(ring.push(2, 2, 2.0, 2.0, 2.0))
            self.assertEqual(ring.pop().sequence_id, 2)

    def test_rejected_capacity_leaves_no_backing_file(self):
        path = os.path.join(tempfile.gettempdir(), "ring_reject_probe.bin")
        if os.path.exists(path):
            os.remove(path)
        with self.assertRaises(RingBufferError):
            MemoryMappedRingBufferEngine(capacity=0, backing_filepath=path)
        self.assertFalse(os.path.exists(path))


class TestPayloadValidation(unittest.TestCase):

    def setUp(self):
        self.engine = MemoryMappedRingBufferEngine(capacity=4)

    def tearDown(self):
        self.engine.close()

    def test_negative_sequence_raises_ring_buffer_error(self):
        # Previously surfaced as a raw `struct.error: int too large to convert`.
        with self.assertRaises(RingBufferError):
            self.engine.push(-1, 1, 1.0, 1.0, 1.0)

    def test_sequence_above_uint64_raises_ring_buffer_error(self):
        with self.assertRaises(RingBufferError):
            self.engine.push(2**64, 1, 1.0, 1.0, 1.0)

    def test_negative_timestamp_raises_ring_buffer_error(self):
        with self.assertRaises(RingBufferError):
            self.engine.push(1, -1, 1.0, 1.0, 1.0)

    def test_non_numeric_payload_raises_ring_buffer_error(self):
        with self.assertRaises(RingBufferError):
            self.engine.push(1, 1, "not-a-price", 1.0, 1.0)

    def test_rejected_push_does_not_advance_head(self):
        with self.assertRaises(RingBufferError):
            self.engine.push(-1, 1, 1.0, 1.0, 1.0)
        self.assertEqual(len(self.engine), 0)
        self.assertIsNone(self.engine.pop())

    def test_ring_buffer_error_is_a_value_error(self):
        self.assertTrue(issubclass(RingBufferError, ValueError))


class TestAttachAndFileLifetime(unittest.TestCase):
    """
    The skill's stated purpose is inter-process transport, so a second handle
    must be able to join a live buffer, and closing one handle must not destroy
    the other's data.
    """

    def setUp(self):
        self.path = os.path.join(tempfile.gettempdir(), "ring_attach_probe.bin")
        if os.path.exists(self.path):
            os.remove(self.path)

    def tearDown(self):
        if os.path.exists(self.path):
            os.remove(self.path)

    def test_consumer_attaches_and_reads_producer_writes(self):
        producer = MemoryMappedRingBufferEngine(capacity=8, backing_filepath=self.path)
        producer.push(11, 111, 1.5, 1.6, 100.0)
        producer.push(12, 112, 2.5, 2.6, 200.0)

        consumer = MemoryMappedRingBufferEngine.attach(self.path)
        try:
            self.assertEqual(consumer.capacity, 8)
            self.assertEqual(len(consumer), 2)
            first = consumer.pop()
            self.assertEqual(first.sequence_id, 11)
            self.assertEqual(first.volume, 100.0)
            # Producer observes the consumer's drain through the shared mapping,
            # with no msync/flush anywhere.
            self.assertEqual(len(producer), 1)
            # ...and a write made after attaching is visible to the consumer.
            producer.push(13, 113, 3.5, 3.6, 300.0)
            self.assertEqual([consumer.pop().sequence_id for _ in range(2)], [12, 13])
        finally:
            consumer.close()
            producer.close()

    def test_attaching_consumer_close_does_not_delete_the_file(self):
        producer = MemoryMappedRingBufferEngine(capacity=4, backing_filepath=self.path)
        try:
            consumer = MemoryMappedRingBufferEngine.attach(self.path)
            consumer.close()
            self.assertTrue(os.path.exists(self.path))
            # Producer's buffer is still usable after the consumer left.
            self.assertTrue(producer.push(1, 1, 1.0, 1.0, 1.0))
        finally:
            producer.close()

    def test_caller_supplied_file_is_not_deleted_on_close(self):
        # Previously close() unlinked ANY backing file, including one the caller
        # owns and another process may still be mapping.
        ring = MemoryMappedRingBufferEngine(capacity=4, backing_filepath=self.path)
        ring.close()
        self.assertTrue(os.path.exists(self.path))

    def test_temp_file_is_deleted_on_close(self):
        ring = MemoryMappedRingBufferEngine(capacity=4)
        temp_path = ring.filepath
        self.assertTrue(os.path.exists(temp_path))
        ring.close()
        self.assertFalse(os.path.exists(temp_path))

    def test_unlink_on_close_can_be_forced(self):
        ring = MemoryMappedRingBufferEngine(
            capacity=4, backing_filepath=self.path, unlink_on_close=True)
        ring.close()
        self.assertFalse(os.path.exists(self.path))

    def test_attach_rejects_missing_file(self):
        with self.assertRaises(RingBufferError):
            MemoryMappedRingBufferEngine.attach(self.path)

    def test_attach_rejects_foreign_file(self):
        with open(self.path, "wb") as f:
            f.write(b"\x00" * 512)
        with self.assertRaises(RingBufferError):
            MemoryMappedRingBufferEngine.attach(self.path)

    def test_attach_rejects_truncated_file(self):
        ring = MemoryMappedRingBufferEngine(capacity=8, backing_filepath=self.path)
        ring.close()
        with open(self.path, "r+b") as f:
            f.truncate(MemoryMappedRingBufferEngine.HEADER_SIZE + 40)
        with self.assertRaises(RingBufferError):
            MemoryMappedRingBufferEngine.attach(self.path)

    def test_attach_rejects_unknown_format_version(self):
        ring = MemoryMappedRingBufferEngine(capacity=4, backing_filepath=self.path)
        ring.close()
        with open(self.path, "r+b") as f:
            f.seek(4)
            f.write(struct.pack(">I", 99))
        with self.assertRaises(RingBufferError):
            MemoryMappedRingBufferEngine.attach(self.path)

    def test_attach_preserves_mid_stream_indices(self):
        producer = MemoryMappedRingBufferEngine(capacity=4, backing_filepath=self.path)
        try:
            for i in range(6):  # forces at least one wrap
                producer.push(i, i, 1.0, 1.0, 1.0)
                if i % 2 == 0:
                    producer.pop()
            _, head, tail = producer._get_header()
            consumer = MemoryMappedRingBufferEngine.attach(self.path)
            try:
                self.assertEqual(consumer._get_header(), (4, head, tail))
            finally:
                consumer.close()
        finally:
            producer.close()


class TestLifecycle(unittest.TestCase):

    def test_close_is_idempotent(self):
        ring = MemoryMappedRingBufferEngine(capacity=4)
        ring.close()
        ring.close()  # must not raise

    def test_use_after_close_names_the_actual_problem(self):
        # The failure must say the mapping is closed, not raise an opaque
        # TypeError about NoneType from somewhere inside struct.
        ring = MemoryMappedRingBufferEngine(capacity=4)
        ring.close()
        for call in (lambda: ring.push(1, 1, 1.0, 1.0, 1.0), ring.pop):
            with self.assertRaises(ValueError) as ctx:
                call()
            self.assertIn("closed", str(ctx.exception))

    def test_context_manager_closes_and_cleans_up(self):
        with MemoryMappedRingBufferEngine(capacity=4) as ring:
            path = ring.filepath
            ring.push(1, 1, 1.0, 1.0, 1.0)
        self.assertFalse(os.path.exists(path))

    def test_context_manager_closes_on_exception(self):
        path = None
        with self.assertRaises(ZeroDivisionError):
            with MemoryMappedRingBufferEngine(capacity=4) as ring:
                path = ring.filepath
                1 / 0
        self.assertFalse(os.path.exists(path))


if __name__ == "__main__":
    unittest.main()
