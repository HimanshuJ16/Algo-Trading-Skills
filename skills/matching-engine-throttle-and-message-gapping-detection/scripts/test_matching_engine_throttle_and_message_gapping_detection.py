import logging
import time
import unittest

from matching_engine_throttle_and_message_gapping_detection import (
    DIRECTIVE_BLOCK_OUTBOUND,
    DIRECTIVE_REQUEST_RETRANSMIT,
    DIRECTIVE_SESSION_RESYNC,
    DIRECTIVE_TERMINATE_SESSION,
    InboundMessageRecord,
    MatchingEngineMonitorEngine,
    OutboundMessageRecord,
    SEQUENCE_CONTIGUOUS,
    STATUS_NORMAL,
    STATUS_SEQUENCE_GAP,
    STATUS_SEQUENCE_REGRESSION,
    STATUS_THROTTLE_WARNING,
    STATUS_THROTTLED,
)

# A fixed anchor, so nothing in this suite depends on the wall clock.
T0 = 1_700_000_000.0

_MODULE_LOGGER = logging.getLogger("matching_engine_throttle_and_message_gapping_detection")
_SAVED_LEVEL = _MODULE_LOGGER.level


def setUpModule():
    """
    Silence the module under test for the duration of this file only.

    Most cases here deliberately drive CRITICAL paths. Raising the level on this one
    logger (rather than calling ``logging.disable``) keeps the suppression local, so the
    repo-wide runner in ``tools/run_all_tests.py`` does not inherit it for the next skill.
    """
    _MODULE_LOGGER.setLevel(logging.CRITICAL + 1)


def tearDownModule():
    _MODULE_LOGGER.setLevel(_SAVED_LEVEL)


def outbound(session, count, at, message_type="NEW_ORDER"):
    return [OutboundMessageRecord(session, message_type, at, i) for i in range(count)]


def inbound(session, seq_ids, at=T0, message_type="EXEC_REPORT", poss_dup=False):
    return [InboundMessageRecord(session, message_type, s, at, poss_dup) for s in seq_ids]


class TestOutboundThrottleDetection(unittest.TestCase):

    def setUp(self):
        self.engine = MatchingEngineMonitorEngine(max_allowed_mps=500.0, warning_threshold_pct=80.0)

    def test_outbound_rate_limit_throttle_warning(self):
        # 420 msgs in a 1s window = 84% of the 500 limit -> warn, but do not block.
        report = self.engine.audit_matching_engine_session(
            "CME_SESSION_01", outbound("CME_SESSION_01", 420, T0 - 0.1),
            inbound("CME_SESSION_01", [1]), as_of_epoch=T0)

        self.assertEqual(report.status, STATUS_THROTTLE_WARNING)
        self.assertEqual(report.throttle_status, STATUS_THROTTLE_WARNING)
        self.assertEqual(report.outbound_rate_per_sec, 420.0)
        self.assertEqual(report.peak_window_rate_per_sec, 420.0)
        self.assertFalse(report.is_throttled)
        self.assertNotIn(DIRECTIVE_BLOCK_OUTBOUND, report.directives)

    def test_exchange_rate_limit_hard_block(self):
        report = self.engine.audit_matching_engine_session(
            "CME_SESSION_01", outbound("CME_SESSION_01", 550, T0 - 0.1), [], as_of_epoch=T0)

        self.assertEqual(report.status, STATUS_THROTTLED)
        self.assertTrue(report.is_throttled)
        self.assertIn(DIRECTIVE_BLOCK_OUTBOUND, report.directives)

    def test_threshold_is_a_non_strict_lower_bound(self):
        """Exactly at the limit blocks; one message below it only warns."""
        at_limit = self.engine.audit_matching_engine_session(
            "S", outbound("S", 500, T0), [], as_of_epoch=T0)
        below = self.engine.audit_matching_engine_session(
            "S", outbound("S", 499, T0), [], as_of_epoch=T0)
        at_warning = self.engine.audit_matching_engine_session(
            "S", outbound("S", 400, T0), [], as_of_epoch=T0)
        below_warning = self.engine.audit_matching_engine_session(
            "S", outbound("S", 399, T0), [], as_of_epoch=T0)

        self.assertTrue(at_limit.is_throttled)
        self.assertEqual(below.throttle_status, STATUS_THROTTLE_WARNING)
        self.assertEqual(at_warning.throttle_status, STATUS_THROTTLE_WARNING)
        self.assertEqual(below_warning.throttle_status, STATUS_NORMAL)

    def test_replayed_log_still_reports_its_burst(self):
        """
        Regression: the verdict must come from the data, not from the wall clock.

        The previous implementation anchored the window to ``time.time()``, so replaying a
        captured 900-message burst an hour later counted zero messages and reported
        MATCHING_ENGINE_NORMAL -- a silent false negative on the exact event the module
        exists to catch.
        """
        report = self.engine.audit_matching_engine_session(
            "S", outbound("S", 900, T0 - 3600.0), [], as_of_epoch=T0)

        self.assertEqual(report.status, STATUS_THROTTLED)
        self.assertEqual(report.peak_window_rate_per_sec, 900.0)
        self.assertEqual(report.outbound_rate_per_sec, 0.0)   # trailing window is empty
        self.assertAlmostEqual(report.newest_outbound_age_sec, 3600.0)

    def test_peak_window_beats_a_diluted_average(self):
        """
        600 messages inside one 200 ms burst, spread across a 10 s log.

        Counted over the whole log that is 60 msgs/sec; counted over the worst 1 s window
        it is 600 msgs/sec, which is what the venue's counter sees.
        """
        burst = [OutboundMessageRecord("S", "NEW_ORDER", T0 + 5.0 + i * 0.0001, i)
                 for i in range(600)]
        padding = [OutboundMessageRecord("S", "NEW_ORDER", T0 + i * 1.0, 900 + i)
                   for i in range(10)]

        report = self.engine.audit_matching_engine_session(
            "S", burst + padding, [], window_seconds=1.0, as_of_epoch=T0 + 10.0)

        self.assertEqual(report.peak_window_rate_per_sec, 601.0)  # burst + one padding msg
        self.assertTrue(report.is_throttled)

    def test_window_length_changes_the_verdict(self):
        """480 messages in 10 ms: inside a 1 s counter, far outside a 100 ms counter."""
        msgs = [OutboundMessageRecord("S", "NEW_ORDER", T0 + i * 0.00002, i) for i in range(480)]

        one_second = self.engine.audit_matching_engine_session(
            "S", msgs, [], window_seconds=1.0, as_of_epoch=T0 + 1.0)
        hundred_ms = self.engine.audit_matching_engine_session(
            "S", msgs, [], window_seconds=0.1, as_of_epoch=T0 + 1.0)

        self.assertEqual(one_second.peak_window_rate_per_sec, 480.0)
        self.assertEqual(hundred_ms.peak_window_rate_per_sec, 4800.0)
        self.assertFalse(one_second.is_throttled)
        self.assertTrue(hundred_ms.is_throttled)

    def test_future_dated_messages_are_counted_and_flagged(self):
        report = self.engine.audit_matching_engine_session(
            "S", outbound("S", 900, T0 + 86400.0), [], as_of_epoch=T0)

        self.assertTrue(report.is_throttled)
        self.assertEqual(report.future_dated_outbound_count, 900)

    def test_empty_log_is_normal(self):
        report = self.engine.audit_matching_engine_session("S", [], [], as_of_epoch=T0)

        self.assertEqual(report.status, STATUS_NORMAL)
        self.assertEqual(report.peak_window_rate_per_sec, 0.0)
        self.assertIsNone(report.newest_outbound_age_sec)
        self.assertEqual(report.directives, ())


class TestSessionIsolation(unittest.TestCase):

    def test_other_sessions_traffic_is_excluded_not_pooled(self):
        """
        Regression: rate limits and sequence numbers are per session.

        The previous implementation never compared a record's ``session_id`` against the
        audited session, so another session's 600-message burst and its sequence 900
        produced a throttle rate and a 899-message phantom gap on this session.
        """
        engine = MatchingEngineMonitorEngine()
        report = engine.audit_matching_engine_session(
            "MINE", outbound("OTHER", 600, T0), inbound("OTHER", [900]), as_of_epoch=T0)

        self.assertEqual(report.peak_window_rate_per_sec, 0.0)
        self.assertFalse(report.is_throttled)
        self.assertFalse(report.has_sequence_gap)
        self.assertEqual(report.records_excluded_other_session, 601)
        self.assertEqual(report.outbound_message_count, 0)

    def test_sequence_state_is_kept_per_session(self):
        engine = MatchingEngineMonitorEngine()
        engine.audit_matching_engine_session("A", [], inbound("A", [1, 2, 3]), as_of_epoch=T0)
        report_b = engine.audit_matching_engine_session("B", [], inbound("B", [1]), as_of_epoch=T0)

        self.assertEqual(engine.session_expected_seq["A"], 4)
        self.assertEqual(report_b.next_expected_seq_id, 2)


class TestSequenceGapDetection(unittest.TestCase):

    def setUp(self):
        self.engine = MatchingEngineMonitorEngine()

    def test_inbound_sequence_gap_detection(self):
        report = self.engine.audit_matching_engine_session(
            "CME_SESSION_02", [], inbound("CME_SESSION_02", [105]), as_of_epoch=T0)

        self.assertEqual(report.status, STATUS_SEQUENCE_GAP)
        self.assertTrue(report.has_sequence_gap)
        self.assertIsNotNone(report.sequence_gap_details)
        self.assertEqual(report.sequence_gap_details.missing_seq_start, 1)
        self.assertEqual(report.sequence_gap_details.missing_seq_end, 104)
        self.assertEqual(report.sequence_gap_details.gap_size, 104)
        self.assertIn(DIRECTIVE_REQUEST_RETRANSMIT, report.directives)

    def test_expected_counter_holds_at_the_gap_until_it_is_filled(self):
        """
        Regression: a gap must stay open until the retransmission actually arrives.

        The previous implementation jumped the expected counter past the gap the moment it
        saw the high sequence number, so the very next audit reported a healthy stream
        while 104 execution reports had never been delivered.
        """
        first = self.engine.audit_matching_engine_session(
            "S", [], inbound("S", [105]), as_of_epoch=T0)
        self.assertEqual(first.next_expected_seq_id, 1)

        # Nothing new arrives: the gap is still owed.
        second = self.engine.audit_matching_engine_session("S", [], [], as_of_epoch=T0)
        self.assertTrue(second.has_sequence_gap)
        self.assertEqual(second.sequence_gap_details.missing_seq_start, 1)
        self.assertEqual(second.sequence_gap_details.missing_seq_end, 104)

    def test_retransmission_closes_the_gap_and_drains_the_buffer(self):
        self.engine.audit_matching_engine_session("S", [], inbound("S", [4]), as_of_epoch=T0)
        report = self.engine.audit_matching_engine_session(
            "S", [], inbound("S", [1, 2, 3], poss_dup=True), as_of_epoch=T0)

        self.assertFalse(report.has_sequence_gap)
        self.assertEqual(report.sequence_status, SEQUENCE_CONTIGUOUS)
        self.assertEqual(report.next_expected_seq_id, 5)   # 4 was buffered and drained
        self.assertEqual(report.directives, ())

    def test_every_gap_in_a_batch_is_reported(self):
        """
        Regression: the previous implementation stopped at the first gap.

        Sequences 1,2,5,6,9,10 hide two holes. Reporting only [3,4] leaves [7,8] silently
        unrecovered.
        """
        report = self.engine.audit_matching_engine_session(
            "S", [], inbound("S", [1, 2, 5, 6, 9, 10]), as_of_epoch=T0)

        ranges = [(g.missing_seq_start, g.missing_seq_end) for g in report.sequence_gaps]
        self.assertEqual(ranges, [(3, 4), (7, 8)])
        self.assertEqual(report.next_expected_seq_id, 3)
        self.assertEqual(report.sequence_gap_details.missing_seq_start, 3)

    def test_arrival_order_is_preserved_not_sorted(self):
        """
        Regression: the previous implementation sorted the batch by sequence number.

        Sorting is look-ahead -- it lets a message that arrived later fill a hole that was
        open when the earlier one landed, so a genuine loss reads as contiguous. Here the
        reordering really does resolve, but it must be visible as reordering.
        """
        report = self.engine.audit_matching_engine_session(
            "S", [], inbound("S", [5, 1, 2, 3, 4]), as_of_epoch=T0)

        self.assertFalse(report.has_sequence_gap)
        self.assertEqual(report.next_expected_seq_id, 6)
        self.assertEqual(report.out_of_order_ahead_count, 1)

    def test_contiguous_stream_reports_nothing(self):
        report = self.engine.audit_matching_engine_session(
            "S", [], inbound("S", [1, 2, 3, 4, 5]), as_of_epoch=T0)

        self.assertEqual(report.status, STATUS_NORMAL)
        self.assertEqual(report.sequence_status, SEQUENCE_CONTIGUOUS)
        self.assertEqual(report.next_expected_seq_id, 6)

    def test_duplicate_ahead_of_the_gap_is_counted_once(self):
        report = self.engine.audit_matching_engine_session(
            "S", [], inbound("S", [10, 10, 10]), as_of_epoch=T0)

        self.assertEqual(len(report.sequence_gaps), 1)
        self.assertEqual(report.duplicate_inbound_count, 2)
        self.assertEqual(report.out_of_order_ahead_count, 1)


class TestRetransmitSizing(unittest.TestCase):

    def test_gap_within_the_cap_needs_one_request(self):
        engine = MatchingEngineMonitorEngine()
        report = engine.audit_matching_engine_session(
            "S", [], inbound("S", [2501]), as_of_epoch=T0)
        gap = report.sequence_gap_details

        self.assertEqual(gap.gap_size, 2500)
        self.assertEqual(gap.retransmit_requests_required, 1)
        self.assertFalse(gap.exceeds_single_retransmit_limit)

    def test_one_message_past_the_cap_needs_two_requests(self):
        engine = MatchingEngineMonitorEngine()
        report = engine.audit_matching_engine_session(
            "S", [], inbound("S", [2502]), as_of_epoch=T0)
        gap = report.sequence_gap_details

        self.assertEqual(gap.gap_size, 2501)
        self.assertEqual(gap.retransmit_requests_required, 2)
        self.assertTrue(gap.exceeds_single_retransmit_limit)

    def test_large_gap_request_count_is_a_ceiling_division(self):
        engine = MatchingEngineMonitorEngine()
        report = engine.audit_matching_engine_session(
            "S", [], inbound("S", [9001]), as_of_epoch=T0)
        gap = report.sequence_gap_details

        # 9000 missing / 2500 per request -> 4 requests (3 full + 1 partial), not 3.
        self.assertEqual(gap.gap_size, 9000)
        self.assertEqual(gap.retransmit_requests_required, 4)

    def test_venue_specific_cap_is_honoured(self):
        engine = MatchingEngineMonitorEngine(max_retransmit_request_count=100)
        report = engine.audit_matching_engine_session(
            "S", [], inbound("S", [251]), as_of_epoch=T0)

        self.assertEqual(report.sequence_gap_details.retransmit_requests_required, 3)


class TestSequenceRegression(unittest.TestCase):

    def setUp(self):
        self.engine = MatchingEngineMonitorEngine()
        self.engine.audit_matching_engine_session(
            "S", [], inbound("S", [1, 2, 3]), as_of_epoch=T0)

    def test_low_sequence_without_possdup_demands_session_termination(self):
        """
        Regression: the previous implementation silently discarded any sequence number
        below the expected one. The FIX session layer requires Logout with
        SessionStatus=9 and transport termination -- silently dropping it is how a stale
        execution report gets applied twice.
        """
        report = self.engine.audit_matching_engine_session(
            "S", [], inbound("S", [2]), as_of_epoch=T0)

        self.assertEqual(report.status, STATUS_SEQUENCE_REGRESSION)
        self.assertTrue(report.has_sequence_regression)
        self.assertEqual(len(report.sequence_regressions), 1)
        self.assertEqual(report.sequence_regressions[0].received_seq_id, 2)
        self.assertEqual(report.sequence_regressions[0].expected_seq_id, 4)
        self.assertIn(DIRECTIVE_TERMINATE_SESSION, report.directives)

    def test_low_sequence_with_possdup_is_an_expected_duplicate(self):
        report = self.engine.audit_matching_engine_session(
            "S", [], inbound("S", [2], poss_dup=True), as_of_epoch=T0)

        self.assertFalse(report.has_sequence_regression)
        self.assertEqual(report.sequence_status, SEQUENCE_CONTIGUOUS)
        self.assertEqual(report.duplicate_inbound_count, 1)
        self.assertEqual(report.next_expected_seq_id, 4)

    def test_a_reset_is_never_inferred_from_a_low_sequence_number(self):
        """Sequence 1 after expecting 4 is a regression, not an automatic session reset."""
        report = self.engine.audit_matching_engine_session(
            "S", [], inbound("S", [1]), as_of_epoch=T0)

        self.assertTrue(report.has_sequence_regression)
        self.assertEqual(report.next_expected_seq_id, 4)   # counter is NOT rewound

    def test_explicit_reset_re_anchors_the_counter(self):
        self.engine.reset_session_sequence("S", next_expected_seq_id=1)
        report = self.engine.audit_matching_engine_session(
            "S", [], inbound("S", [1, 2]), as_of_epoch=T0)

        self.assertFalse(report.has_sequence_regression)
        self.assertFalse(report.has_sequence_gap)
        self.assertEqual(report.next_expected_seq_id, 3)

    def test_explicit_reset_discards_a_stale_open_gap(self):
        engine = MatchingEngineMonitorEngine()
        engine.audit_matching_engine_session("R", [], inbound("R", [50]), as_of_epoch=T0)
        engine.reset_session_sequence("R", next_expected_seq_id=1)
        report = engine.audit_matching_engine_session("R", [], inbound("R", [1]), as_of_epoch=T0)

        self.assertFalse(report.has_sequence_gap)
        self.assertEqual(report.next_expected_seq_id, 2)


class TestStatusPrecedenceAndDirectives(unittest.TestCase):

    def test_a_throttle_block_is_not_masked_by_a_sequence_gap(self):
        """
        Regression: the previous implementation overwrote the throttle status with the gap
        status, so an agent branching on ``status`` kept submitting into a session the
        venue was about to terminate.
        """
        engine = MatchingEngineMonitorEngine()
        report = engine.audit_matching_engine_session(
            "S", outbound("S", 700, T0), inbound("S", [50]), as_of_epoch=T0)

        self.assertEqual(report.status, STATUS_THROTTLED)
        self.assertTrue(report.is_throttled)
        self.assertTrue(report.has_sequence_gap)
        self.assertEqual(report.throttle_status, STATUS_THROTTLED)
        self.assertEqual(report.sequence_status, STATUS_SEQUENCE_GAP)
        self.assertIn(DIRECTIVE_BLOCK_OUTBOUND, report.directives)
        self.assertIn(DIRECTIVE_REQUEST_RETRANSMIT, report.directives)

    def test_regression_outranks_a_throttle_block(self):
        engine = MatchingEngineMonitorEngine()
        engine.audit_matching_engine_session("S", [], inbound("S", [1, 2]), as_of_epoch=T0)
        report = engine.audit_matching_engine_session(
            "S", outbound("S", 700, T0), inbound("S", [1]), as_of_epoch=T0)

        self.assertEqual(report.status, STATUS_SEQUENCE_REGRESSION)
        self.assertTrue(report.is_throttled)          # still independently true
        self.assertIn(DIRECTIVE_BLOCK_OUTBOUND, report.directives)
        self.assertIn(DIRECTIVE_TERMINATE_SESSION, report.directives)

    def test_gap_buffer_overflow_escalates_to_a_resync(self):
        engine = MatchingEngineMonitorEngine(max_buffered_ahead=3)
        report = engine.audit_matching_engine_session(
            "S", [], inbound("S", [10, 20, 30, 40, 50]), as_of_epoch=T0)

        self.assertTrue(report.buffered_ahead_overflow)
        self.assertIn(DIRECTIVE_SESSION_RESYNC, report.directives)


class TestSessionStateLifecycle(unittest.TestCase):

    def test_forget_session_drops_retained_state(self):
        engine = MatchingEngineMonitorEngine()
        engine.audit_matching_engine_session("S", [], inbound("S", [10]), as_of_epoch=T0)
        self.assertIn("S", engine.session_expected_seq)

        engine.forget_session("S")
        self.assertNotIn("S", engine.session_expected_seq)

        # A later audit starts from a clean slate, expecting sequence 1 again.
        report = engine.audit_matching_engine_session("S", [], inbound("S", [1]), as_of_epoch=T0)
        self.assertFalse(report.has_sequence_gap)
        self.assertEqual(report.next_expected_seq_id, 2)

    def test_forget_session_on_an_unknown_id_is_a_no_op(self):
        engine = MatchingEngineMonitorEngine()
        engine.forget_session("never-seen")   # must not raise
        self.assertEqual(engine.session_expected_seq, {})

    def test_status_is_never_an_inbound_only_label(self):
        """A clean session reports MATCHING_ENGINE_NORMAL, not SEQUENCE_CONTIGUOUS."""
        engine = MatchingEngineMonitorEngine()
        report = engine.audit_matching_engine_session(
            "S", outbound("S", 1, T0), inbound("S", [1]), as_of_epoch=T0)

        self.assertEqual(report.status, STATUS_NORMAL)
        self.assertEqual(report.sequence_status, SEQUENCE_CONTIGUOUS)


class TestInputValidation(unittest.TestCase):

    def test_invalid_constructor_arguments_raise(self):
        for kwargs in (
            {"max_allowed_mps": 0.0},
            {"max_allowed_mps": -500.0},
            {"max_allowed_mps": float("nan")},
            {"max_allowed_mps": float("inf")},
            {"warning_threshold_pct": 0.0},
            {"warning_threshold_pct": -1.0},
            {"warning_threshold_pct": 100.1},
            {"max_retransmit_request_count": 0},
            {"max_buffered_ahead": 0},
        ):
            with self.subTest(**kwargs):
                with self.assertRaises(ValueError):
                    MatchingEngineMonitorEngine(**kwargs)

    def test_non_integer_counts_raise_type_error(self):
        with self.assertRaises(TypeError):
            MatchingEngineMonitorEngine(max_retransmit_request_count=2500.0)

    def test_invalid_audit_arguments_raise(self):
        engine = MatchingEngineMonitorEngine()
        for kwargs in (
            {"session_id": ""},
            {"session_id": "   "},
            {"window_seconds": 0.0},
            {"window_seconds": -1.0},
            {"window_seconds": float("nan")},
            {"as_of_epoch": float("inf")},
        ):
            with self.subTest(**kwargs):
                call = {"session_id": "S", "outbound_messages": [], "inbound_messages": []}
                call.update(kwargs)
                with self.assertRaises(ValueError):
                    engine.audit_matching_engine_session(**call)

    def test_non_finite_timestamp_is_rejected_at_construction(self):
        """A NaN timestamp used to be silently dropped, reporting a burst as 0 msgs/sec."""
        for bad in (float("nan"), float("inf"), float("-inf")):
            with self.subTest(timestamp=bad):
                with self.assertRaises(ValueError):
                    OutboundMessageRecord("S", "NEW_ORDER", bad, 1)
                with self.assertRaises(ValueError):
                    InboundMessageRecord("S", "EXEC_REPORT", 1, bad)

    def test_inbound_sequence_numbers_must_be_positive_ints(self):
        for bad in (0, -1):
            with self.subTest(sequence_id=bad):
                with self.assertRaises(ValueError):
                    InboundMessageRecord("S", "EXEC_REPORT", bad, T0)

    def test_a_swapped_sequence_and_timestamp_fails_loudly(self):
        """InboundMessageRecord takes (session, type, sequence, timestamp) in that order."""
        with self.assertRaises(TypeError):
            InboundMessageRecord("S", "EXEC_REPORT", T0, 105)

    def test_empty_identifiers_are_rejected(self):
        with self.assertRaises(ValueError):
            OutboundMessageRecord("", "NEW_ORDER", T0, 1)
        with self.assertRaises(ValueError):
            InboundMessageRecord("S", "", 1, T0)

    def test_poss_dup_must_be_a_bool(self):
        with self.assertRaises(TypeError):
            InboundMessageRecord("S", "EXEC_REPORT", 1, T0, "Y")

    def test_records_and_report_are_immutable(self):
        engine = MatchingEngineMonitorEngine()
        report = engine.audit_matching_engine_session("S", [], [], as_of_epoch=T0)
        with self.assertRaises(Exception):
            report.is_throttled = True
        with self.assertRaises(Exception):
            OutboundMessageRecord("S", "NEW_ORDER", T0, 1).timestamp_epoch = 0.0


class TestDeterminism(unittest.TestCase):

    def test_verdict_does_not_depend_on_the_wall_clock(self):
        engine_a = MatchingEngineMonitorEngine()
        engine_b = MatchingEngineMonitorEngine()
        msgs = outbound("S", 600, T0)

        a = engine_a.audit_matching_engine_session("S", msgs, [], as_of_epoch=T0)
        b = engine_b.audit_matching_engine_session("S", msgs, [], as_of_epoch=T0 + 10_000.0)

        self.assertEqual(a.peak_window_rate_per_sec, b.peak_window_rate_per_sec)
        self.assertEqual(a.is_throttled, b.is_throttled)
        self.assertEqual(a.status, b.status)

    def test_default_as_of_uses_the_wall_clock_for_the_trailing_rate_only(self):
        engine = MatchingEngineMonitorEngine()
        now = time.time()
        report = engine.audit_matching_engine_session("S", outbound("S", 600, now), [])

        self.assertTrue(report.is_throttled)
        self.assertEqual(report.peak_window_rate_per_sec, 600.0)
        self.assertLess(abs(report.as_of_epoch - now), 5.0)


if __name__ == "__main__":
    unittest.main()
