"""
Unit tests for sequence-number-gap-detection-for-feeds.

The expected values here are derived from the protocol semantics documented in
``references/standards.md``, not from re-running the implementation's own arithmetic:
MoldUDP64 message-level sequencing and partial retransmission responses, CME MDP 3.0
per-channel packet sequencing and weekly resets, and Binance's ``U``/``u``/``pu``
update-ID ranges with snapshot-only recovery.
"""
import logging
import unittest

from gap_detector import (
    FeedFrame,
    FeedResetRequiredError,
    FeedSyncState,
    FrameDisposition,
    SequenceGapDetector,
)

STREAM = "NASDAQ-ITCH-CH01"


def frame(seq, last=None, stream=STREAM, payload=None):
    """Build a frame; ``payload`` defaults to something identifiable per sequence."""
    return FeedFrame(stream, seq, payload if payload is not None else {"seq": seq}, last)


class TestFeedFrameValidation(unittest.TestCase):
    """External input reaches this dataclass directly; it must not accept nonsense."""

    def test_empty_stream_rejected(self):
        with self.assertRaises(ValueError):
            FeedFrame("", 100, {})

    def test_non_string_stream_rejected(self):
        with self.assertRaises(ValueError):
            FeedFrame(None, 100, {})

    def test_negative_sequence_rejected(self):
        with self.assertRaises(ValueError):
            FeedFrame(STREAM, -1, {})

    def test_non_integer_sequence_rejected(self):
        with self.assertRaises(TypeError):
            FeedFrame(STREAM, 100.5, {})

    def test_bool_sequence_rejected(self):
        # bool is an int subclass; a True slipping in as a sequence number would
        # silently read as sequence 1.
        with self.assertRaises(TypeError):
            FeedFrame(STREAM, True, {})

    def test_inverted_range_rejected(self):
        with self.assertRaises(ValueError):
            FeedFrame(STREAM, 200, {}, 199)

    def test_point_frame_spans_one_sequence(self):
        f = frame(100)
        self.assertEqual(f.final_sequence_id, 100)
        self.assertEqual(f.sequence_span, 1)

    def test_range_frame_span(self):
        f = frame(100, 104)
        self.assertEqual(f.final_sequence_id, 104)
        self.assertEqual(f.sequence_span, 5)


class TestConstructorValidation(unittest.TestCase):

    def test_zero_buffer_rejected(self):
        with self.assertRaises(ValueError):
            SequenceGapDetector(max_buffer_size=0)

    def test_negative_buffer_rejected(self):
        with self.assertRaises(ValueError):
            SequenceGapDetector(max_buffer_size=-1)

    def test_zero_reset_threshold_rejected(self):
        with self.assertRaises(ValueError):
            SequenceGapDetector(sequence_reset_threshold=0)

    def test_non_frame_ingest_rejected(self):
        with self.assertRaises(TypeError):
            SequenceGapDetector().ingest_frame("not-a-frame")


class TestInOrderProcessing(unittest.TestCase):

    def setUp(self):
        self.detector = SequenceGapDetector(max_buffer_size=100)

    def test_baseline_then_contiguous_frames(self):
        res1 = self.detector.ingest_frame(frame(100))
        self.assertEqual(res1.state, FeedSyncState.SYNCED)
        self.assertEqual(res1.disposition, FrameDisposition.PROCESSED)
        self.assertEqual(len(res1.processed_frames), 1)
        self.assertTrue(res1.is_trading_authorized)

        res2 = self.detector.ingest_frame(frame(101))
        self.assertEqual(res2.state, FeedSyncState.SYNCED)
        self.assertEqual(res2.processed_frames[0].sequence_id, 101)
        self.assertEqual(self.detector.stats(STREAM).expected_sequence, 102)

    def test_unknown_stream_is_not_authorized(self):
        # No frame has established any correspondence with the publisher yet.
        self.assertFalse(self.detector.is_trading_authorized("never-seen"))
        self.assertIsNone(self.detector.get_state("never-seen"))

    def test_streams_are_independent(self):
        self.detector.ingest_frame(frame(100, stream="CH-A"))
        self.detector.ingest_frame(frame(500, stream="CH-B"))
        # A gap on one channel must not disturb the other.
        self.detector.ingest_frame(frame(103, stream="CH-A"))
        self.assertEqual(self.detector.get_state("CH-A"), FeedSyncState.DIRTY_SYNC_PENDING)
        self.assertEqual(self.detector.get_state("CH-B"), FeedSyncState.SYNCED)
        self.assertTrue(self.detector.is_trading_authorized("CH-B"))
        self.assertEqual(set(self.detector.tracked_streams()), {"CH-A", "CH-B"})

    def test_stream_keys_are_compared_exactly(self):
        self.detector.ingest_frame(frame(100, stream="btcusdt@depth"))
        self.detector.ingest_frame(frame(100, stream="BTCUSDT@DEPTH"))
        self.assertEqual(len(self.detector.tracked_streams()), 2)


class TestGapDetection(unittest.TestCase):

    def setUp(self):
        self.detector = SequenceGapDetector(max_buffer_size=100)
        self.detector.ingest_frame(frame(100))

    def test_gap_buffers_and_blocks_trading(self):
        res = self.detector.ingest_frame(frame(103))
        self.assertTrue(res.is_gap_detected)
        self.assertEqual(res.disposition, FrameDisposition.BUFFERED)
        self.assertEqual(res.state, FeedSyncState.DIRTY_SYNC_PENDING)
        self.assertEqual(res.missing_ranges, ((101, 102),))
        self.assertEqual(res.missing_sequence_count, 2)
        self.assertEqual(res.processed_frames, ())
        self.assertFalse(res.is_trading_authorized)
        self.assertFalse(self.detector.is_trading_authorized(STREAM))

    def test_missing_ranges_exclude_already_buffered_frames(self):
        # 101,102 missing; 103 buffered; then 106 arrives leaving 104,105 missing.
        # Re-requesting 103 would spend capped venue recovery capacity for nothing.
        self.detector.ingest_frame(frame(103))
        res = self.detector.ingest_frame(frame(106))
        self.assertEqual(res.missing_ranges, ((101, 102), (104, 105)))
        self.assertEqual(res.missing_sequence_count, 4)

    def test_duplicate_of_buffered_frame_is_suppressed(self):
        self.detector.ingest_frame(frame(103))
        res = self.detector.ingest_frame(frame(103))
        self.assertEqual(res.disposition, FrameDisposition.DUPLICATE)
        self.assertEqual(self.detector.stats(STREAM).buffered_frames, 1)
        self.assertEqual(self.detector.stats(STREAM).gaps_detected, 1)

    def test_wider_range_replaces_a_narrower_buffered_frame(self):
        # A retransmission that covers more than the copy already held carries
        # sequences not yet buffered; discarding it as a duplicate would lose them.
        detector = SequenceGapDetector()
        detector.ingest_frame(frame(100))
        detector.ingest_frame(frame(103, 104))
        res = detector.ingest_frame(frame(103, 108))
        self.assertEqual(res.disposition, FrameDisposition.BUFFERED)
        self.assertEqual(detector.stats(STREAM).buffered_frames, 1)
        done = detector.reconcile_missing_frames(STREAM, [frame(101), frame(102)])
        self.assertTrue(done.is_synced)
        self.assertEqual(detector.stats(STREAM).expected_sequence, 109)

    def test_stale_frame_is_suppressed_not_applied(self):
        # A retransmission response is unicast onto the same socket as the live
        # multicast stream, so echoes of already-processed messages are routine.
        res = self.detector.ingest_frame(frame(100))
        self.assertEqual(res.disposition, FrameDisposition.DUPLICATE)
        self.assertEqual(res.processed_frames, ())
        self.assertEqual(res.state, FeedSyncState.SYNCED)
        self.assertEqual(self.detector.stats(STREAM).duplicates_suppressed, 1)

    def test_gap_counter_counts_gaps_not_out_of_order_frames(self):
        self.detector.ingest_frame(frame(103))
        self.detector.ingest_frame(frame(104))
        self.detector.ingest_frame(frame(105))
        self.assertEqual(self.detector.stats(STREAM).gaps_detected, 1)

    def test_outstanding_missing_count_feeds_data_quality_metrics(self):
        self.detector.ingest_frame(frame(106))
        stats = self.detector.stats(STREAM)
        self.assertEqual(stats.outstanding_missing_count, 5)  # 101..105
        self.assertFalse(stats.is_trading_authorized)

    def test_stats_unknown_stream_raises(self):
        with self.assertRaises(KeyError):
            self.detector.stats("never-seen")


class TestBufferBound(unittest.TestCase):
    """The documented memory bound must actually be enforced."""

    def test_overflow_drops_frame_and_latches_reset_required(self):
        detector = SequenceGapDetector(max_buffer_size=2)
        detector.ingest_frame(frame(100))
        detector.ingest_frame(frame(103))
        detector.ingest_frame(frame(104))
        stats_before = detector.stats(STREAM)
        self.assertEqual(stats_before.buffered_frames, 2)

        res = detector.ingest_frame(frame(105))
        self.assertEqual(res.disposition, FrameDisposition.DROPPED_BUFFER_FULL)
        self.assertEqual(res.state, FeedSyncState.RESET_REQUIRED)
        self.assertEqual(detector.stats(STREAM).buffered_frames, 2)
        self.assertEqual(detector.stats(STREAM).frames_dropped_buffer_full, 1)
        self.assertFalse(detector.is_trading_authorized(STREAM))

    def test_reset_required_is_latched_against_later_frames(self):
        # Once a frame has been dropped, applying later in-order frames would build
        # state that silently disagrees with the venue.
        detector = SequenceGapDetector(max_buffer_size=1)
        detector.ingest_frame(frame(100))
        detector.ingest_frame(frame(103))
        detector.ingest_frame(frame(104))  # dropped, latches
        res = detector.ingest_frame(frame(101))
        self.assertEqual(res.disposition, FrameDisposition.DROPPED_RESET_REQUIRED)
        self.assertEqual(res.processed_frames, ())
        self.assertEqual(res.state, FeedSyncState.RESET_REQUIRED)

    def test_backfill_refused_on_latched_stream(self):
        detector = SequenceGapDetector(max_buffer_size=1)
        detector.ingest_frame(frame(100))
        detector.ingest_frame(frame(103))
        detector.ingest_frame(frame(104))
        with self.assertRaises(FeedResetRequiredError):
            detector.reconcile_missing_frames(STREAM, [frame(101), frame(102)])

    def test_resynchronize_clears_latch(self):
        detector = SequenceGapDetector(max_buffer_size=1)
        detector.ingest_frame(frame(100))
        detector.ingest_frame(frame(103))
        detector.ingest_frame(frame(104))
        detector.resynchronize(STREAM, 200)
        self.assertEqual(detector.get_state(STREAM), FeedSyncState.SYNCED)
        self.assertEqual(detector.stats(STREAM).buffered_frames, 0)
        res = detector.ingest_frame(frame(200))
        self.assertEqual(res.disposition, FrameDisposition.PROCESSED)
        self.assertTrue(res.is_trading_authorized)


class TestSequenceRestart(unittest.TestCase):
    """CME resets MsgSeqNum weekly; a MoldUDP64 restart opens a new Session."""

    def test_large_backward_jump_is_reported_not_swallowed(self):
        detector = SequenceGapDetector(sequence_reset_threshold=1000)
        detector.ingest_frame(frame(5_000_000))
        res = detector.ingest_frame(frame(1))
        self.assertEqual(res.disposition, FrameDisposition.RESET_SUSPECTED)
        self.assertEqual(res.state, FeedSyncState.RESET_REQUIRED)
        self.assertEqual(res.processed_frames, ())
        self.assertEqual(detector.stats(STREAM).resets_suspected, 1)

    def test_small_backward_jump_is_still_a_duplicate(self):
        detector = SequenceGapDetector(sequence_reset_threshold=1000)
        detector.ingest_frame(frame(5_000))
        res = detector.ingest_frame(frame(4_999))
        self.assertEqual(res.disposition, FrameDisposition.DUPLICATE)
        self.assertEqual(res.state, FeedSyncState.SYNCED)

    def test_restart_boundary_is_inclusive(self):
        detector = SequenceGapDetector(sequence_reset_threshold=100)
        detector.ingest_frame(frame(1_000))  # expected becomes 1001
        res = detector.ingest_frame(frame(901))  # 1001 - 901 == 100
        self.assertEqual(res.disposition, FrameDisposition.RESET_SUSPECTED)

    def test_one_below_boundary_is_a_duplicate(self):
        detector = SequenceGapDetector(sequence_reset_threshold=100)
        detector.ingest_frame(frame(1_000))
        res = detector.ingest_frame(frame(902))  # 1001 - 902 == 99
        self.assertEqual(res.disposition, FrameDisposition.DUPLICATE)

    def test_restarted_stream_recovers_only_via_resynchronize(self):
        detector = SequenceGapDetector(sequence_reset_threshold=1000)
        detector.ingest_frame(frame(5_000_000))
        detector.ingest_frame(frame(1))
        detector.resynchronize(STREAM, 1)
        res = detector.ingest_frame(frame(1))
        self.assertEqual(res.disposition, FrameDisposition.PROCESSED)
        self.assertEqual(res.processed_frames[0].sequence_id, 1)
        self.assertTrue(detector.is_trading_authorized(STREAM))


class TestReconciliation(unittest.TestCase):

    def setUp(self):
        self.detector = SequenceGapDetector(max_buffer_size=100)
        self.detector.ingest_frame(frame(100))
        self.detector.ingest_frame(frame(103))  # buffers 103, gap 101..102

    def test_full_backfill_drains_buffer_and_restores_sync(self):
        res = self.detector.reconcile_missing_frames(STREAM, [frame(101), frame(102)])
        self.assertTrue(res.is_synced)
        self.assertEqual(res.state, FeedSyncState.SYNCED)
        self.assertEqual(
            [f.sequence_id for f in res.processed_frames], [101, 102, 103]
        )
        self.assertEqual(res.remaining_ranges, ())
        self.assertTrue(self.detector.is_trading_authorized(STREAM))
        self.assertEqual(self.detector.stats(STREAM).expected_sequence, 104)

    def test_backfill_accepted_out_of_order(self):
        res = self.detector.reconcile_missing_frames(STREAM, [frame(102), frame(101)])
        self.assertEqual(
            [f.sequence_id for f in res.processed_frames], [101, 102, 103]
        )

    def test_partial_backfill_does_not_claim_recovery(self):
        # MoldUDP64 returns only the messages that completely fit one UDP packet;
        # a caller that resumes on the first response trades on a broken stream.
        res = self.detector.reconcile_missing_frames(STREAM, [frame(101)])
        self.assertFalse(res.is_synced)
        self.assertEqual(res.state, FeedSyncState.RECOVERING)
        self.assertEqual(res.remaining_ranges, ((102, 102),))
        self.assertEqual(res.remaining_sequence_count, 1)
        self.assertEqual([f.sequence_id for f in res.processed_frames], [101])
        self.assertFalse(self.detector.is_trading_authorized(STREAM))

    def test_recovering_state_survives_further_live_frames(self):
        self.detector.reconcile_missing_frames(STREAM, [frame(101)])
        self.detector.ingest_frame(frame(104))
        self.assertEqual(self.detector.get_state(STREAM), FeedSyncState.RECOVERING)

    def test_second_backfill_round_closes_the_gap(self):
        self.detector.reconcile_missing_frames(STREAM, [frame(101)])
        res = self.detector.reconcile_missing_frames(STREAM, [frame(102)])
        self.assertTrue(res.is_synced)
        self.assertEqual([f.sequence_id for f in res.processed_frames], [102, 103])

    def test_duplicate_backfill_frames_counted_not_applied(self):
        self.detector.reconcile_missing_frames(STREAM, [frame(101), frame(102)])
        res = self.detector.reconcile_missing_frames(STREAM, [frame(101), frame(102)])
        self.assertEqual(res.duplicate_count, 2)
        self.assertEqual(res.processed_frames, ())
        self.assertTrue(res.is_synced)

    def test_foreign_stream_frame_rejected(self):
        with self.assertRaises(ValueError):
            self.detector.reconcile_missing_frames(
                STREAM, [frame(101), frame(102, stream="OTHER-CHANNEL")]
            )

    def test_unknown_stream_rejected(self):
        with self.assertRaises(ValueError):
            self.detector.reconcile_missing_frames("never-seen", [frame(1)])

    def test_non_frame_backfill_rejected(self):
        with self.assertRaises(TypeError):
            self.detector.reconcile_missing_frames(STREAM, [frame(101), "102"])


class TestHeartbeat(unittest.TestCase):
    """MoldUDP64 heartbeats carry the next expected sequence precisely so loss is
    visible during quiet periods."""

    def setUp(self):
        self.detector = SequenceGapDetector()

    def test_heartbeat_seeds_an_unseen_stream(self):
        res = self.detector.observe_heartbeat(STREAM, 500)
        self.assertEqual(res.state, FeedSyncState.SYNCED)
        self.assertEqual(self.detector.stats(STREAM).expected_sequence, 500)
        # The seeded baseline means the first live frame is checked, not adopted.
        gap = self.detector.ingest_frame(frame(505))
        self.assertEqual(gap.missing_ranges, ((500, 504),))

    def test_heartbeat_confirms_contiguity(self):
        self.detector.ingest_frame(frame(100))
        res = self.detector.observe_heartbeat(STREAM, 101)
        self.assertFalse(res.is_gap_detected)
        self.assertEqual(res.state, FeedSyncState.SYNCED)

    def test_heartbeat_reveals_tail_loss(self):
        # Frames 101..104 were lost and no later frame will ever expose them.
        self.detector.ingest_frame(frame(100))
        res = self.detector.observe_heartbeat(STREAM, 105)
        self.assertTrue(res.is_gap_detected)
        self.assertEqual(res.missing_ranges, ((101, 104),))
        self.assertEqual(res.state, FeedSyncState.DIRTY_SYNC_PENDING)
        self.assertFalse(self.detector.is_trading_authorized(STREAM))

    def test_tail_loss_stays_outstanding_after_partial_backfill(self):
        self.detector.ingest_frame(frame(100))
        self.detector.observe_heartbeat(STREAM, 105)
        res = self.detector.reconcile_missing_frames(STREAM, [frame(101), frame(102)])
        self.assertFalse(res.is_synced)
        self.assertEqual(res.remaining_ranges, ((103, 104),))

    def test_heartbeat_excludes_buffered_frames_from_missing_ranges(self):
        self.detector.ingest_frame(frame(100))
        self.detector.ingest_frame(frame(103))
        res = self.detector.observe_heartbeat(STREAM, 106)
        self.assertEqual(res.missing_ranges, ((101, 102), (104, 105)))

    def test_stale_heartbeat_ignored(self):
        self.detector.ingest_frame(frame(100))
        res = self.detector.observe_heartbeat(STREAM, 99)
        self.assertEqual(res.disposition, FrameDisposition.DUPLICATE)
        self.assertEqual(res.state, FeedSyncState.SYNCED)

    def test_heartbeat_far_behind_suspects_restart(self):
        detector = SequenceGapDetector(sequence_reset_threshold=1000)
        detector.ingest_frame(frame(5_000_000))
        res = detector.observe_heartbeat(STREAM, 1)
        self.assertEqual(res.disposition, FrameDisposition.RESET_SUSPECTED)
        self.assertEqual(res.state, FeedSyncState.RESET_REQUIRED)

    def test_heartbeat_validates_input(self):
        with self.assertRaises(ValueError):
            self.detector.observe_heartbeat("", 1)
        with self.assertRaises(ValueError):
            self.detector.observe_heartbeat(STREAM, -1)
        with self.assertRaises(TypeError):
            self.detector.observe_heartbeat(STREAM, 1.5)


class TestRangeSequencedStreams(unittest.TestCase):
    """Binance diff-depth events carry U (first) and u (final) update IDs; continuity
    is ``pu == previous u``, and the first post-snapshot event must satisfy
    ``U <= lastUpdateId AND u >= lastUpdateId``."""

    def setUp(self):
        self.detector = SequenceGapDetector()
        self.stream = "btcusdt@depth"

    def test_contiguous_ranges_advance_by_final_id(self):
        self.detector.ingest_frame(frame(100, 104, stream=self.stream))
        res = self.detector.ingest_frame(frame(105, 109, stream=self.stream))
        self.assertEqual(res.disposition, FrameDisposition.PROCESSED)
        self.assertEqual(res.state, FeedSyncState.SYNCED)
        self.assertEqual(self.detector.stats(self.stream).expected_sequence, 110)

    def test_broken_pu_continuity_is_a_gap(self):
        self.detector.ingest_frame(frame(100, 104, stream=self.stream))
        res = self.detector.ingest_frame(frame(107, 109, stream=self.stream))
        self.assertTrue(res.is_gap_detected)
        self.assertEqual(res.missing_ranges, ((105, 106),))

    def test_first_post_snapshot_event_straddling_the_snapshot_is_applied(self):
        # Snapshot lastUpdateId = 1000 -> expect 1001. Event U=998, u=1005 satisfies
        # "U <= lastUpdateId AND u >= lastUpdateId" and must be applied, not dropped.
        self.detector.resynchronize(self.stream, 1001)
        res = self.detector.ingest_frame(frame(998, 1005, stream=self.stream))
        self.assertEqual(res.disposition, FrameDisposition.PARTIAL_OVERLAP)
        self.assertEqual(len(res.processed_frames), 1)
        self.assertEqual(res.state, FeedSyncState.SYNCED)
        self.assertEqual(self.detector.stats(self.stream).expected_sequence, 1006)

    def test_event_entirely_below_snapshot_is_dropped(self):
        # Binance step: "drop any event where u is <= lastUpdateId".
        self.detector.resynchronize(self.stream, 1001)
        res = self.detector.ingest_frame(frame(990, 1000, stream=self.stream))
        self.assertEqual(res.disposition, FrameDisposition.DUPLICATE)
        self.assertEqual(res.processed_frames, ())

    def test_buffered_range_frame_drains_after_backfill(self):
        self.detector.ingest_frame(frame(100, 104, stream=self.stream))
        self.detector.ingest_frame(frame(110, 114, stream=self.stream))
        res = self.detector.reconcile_missing_frames(
            self.stream, [frame(105, 109, stream=self.stream)]
        )
        self.assertTrue(res.is_synced)
        self.assertEqual([f.sequence_id for f in res.processed_frames], [105, 110])
        self.assertEqual(self.detector.stats(self.stream).expected_sequence, 115)

    def test_backfill_overtaking_a_buffered_frame_still_drains_it(self):
        # The backfill range covers past the buffered frame's start, so the buffered
        # frame no longer sits at the expected sequence -- it straddles it.
        self.detector.ingest_frame(frame(100, 104, stream=self.stream))
        self.detector.ingest_frame(frame(110, 120, stream=self.stream))
        res = self.detector.reconcile_missing_frames(
            self.stream, [frame(105, 112, stream=self.stream)]
        )
        self.assertTrue(res.is_synced)
        self.assertEqual([f.sequence_id for f in res.processed_frames], [105, 110])
        self.assertEqual(self.detector.stats(self.stream).expected_sequence, 121)
        self.assertEqual(self.detector.stats(self.stream).buffered_frames, 0)

    def test_backfill_superseding_a_buffered_frame_purges_it(self):
        self.detector.ingest_frame(frame(100, 104, stream=self.stream))
        self.detector.ingest_frame(frame(110, 114, stream=self.stream))
        res = self.detector.reconcile_missing_frames(
            self.stream, [frame(105, 120, stream=self.stream)]
        )
        self.assertTrue(res.is_synced)
        self.assertEqual(self.detector.stats(self.stream).expected_sequence, 121)
        self.assertEqual(self.detector.stats(self.stream).buffered_frames, 0)


class TestRegressionAgainstSilentFailures(unittest.TestCase):
    """Each case here passed silently in a naive implementation."""

    def test_buffer_cannot_grow_past_the_configured_bound(self):
        detector = SequenceGapDetector(max_buffer_size=5)
        detector.ingest_frame(frame(0))
        for seq in range(10, 60):
            detector.ingest_frame(frame(seq))
        self.assertLessEqual(detector.stats(STREAM).buffered_frames, 5)

    def test_stream_does_not_go_deaf_after_a_restart(self):
        detector = SequenceGapDetector(sequence_reset_threshold=1000)
        detector.ingest_frame(frame(5_000_000))
        for seq in range(1, 20):
            res = detector.ingest_frame(frame(seq))
            self.assertEqual(res.processed_frames, ())
        self.assertEqual(detector.get_state(STREAM), FeedSyncState.RESET_REQUIRED)
        self.assertFalse(detector.is_trading_authorized(STREAM))

    def test_no_frame_is_released_before_its_predecessors(self):
        detector = SequenceGapDetector()
        detector.ingest_frame(frame(100))
        released = []
        for seq in (104, 103, 102, 101):
            released.extend(f.sequence_id for f in detector.ingest_frame(frame(seq)).processed_frames)
        self.assertEqual(released, [101, 102, 103, 104])


class TestLoggingDiscipline(unittest.TestCase):

    def test_gap_is_logged_at_warning_with_the_missing_range(self):
        detector = SequenceGapDetector()
        detector.ingest_frame(frame(100))
        with self.assertLogs("gap_detector", level=logging.WARNING) as captured:
            detector.ingest_frame(frame(103))
        self.assertIn("[101..102]", captured.output[0])

    def test_buffer_overflow_is_logged_at_error(self):
        detector = SequenceGapDetector(max_buffer_size=1)
        detector.ingest_frame(frame(100))
        detector.ingest_frame(frame(103))
        with self.assertLogs("gap_detector", level=logging.ERROR):
            detector.ingest_frame(frame(104))


if __name__ == "__main__":
    unittest.main()
