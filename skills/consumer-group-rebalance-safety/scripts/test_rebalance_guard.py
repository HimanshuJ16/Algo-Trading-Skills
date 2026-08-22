import threading
import unittest

from rebalance_guard import (
    ConsumerGroupRebalanceGuard,
    DuplicateMessageException,
    OffsetCommitError,
    OffsetRegressionError,
    PartitionRevokedException,
    StreamMessage,
)


class RecordingCommitter:
    """Captures every commit call so tests can assert the exact offsets committed."""

    def __init__(self, fail_with=None):
        self.calls = []
        self.fail_with = fail_with

    def __call__(self, offsets):
        self.calls.append(dict(offsets))
        if self.fail_with is not None:
            raise self.fail_with


class RecordingFlusher:
    def __init__(self, fail_on_partition=None):
        self.calls = []
        self.fail_on_partition = fail_on_partition

    def __call__(self, partition, messages):
        self.calls.append((partition, list(messages)))
        if partition == self.fail_on_partition:
            raise RuntimeError("executor unavailable")


def msg(partition, offset, key, symbol="AAPL"):
    return StreamMessage(
        partition=partition, offset=offset, idempotency_key=key, payload={"symbol": symbol}
    )


class TestMessageProcessing(unittest.TestCase):
    def setUp(self):
        self.committer = RecordingCommitter()
        self.guard = ConsumerGroupRebalanceGuard(commit_fn=self.committer)
        self.guard.on_partitions_assigned([0, 1])

    def test_normal_message_processing(self):
        self.guard.process_message(msg(0, 100, "ORD_101"))

        self.assertIn("ORD_101", self.guard.processed_idempotency_keys)
        self.assertEqual(self.guard.last_processed_offsets[0], 100)
        # Nothing is committed until revocation or an explicit commit.
        self.assertEqual(self.guard.committed_offsets, {})

    def test_duplicate_key_is_rejected(self):
        self.guard.process_message(msg(0, 100, "ORD_101"))
        with self.assertRaises(DuplicateMessageException):
            self.guard.process_message(msg(0, 101, "ORD_101"))

    def test_offset_regression_is_rejected(self):
        """A replay from a stale position must not drag the commit pointer backwards."""
        self.guard.process_message(msg(0, 500, "ORD_A"))
        with self.assertRaises(OffsetRegressionError):
            self.guard.process_message(msg(0, 499, "ORD_B"))
        with self.assertRaises(OffsetRegressionError):
            self.guard.process_message(msg(0, 500, "ORD_C"))
        self.assertEqual(self.guard.last_processed_offsets[0], 500)

    def test_offset_regression_is_catchable_as_duplicate(self):
        self.guard.process_message(msg(0, 5, "ORD_A"))
        with self.assertRaises(DuplicateMessageException):
            self.guard.process_message(msg(0, 4, "ORD_B"))

    def test_fence_is_checked_before_duplicate(self):
        """A revoked partition must never execute, duplicate or not."""
        self.guard.process_message(msg(0, 10, "ORD_X"))
        self.guard.on_partitions_revoked([0])
        with self.assertRaises(PartitionRevokedException):
            self.guard.process_message(msg(0, 10, "ORD_X"))

    def test_partitions_are_independent(self):
        self.guard.process_message(msg(0, 900, "ORD_P0"))
        self.guard.process_message(msg(1, 5, "ORD_P1"))
        self.assertEqual(self.guard.last_processed_offsets, {0: 900, 1: 5})

    def test_invalid_messages_are_rejected(self):
        for bad in (
            msg(-1, 1, "K"),
            msg(0, -1, "K"),
            msg(0, 1, ""),
            msg(0, 1, "   "),
        ):
            with self.assertRaises(ValueError):
                self.guard.process_message(bad)

    def test_idempotency_cache_is_bounded(self):
        guard = ConsumerGroupRebalanceGuard(max_idempotency_keys=3)
        guard.on_partitions_assigned([0])
        for i in range(5):
            guard.process_message(msg(0, i, f"K{i}"))

        self.assertEqual(len(guard.processed_idempotency_keys), 3)
        self.assertNotIn("K0", guard.processed_idempotency_keys)
        self.assertIn("K4", guard.processed_idempotency_keys)


class TestRevocation(unittest.TestCase):
    def setUp(self):
        self.committer = RecordingCommitter()
        self.flusher = RecordingFlusher()
        self.guard = ConsumerGroupRebalanceGuard(
            commit_fn=self.committer, flush_fn=self.flusher
        )
        self.guard.on_partitions_assigned([0, 1])

    def test_commits_next_offset_not_last_processed(self):
        """Regression: committing the last processed offset replays that message."""
        self.guard.process_message(msg(0, 100, "ORD_101"))
        self.guard.on_partitions_revoked([0])

        self.assertEqual(self.committer.calls, [{0: 101}])
        self.assertEqual(self.guard.committed_offsets, {0: 101})

    def test_commits_even_when_buffer_already_drained(self):
        """Progress must be committed on revocation regardless of buffer contents."""
        self.guard.process_message(msg(1, 42, "ORD_D"))
        self.guard.in_flight_buffer[1].clear()

        self.guard.on_partitions_revoked([1])
        self.assertEqual(self.committer.calls, [{1: 43}])

    def test_flush_runs_before_commit(self):
        order = []
        guard = ConsumerGroupRebalanceGuard(
            commit_fn=lambda offsets: order.append(("commit", dict(offsets))),
            flush_fn=lambda p, m: order.append(("flush", p)),
        )
        guard.on_partitions_assigned([0])
        guard.process_message(msg(0, 7, "ORD_F"))
        guard.on_partitions_revoked([0])

        self.assertEqual(order, [("flush", 0), ("commit", {0: 8})])

    def test_flush_failure_blocks_commit_and_raises(self):
        flusher = RecordingFlusher(fail_on_partition=0)
        committer = RecordingCommitter()
        guard = ConsumerGroupRebalanceGuard(commit_fn=committer, flush_fn=flusher)
        guard.on_partitions_assigned([0, 1])
        guard.process_message(msg(0, 10, "ORD_0"))
        guard.process_message(msg(1, 20, "ORD_1"))

        with self.assertRaises(OffsetCommitError) as ctx:
            guard.on_partitions_revoked([0, 1])

        # Partition 0 never flushed, so it is not committed; partition 1 is.
        self.assertEqual(committer.calls, [{1: 21}])
        self.assertIn(0, ctx.exception.failures)
        self.assertNotIn(1, ctx.exception.failures)

    def test_commit_failure_raises_but_partitions_stay_fenced(self):
        committer = RecordingCommitter(fail_with=RuntimeError("broker unreachable"))
        guard = ConsumerGroupRebalanceGuard(commit_fn=committer)
        guard.on_partitions_assigned([0])
        guard.process_message(msg(0, 3, "ORD_Z"))

        with self.assertRaises(OffsetCommitError):
            guard.on_partitions_revoked([0])

        self.assertFalse(guard.is_partition_active(0))
        self.assertEqual(guard.committed_offsets, {})
        with self.assertRaises(PartitionRevokedException):
            guard.process_message(msg(0, 4, "ORD_Y"))

    def test_partition_state_is_discarded_on_revocation(self):
        self.guard.process_message(msg(0, 1, "ORD_S"))
        self.guard.on_partitions_revoked([0])

        self.assertNotIn(0, self.guard.in_flight_buffer)
        self.assertNotIn(0, self.guard.last_processed_offsets)
        self.assertNotIn(0, self.guard.active_partitions)
        # The untouched partition is unaffected.
        self.assertIn(1, self.guard.active_partitions)

    def test_revoking_unknown_partition_is_a_noop(self):
        self.guard.on_partitions_revoked([99])
        self.assertEqual(self.committer.calls, [])

    def test_reassignment_after_revocation_reactivates(self):
        self.guard.process_message(msg(0, 100, "ORD_101"))
        self.guard.on_partitions_revoked([0])
        self.guard.on_partitions_assigned([0])

        self.assertTrue(self.guard.is_partition_active(0))
        # Offsets restart cleanly: the stale high-water mark was discarded.
        self.guard.process_message(msg(0, 5, "ORD_NEW"))
        self.assertEqual(self.guard.last_processed_offsets[0], 5)

    def test_no_commit_fn_means_no_commit_recorded(self):
        guard = ConsumerGroupRebalanceGuard()
        guard.on_partitions_assigned([0])
        guard.process_message(msg(0, 9, "ORD_N"))
        guard.on_partitions_revoked([0])

        self.assertEqual(guard.committed_offsets, {})


class TestPartitionsLost(unittest.TestCase):
    def setUp(self):
        self.committer = RecordingCommitter()
        self.flusher = RecordingFlusher()
        self.guard = ConsumerGroupRebalanceGuard(
            commit_fn=self.committer, flush_fn=self.flusher
        )
        self.guard.on_partitions_assigned([0])

    def test_lost_partitions_are_never_committed(self):
        """Ownership is already gone; committing would fight the new owner."""
        self.guard.process_message(msg(0, 77, "ORD_L"))
        self.guard.on_partitions_lost([0])

        self.assertEqual(self.committer.calls, [])
        self.assertEqual(self.flusher.calls, [])
        self.assertEqual(self.guard.committed_offsets, {})

    def test_lost_partitions_are_fenced_and_discarded(self):
        self.guard.process_message(msg(0, 77, "ORD_L"))
        self.guard.on_partitions_lost([0])

        self.assertFalse(self.guard.is_partition_active(0))
        self.assertNotIn(0, self.guard.in_flight_buffer)
        with self.assertRaises(PartitionRevokedException):
            self.guard.process_message(msg(0, 78, "ORD_L2"))


class TestRebalanceStormDetection(unittest.TestCase):
    def test_eager_revoke_plus_assign_counts_as_one_rebalance(self):
        guard = ConsumerGroupRebalanceGuard(rebalance_storm_threshold_count=3)
        self.assertEqual(len(guard.rebalance_timestamps), 0)

        guard.on_partitions_assigned([0])  # initial join
        for _ in range(2):
            guard.on_partitions_revoked([0])
            guard.on_partitions_assigned([0])

        self.assertEqual(len(guard.rebalance_timestamps), 3)
        self.assertTrue(guard.is_rebalance_storm())

    def test_below_threshold_is_not_a_storm(self):
        guard = ConsumerGroupRebalanceGuard(rebalance_storm_threshold_count=3)
        guard.on_partitions_assigned([0])
        guard.on_partitions_revoked([0])
        guard.on_partitions_assigned([0])

        self.assertEqual(len(guard.rebalance_timestamps), 2)
        self.assertFalse(guard.is_rebalance_storm())

    def test_storm_flag_is_returned_by_callbacks(self):
        guard = ConsumerGroupRebalanceGuard(rebalance_storm_threshold_count=2)
        self.assertFalse(guard.on_partitions_assigned([0]))
        self.assertTrue(guard.on_partitions_revoked([0]))

    def test_lost_partitions_count_toward_storm(self):
        guard = ConsumerGroupRebalanceGuard(rebalance_storm_threshold_count=2)
        guard.on_partitions_assigned([0])
        self.assertTrue(guard.on_partitions_lost([0]))

    def test_events_outside_window_are_dropped(self):
        guard = ConsumerGroupRebalanceGuard(
            rebalance_storm_threshold_count=2, rebalance_window_sec=0.01
        )
        guard.on_partitions_assigned([0])
        guard.rebalance_timestamps = [t - 10.0 for t in guard.rebalance_timestamps]

        self.assertFalse(guard.is_rebalance_storm())
        self.assertEqual(guard.rebalance_timestamps, [])

    def test_invalid_configuration_is_rejected(self):
        with self.assertRaises(ValueError):
            ConsumerGroupRebalanceGuard(rebalance_storm_threshold_count=0)
        with self.assertRaises(ValueError):
            ConsumerGroupRebalanceGuard(rebalance_window_sec=0)
        with self.assertRaises(ValueError):
            ConsumerGroupRebalanceGuard(max_idempotency_keys=0)


class TestConcurrency(unittest.TestCase):
    def test_fence_holds_against_concurrent_producers(self):
        """The fence must hold across threads: nothing lands in a discarded buffer.

        Each producer owns its own partition (so offsets stay monotonic) and runs
        until it observes the fence, which makes the rejection deterministic
        rather than a race the revoking thread usually wins.
        """
        MAX_ITERATIONS = 100_000
        guard = ConsumerGroupRebalanceGuard()
        guard.on_partitions_assigned([0, 1])

        produced = threading.Event()
        fenced = {0: False, 1: False}
        overran = []

        def produce(partition):
            for i in range(MAX_ITERATIONS):
                try:
                    guard.process_message(msg(partition, i, f"P{partition}-{i}"))
                    produced.set()
                except PartitionRevokedException:
                    fenced[partition] = True
                    return
            overran.append(partition)

        workers = [threading.Thread(target=produce, args=(p,)) for p in (0, 1)]
        for w in workers:
            w.start()
        self.assertTrue(produced.wait(timeout=5), "producers never admitted a message")
        guard.on_partitions_revoked([0, 1])
        for w in workers:
            w.join(timeout=10)
            self.assertFalse(w.is_alive())

        self.assertEqual(overran, [], "producers never observed the fence")
        self.assertEqual(fenced, {0: True, 1: True})
        self.assertFalse(guard.is_partition_active(0))
        self.assertFalse(guard.is_partition_active(1))
        # Buffers were discarded during revocation and nothing was appended after.
        self.assertEqual(guard.in_flight_buffer, {})


if __name__ == "__main__":
    unittest.main()
