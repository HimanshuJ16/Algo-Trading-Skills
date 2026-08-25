"""
Unit tests for exchange-multicast-feed-handling skill.

Expected values are derived from the venue specifications cited in
references/standards.md, not from re-running the implementation's own logic.
"""
import unittest

from multicast_handler import (
    ExchangeMulticastFeedHandler,
    GapState,
    MulticastChannel,
    MulticastPacket,
    PacketDisposition,
)

WINDOW = 0.010  # 10 ms arbitration hold-down used throughout these tests.


class FakeClock:
    """Deterministic monotonic clock so window behaviour is tested without sleeping."""

    def __init__(self, start: float = 0.0) -> None:
        self.now = start

    def __call__(self) -> float:
        return self.now

    def advance(self, seconds: float) -> None:
        self.now += seconds


def build_handler(initial_sequence: int = 100, **kwargs) -> ExchangeMulticastFeedHandler:
    kwargs.setdefault("arbitration_window_s", WINDOW)
    return ExchangeMulticastFeedHandler(initial_sequence, **kwargs)


class TestLineArbitration(unittest.TestCase):
    """Tier 1 recovery: both lines carry identical packets, first copy wins."""

    def setUp(self):
        self.clock = FakeClock()
        self.handler = build_handler(clock=self.clock)

    def test_first_copy_processed_second_copy_discarded(self):
        res_a = self.handler.ingest_packet(MulticastChannel.CHANNEL_A, 100, b"TICK_100")
        self.assertEqual(res_a.disposition, PacketDisposition.PROCESSED)
        self.assertEqual(len(res_a.processed_packets), 1)
        self.assertFalse(res_a.is_gap_detected)

        res_b = self.handler.ingest_packet(MulticastChannel.CHANNEL_B, 100, b"TICK_100")
        self.assertEqual(res_b.disposition, PacketDisposition.DUPLICATE)
        self.assertEqual(res_b.processed_packets, [])

        # Channel B carries the stream forward when A drops the next packet.
        res_c = self.handler.ingest_packet(MulticastChannel.CHANNEL_B, 101, b"TICK_101")
        self.assertEqual(res_c.disposition, PacketDisposition.PROCESSED)
        self.assertEqual(self.handler.expected_sequence, 102)

    def test_duplicate_of_a_buffered_packet_is_recognised(self):
        """Regression: an out-of-order twin must not re-buffer or re-open recovery.

        Seq 103 arrives ahead of 101 on line A, then the identical packet arrives
        on line B. Dedup that only compares against the expected sequence misses
        this case entirely, double-buffering the packet and emitting a second
        recovery request for an overlapping range.
        """
        self.handler.ingest_packet(MulticastChannel.CHANNEL_A, 100, b"TICK_100")
        first = self.handler.ingest_packet(MulticastChannel.CHANNEL_A, 103, b"TICK_103")
        self.assertEqual(first.disposition, PacketDisposition.BUFFERED)

        twin = self.handler.ingest_packet(MulticastChannel.CHANNEL_B, 103, b"TICK_103")
        self.assertEqual(twin.disposition, PacketDisposition.DUPLICATE)
        self.assertEqual(len(self.handler.out_of_order_buffer), 1)
        self.assertEqual(self.handler.recovery_requests, [])

    def test_gap_filled_by_other_line_within_window_never_requests_recovery(self):
        """The whole point of A/B: the twin arrives, so no venue request is made."""
        self.handler.ingest_packet(MulticastChannel.CHANNEL_A, 100, b"A100")
        self.handler.ingest_packet(MulticastChannel.CHANNEL_A, 102, b"A102")
        self.assertIsNotNone(self.handler.pending_gap)

        self.clock.advance(WINDOW / 2)
        res = self.handler.ingest_packet(MulticastChannel.CHANNEL_B, 101, b"B101")

        self.assertEqual([p.sequence_id for p in res.processed_packets], [101, 102])
        self.assertIsNone(self.handler.pending_gap)
        self.assertFalse(res.is_gap_detected)
        self.assertEqual(self.handler.recovery_requests, [])

        self.clock.advance(WINDOW * 10)
        self.assertIsNone(self.handler.poll_recovery())


class TestArbitrationWindow(unittest.TestCase):
    """Every lost packet starts life as a delayed one (Eurex T7 R14.1 s7.4)."""

    def setUp(self):
        self.clock = FakeClock()
        self.handler = build_handler(clock=self.clock)
        self.handler.ingest_packet(MulticastChannel.CHANNEL_A, 100, b"A100")

    def test_no_recovery_request_inside_the_window(self):
        """Regression: recovery must not fire on the first reordered datagram."""
        res = self.handler.ingest_packet(MulticastChannel.CHANNEL_A, 103, b"A103")
        self.assertEqual(res.disposition, PacketDisposition.BUFFERED)
        self.assertTrue(res.is_gap_detected)
        self.assertEqual(res.missing_range, (101, 102))
        self.assertIsNone(res.recovery_request)
        self.assertEqual(self.handler.recovery_requests, [])
        self.assertEqual(self.handler.pending_gap.state, GapState.ARBITRATING)

    def test_boundary_exactly_at_the_window_requests_recovery(self):
        self.handler.ingest_packet(MulticastChannel.CHANNEL_A, 103, b"A103")
        self.clock.advance(WINDOW)
        request = self.handler.poll_recovery()
        self.assertIsNotNone(request)
        self.assertEqual((request.start, request.end), (101, 102))
        self.assertEqual(request.length, 2)

    def test_open_gap_yields_exactly_one_request_however_many_packets_follow(self):
        """Regression: the old handler emitted one overlapping request per packet."""
        self.handler.ingest_packet(MulticastChannel.CHANNEL_A, 103, b"A103")
        self.clock.advance(WINDOW)
        for seq in (104, 105, 106, 107):
            self.handler.ingest_packet(MulticastChannel.CHANNEL_A, seq, b"x")

        self.assertEqual(len(self.handler.recovery_requests), 1)
        self.assertEqual(self.handler.recovery_requests[0].start, 101)

    def test_later_packets_do_not_restart_the_timer(self):
        """Eurex: recovery already pending for a stream must not reset the timer."""
        self.handler.ingest_packet(MulticastChannel.CHANNEL_A, 103, b"A103")
        detected_at = self.handler.pending_gap.detected_at

        for _ in range(5):
            self.clock.advance(WINDOW / 10)
            self.handler.ingest_packet(
                MulticastChannel.CHANNEL_A, self.handler.pending_gap.end + 2, b"x"
            )
        self.assertEqual(self.handler.pending_gap.detected_at, detected_at)
        # 5 x window/10 = window/2, still inside the hold-down.
        self.assertEqual(self.handler.recovery_requests, [])

        self.clock.advance(WINDOW)
        self.assertIsNotNone(self.handler.poll_recovery())

    def test_quiet_feed_after_loss_still_escalates(self):
        """No further packets arrive, so only the timer can trigger recovery."""
        self.handler.ingest_packet(MulticastChannel.CHANNEL_A, 103, b"A103")
        self.assertIsNone(self.handler.poll_recovery())
        self.clock.advance(WINDOW * 2)
        self.assertIsNotNone(self.handler.poll_recovery())
        # Idempotent: polling again does not duplicate the request.
        self.clock.advance(WINDOW * 2)
        self.assertIsNone(self.handler.poll_recovery())
        self.assertEqual(len(self.handler.recovery_requests), 1)

    def test_zero_window_requests_immediately(self):
        handler = build_handler(clock=self.clock, arbitration_window_s=0.0)
        handler.ingest_packet(MulticastChannel.CHANNEL_A, 100, b"A100")
        res = handler.ingest_packet(MulticastChannel.CHANNEL_A, 102, b"A102")
        self.assertIsNotNone(res.recovery_request)


class TestRecoveryReconciliation(unittest.TestCase):
    def setUp(self):
        self.clock = FakeClock()
        self.handler = build_handler(clock=self.clock)
        self.handler.ingest_packet(MulticastChannel.CHANNEL_A, 100, b"A100")
        self.handler.ingest_packet(MulticastChannel.CHANNEL_A, 103, b"A103")
        self.clock.advance(WINDOW)
        self.handler.poll_recovery()

    def test_recovered_data_is_applied_before_queued_real_time_data(self):
        """CME instructs that recovered data be applied prior to queued data."""
        result = self.handler.apply_recovery_packets([
            MulticastPacket(MulticastChannel.RECOVERY, 102, b"R102", 0.0),
            MulticastPacket(MulticastChannel.RECOVERY, 101, b"R101", 0.0),
        ])
        self.assertEqual([p.sequence_id for p in result.processed_packets], [101, 102, 103])
        self.assertTrue(result.is_gap_closed)
        self.assertIsNone(result.outstanding_gap)
        self.assertEqual(self.handler.expected_sequence, 104)
        self.assertEqual(self.handler.out_of_order_buffer, {})

    def test_partial_recovery_leaves_the_gap_open_and_re_armed(self):
        """Regression: a half-filled gap must not close silently."""
        result = self.handler.apply_recovery_packets([
            MulticastPacket(MulticastChannel.RECOVERY, 101, b"R101", 0.0),
        ])
        self.assertEqual([p.sequence_id for p in result.processed_packets], [101])
        self.assertFalse(result.is_gap_closed)
        self.assertEqual(result.outstanding_gap, (102, 102))
        self.assertEqual(self.handler.expected_sequence, 102)
        # 103 is still held: applying it over a hole would corrupt the book.
        self.assertIn(103, self.handler.out_of_order_buffer)
        # Re-armed, so the remainder can be requested again.
        self.assertEqual(self.handler.pending_gap.state, GapState.ARBITRATING)
        self.clock.advance(WINDOW)
        second = self.handler.poll_recovery()
        self.assertIsNotNone(second)
        self.assertEqual((second.start, second.end), (102, 102))

    def test_snapshot_resynchronization_abandons_the_gap(self):
        self.handler.resynchronize(500)
        self.assertEqual(self.handler.expected_sequence, 500)
        self.assertIsNone(self.handler.pending_gap)
        self.assertEqual(self.handler.out_of_order_buffer, {})
        self.assertFalse(self.handler.requires_resynchronization)

        res = self.handler.ingest_packet(MulticastChannel.CHANNEL_A, 500, b"S500")
        self.assertEqual(res.disposition, PacketDisposition.PROCESSED)

    def test_apply_recovery_packets_rejects_non_packets(self):
        with self.assertRaises(TypeError):
            self.handler.apply_recovery_packets([(101, b"R101")])
        with self.assertRaises(TypeError):
            self.handler.apply_recovery_packets("not a list")


class TestBufferBounds(unittest.TestCase):
    def test_buffer_cap_escalates_to_resynchronization(self):
        """Regression: SKILL.md names unbounded buffer growth as a pitfall."""
        clock = FakeClock()
        handler = build_handler(clock=clock, max_buffered_packets=3)
        handler.ingest_packet(MulticastChannel.CHANNEL_A, 100, b"A100")

        for seq in (102, 103, 104):
            self.assertEqual(
                handler.ingest_packet(MulticastChannel.CHANNEL_A, seq, b"x").disposition,
                PacketDisposition.BUFFERED,
            )

        overflow = handler.ingest_packet(MulticastChannel.CHANNEL_A, 105, b"x")
        self.assertEqual(overflow.disposition, PacketDisposition.DROPPED_BUFFER_FULL)
        self.assertTrue(overflow.requires_resynchronization)
        self.assertTrue(handler.requires_resynchronization)
        self.assertEqual(len(handler.out_of_order_buffer), 3)

        handler.resynchronize(200)
        self.assertFalse(handler.requires_resynchronization)


class TestSequenceReset(unittest.TestCase):
    """CME MDP 3.0 resets MsgSeqNum weekly and on Channel Reset."""

    def setUp(self):
        self.clock = FakeClock()
        self.handler = build_handler(
            initial_sequence=4_000_000, clock=self.clock, sequence_reset_threshold=1_000_000
        )

    def test_restart_is_reported_not_silently_swallowed(self):
        """Regression: a handler that calls this a duplicate goes deaf for the week."""
        res = self.handler.ingest_packet(MulticastChannel.CHANNEL_A, 1, b"WEEK_START")
        self.assertEqual(res.disposition, PacketDisposition.RESET_SUSPECTED)
        self.assertEqual(res.processed_packets, [])
        self.assertEqual(self.handler.expected_sequence, 4_000_000)

    def test_reset_sequence_restores_processing(self):
        self.handler.reset_sequence(1)
        res = self.handler.ingest_packet(MulticastChannel.CHANNEL_A, 1, b"WEEK_START")
        self.assertEqual(res.disposition, PacketDisposition.PROCESSED)
        self.assertEqual(self.handler.expected_sequence, 2)

    def test_ordinary_lagging_duplicate_is_not_mistaken_for_a_reset(self):
        res = self.handler.ingest_packet(MulticastChannel.CHANNEL_B, 3_999_998, b"late")
        self.assertEqual(res.disposition, PacketDisposition.DUPLICATE)


class TestMoldUdp64SequenceSpace(unittest.TestCase):
    """MoldUDP64 numbers messages, not packets (Nasdaq MoldUDP64 spec, Header)."""

    def setUp(self):
        self.clock = FakeClock()
        self.handler = build_handler(initial_sequence=1, clock=self.clock)

    def test_message_count_advances_the_sequence_space(self):
        res = self.handler.ingest_packet(MulticastChannel.CHANNEL_A, 1, b"3msgs", 3)
        self.assertEqual(res.disposition, PacketDisposition.PROCESSED)
        # Messages 1, 2 and 3 were carried, so message 4 is expected next.
        self.assertEqual(self.handler.expected_sequence, 4)

        follow = self.handler.ingest_packet(MulticastChannel.CHANNEL_A, 4, b"2msgs", 2)
        self.assertEqual(follow.disposition, PacketDisposition.PROCESSED)
        self.assertEqual(self.handler.expected_sequence, 6)

    def test_gap_is_measured_in_messages(self):
        self.handler.ingest_packet(MulticastChannel.CHANNEL_A, 1, b"3msgs", 3)
        res = self.handler.ingest_packet(MulticastChannel.CHANNEL_A, 9, b"x", 1)
        self.assertEqual(res.missing_range, (4, 8))

    def test_twin_packet_covering_processed_messages_is_a_duplicate(self):
        self.handler.ingest_packet(MulticastChannel.CHANNEL_A, 1, b"3msgs", 3)
        twin = self.handler.ingest_packet(MulticastChannel.CHANNEL_B, 2, b"2msgs", 2)
        self.assertEqual(twin.disposition, PacketDisposition.DUPLICATE)

    def test_straddling_recovery_packet_reports_how_many_messages_to_skip(self):
        """A Re-request Server returns whole packets that need not align."""
        self.handler.ingest_packet(MulticastChannel.CHANNEL_A, 1, b"3msgs", 3)
        res = self.handler.ingest_packet(MulticastChannel.RECOVERY, 2, b"4msgs", 4)
        self.assertEqual(res.disposition, PacketDisposition.PARTIAL_OVERLAP)
        # Messages 2 and 3 are already processed; 4 and 5 are new.
        self.assertEqual(res.first_new_message_index, 2)
        self.assertEqual(self.handler.expected_sequence, 6)

    def test_multi_message_fill_purges_superseded_buffered_packets(self):
        self.handler.ingest_packet(MulticastChannel.CHANNEL_A, 1, b"m1", 1)
        self.handler.ingest_packet(MulticastChannel.CHANNEL_A, 4, b"m4", 1)
        self.handler.ingest_packet(MulticastChannel.CHANNEL_A, 5, b"m5", 1)
        self.assertEqual(sorted(self.handler.out_of_order_buffer), [4, 5])

        # A recovery packet covering 2..5 supersedes both buffered packets.
        self.handler.apply_recovery_packets(
            [MulticastPacket(MulticastChannel.RECOVERY, 2, b"2-5", 0.0, 4)]
        )
        self.assertEqual(self.handler.expected_sequence, 6)
        self.assertEqual(self.handler.out_of_order_buffer, {})
        self.assertIsNone(self.handler.pending_gap)


class TestMalformedInputContainment(unittest.TestCase):
    def test_absurd_message_count_does_not_stall_the_ingest_path(self):
        """A bogus header field must not turn buffer purging into a huge loop."""
        handler = build_handler(initial_sequence=1)
        handler.ingest_packet(MulticastChannel.CHANNEL_A, 5, b"x")
        res = handler.ingest_packet(MulticastChannel.CHANNEL_A, 1, b"x", 50_000_000)
        self.assertEqual(res.disposition, PacketDisposition.PROCESSED)
        self.assertEqual(handler.expected_sequence, 50_000_001)
        # The packet covered the buffered one, so nothing is left holding the stream.
        self.assertEqual(handler.out_of_order_buffer, {})
        self.assertIsNone(handler.pending_gap)


class TestGapNarrowing(unittest.TestCase):
    def test_partial_fill_re_anchors_the_gap_start(self):
        clock = FakeClock()
        handler = build_handler(clock=clock)
        handler.ingest_packet(MulticastChannel.CHANNEL_A, 100, b"A100")
        handler.ingest_packet(MulticastChannel.CHANNEL_A, 104, b"A104")
        self.assertEqual(handler.pending_gap.as_range(), (101, 103))

        res = handler.ingest_packet(MulticastChannel.CHANNEL_B, 101, b"B101")
        self.assertEqual(res.missing_range, (102, 103))
        self.assertTrue(res.is_gap_detected)

        clock.advance(WINDOW)
        request = handler.poll_recovery()
        # Only the still-missing range is requested, not the original one.
        self.assertEqual((request.start, request.end), (102, 103))


class TestInputValidation(unittest.TestCase):
    def test_constructor_rejects_invalid_arguments(self):
        with self.assertRaises(TypeError):
            ExchangeMulticastFeedHandler()  # arbitration_window_s is required
        with self.assertRaises(ValueError):
            build_handler(-1)
        with self.assertRaises(TypeError):
            build_handler("100")
        with self.assertRaises(ValueError):
            build_handler(100, arbitration_window_s=-0.001)
        with self.assertRaises(ValueError):
            build_handler(100, max_buffered_packets=0)
        with self.assertRaises(ValueError):
            build_handler(100, sequence_reset_threshold=0)

    def test_ingest_rejects_invalid_arguments(self):
        handler = build_handler()
        with self.assertRaises(TypeError):
            handler.ingest_packet("CHANNEL_A", 100, b"x")
        with self.assertRaises(TypeError):
            handler.ingest_packet(MulticastChannel.CHANNEL_A, "100", b"x")
        with self.assertRaises(ValueError):
            handler.ingest_packet(MulticastChannel.CHANNEL_A, -1, b"x")
        with self.assertRaises(TypeError):
            handler.ingest_packet(MulticastChannel.CHANNEL_A, 100, "not bytes")
        with self.assertRaises(ValueError):
            handler.ingest_packet(MulticastChannel.CHANNEL_A, 100, b"x", 0)
        with self.assertRaises(TypeError):
            handler.ingest_packet(MulticastChannel.CHANNEL_A, 100, b"x", 1.5)

    def test_booleans_are_not_accepted_as_sequence_numbers(self):
        handler = build_handler()
        with self.assertRaises(TypeError):
            handler.ingest_packet(MulticastChannel.CHANNEL_A, True, b"x")

    def test_resynchronize_rejects_invalid_arguments(self):
        handler = build_handler()
        with self.assertRaises(ValueError):
            handler.resynchronize(-1)
        with self.assertRaises(TypeError):
            handler.resynchronize(1.0)


class TestStateHygiene(unittest.TestCase):
    def test_handler_retains_no_per_packet_history(self):
        """Regression: a colocated handler cannot keep every packet it has seen."""
        handler = build_handler()
        for seq in range(100, 1100):
            handler.ingest_packet(MulticastChannel.CHANNEL_A, seq, b"x")
        self.assertEqual(handler.expected_sequence, 1100)
        self.assertEqual(handler.out_of_order_buffer, {})
        self.assertEqual(handler.recovery_requests, [])
        self.assertFalse(
            any(isinstance(v, dict) and len(v) > 16 for v in vars(handler).values())
        )

    def test_payload_is_copied_so_a_reused_receive_buffer_cannot_mutate_it(self):
        handler = build_handler()
        scratch = bytearray(b"TICK_100")
        res = handler.ingest_packet(MulticastChannel.CHANNEL_A, 100, scratch)
        scratch[0:4] = b"ZZZZ"
        self.assertEqual(res.processed_packets[0].payload, b"TICK_100")


if __name__ == "__main__":
    unittest.main()
