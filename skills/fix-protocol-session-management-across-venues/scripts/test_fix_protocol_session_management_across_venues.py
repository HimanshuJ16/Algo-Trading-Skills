import unittest

from fix_protocol_session_management_across_venues import (
    FixMessage,
    FixProtocolSessionManagerEngine,
    FixSessionAuditReport,
    FixSessionConfig,
    MSG_HEARTBEAT,
    MSG_LOGON,
    MSG_LOGOUT,
    MSG_REJECT,
    MSG_RESEND_REQUEST,
    MSG_SEQUENCE_RESET,
    MSG_TEST_REQUEST,
    STATE_DISCONNECTED,
    STATE_LOGGED_IN,
    STATE_LOGON_SENT,
    STATE_LOGOUT_SENT,
    STATE_RESEND_REQUEST_SENT,
    STATUS_MESSAGE_DISCARDED,
    STATUS_MESSAGE_REJECTED,
    STATUS_SESSION_ACTIVE,
    STATUS_SESSION_TERMINATED,
    fix_utc_timestamp,
)

VENUE = "NASDAQ"
FIRM = "FIRM_ALPHA"


def inbound(msg_type, seq, body=None, poss_dup=False):
    """An inbound message from the venue on a correctly addressed session."""
    return FixMessage(
        msg_type,
        msg_seq_num=seq,
        sender_comp_id=VENUE,
        target_comp_id=FIRM,
        sending_time_iso="20260730-10:00:00.000",
        body_fields=body or {},
        poss_dup_flag=poss_dup,
    )


class FixSessionTestBase(unittest.TestCase):

    def setUp(self):
        self.now = 1_000_000.0
        config = FixSessionConfig(
            session_id="FIX_NASDAQ_01", sender_comp_id=FIRM, target_comp_id=VENUE
        )
        self.engine = FixProtocolSessionManagerEngine(config, clock=lambda: self.now)

    def establish(self):
        """Drive the engine to LOGGED_IN with expected_in_seq_num == 2."""
        self.engine.initiate_logon()
        self.engine.process_inbound_msg(inbound(MSG_LOGON, 1))
        return self.engine


class TestLogonHandshake(FixSessionTestBase):

    def test_logon_handshake_transitions_to_logged_in(self):
        logon_out = self.engine.initiate_logon()
        self.assertEqual(self.engine.state, STATE_LOGON_SENT)
        self.assertEqual(logon_out.msg_seq_num, 1)

        report, _ = self.engine.process_inbound_msg(inbound(MSG_LOGON, 1))

        self.assertEqual(report.state, STATE_LOGGED_IN)
        self.assertEqual(report.expected_in_seq_num, 2)
        self.assertFalse(report.gap_detected)

    def test_logon_with_gap_is_accepted_then_recovered(self):
        """Regression: a Logon carrying a gap must NOT swallow the missing range.

        The pre-fix engine set expected_in_seq_num = logon_seq + 1, silently
        discarding every message the venue sent in between with no recovery.
        """
        self.engine.initiate_logon()
        report, resend = self.engine.process_inbound_msg(inbound(MSG_LOGON, 7))

        self.assertTrue(report.gap_detected)
        # Logon is processed first -- the session comes up.
        self.assertEqual(self.engine.state, STATE_RESEND_REQUEST_SENT)
        # ...but the expected sequence is held at 1, not advanced to 8.
        self.assertEqual(self.engine.expected_in_seq_num, 1)
        self.assertIsNotNone(resend)
        self.assertEqual(resend.msg_type, MSG_RESEND_REQUEST)
        self.assertEqual(resend.body_fields[7], "1")
        self.assertEqual(resend.body_fields[16], "0")

    def test_logon_gap_without_auto_resend_still_holds_sequence(self):
        config = FixSessionConfig(
            session_id="S", sender_comp_id=FIRM, target_comp_id=VENUE,
            auto_resend_on_gap=False,
        )
        engine = FixProtocolSessionManagerEngine(config, clock=lambda: self.now)
        engine.initiate_logon()
        report, resend = engine.process_inbound_msg(inbound(MSG_LOGON, 7))

        self.assertTrue(report.gap_detected)
        self.assertIsNone(resend)
        self.assertEqual(engine.expected_in_seq_num, 1)

    def test_reset_seq_num_flag_resets_both_directions(self):
        engine = self.establish()
        for seq in (2, 3, 4):
            engine.process_inbound_msg(inbound("8", seq))
        self.assertEqual(engine.expected_in_seq_num, 5)

        logon = engine.initiate_logon(reset_seq_num=True)
        self.assertEqual(logon.msg_seq_num, 1)
        self.assertEqual(engine.expected_in_seq_num, 1)

    def test_unexpected_logon_on_established_session_terminates(self):
        engine = self.establish()
        report, response = engine.process_inbound_msg(inbound(MSG_LOGON, 2))

        self.assertEqual(report.status, STATUS_SESSION_TERMINATED)
        self.assertEqual(response.msg_type, MSG_LOGOUT)
        self.assertEqual(engine.state, STATE_LOGOUT_SENT)


class TestSequenceGapRecovery(FixSessionTestBase):

    def test_sequence_gap_triggers_resend_request(self):
        engine = self.establish()
        report, resend_out = engine.process_inbound_msg(inbound("D", 5))

        self.assertTrue(report.gap_detected)
        self.assertEqual(report.state, STATE_RESEND_REQUEST_SENT)
        self.assertIsNotNone(resend_out)
        self.assertEqual(resend_out.msg_type, MSG_RESEND_REQUEST)
        self.assertEqual(resend_out.body_fields[7], "2")

    def test_gap_does_not_advance_expected_sequence(self):
        """The gap-triggering message is held, not applied."""
        engine = self.establish()
        engine.process_inbound_msg(inbound("D", 5))
        self.assertEqual(engine.expected_in_seq_num, 2)

    def test_second_gap_message_does_not_trigger_resend_storm(self):
        """Regression: only one ResendRequest per outstanding gap.

        EndSeqNo=0 is open-ended, so the first request already covers every
        later message. Re-issuing per message is the resend-storm failure mode.
        """
        engine = self.establish()
        engine.process_inbound_msg(inbound("D", 5))
        report, resend = engine.process_inbound_msg(inbound("D", 6))

        self.assertTrue(report.gap_detected)
        self.assertIsNone(resend)
        self.assertEqual(engine.expected_in_seq_num, 2)

    def test_gap_fill_resynchronizes_session(self):
        engine = self.establish()
        engine.process_inbound_msg(inbound("D", 5))

        report, _ = engine.process_inbound_msg(
            inbound(MSG_SEQUENCE_RESET, 2, {36: "6", 123: "Y"})
        )

        self.assertEqual(report.state, STATE_LOGGED_IN)
        self.assertEqual(report.expected_in_seq_num, 6)
        self.assertFalse(report.gap_detected)


class TestSequenceResetSafety(FixSessionTestBase):
    """A SequenceReset may only ever increase the expected sequence number."""

    def _advance_to(self, engine, seq):
        for n in range(2, seq):
            engine.process_inbound_msg(inbound("8", n))

    def test_gap_fill_cannot_rewind_expected_sequence(self):
        """Regression: NewSeqNo below expected replays applied ExecutionReports."""
        engine = self.establish()
        self._advance_to(engine, 6)
        self.assertEqual(engine.expected_in_seq_num, 6)

        report, reject = engine.process_inbound_msg(
            inbound(MSG_SEQUENCE_RESET, 6, {36: "2", 123: "Y"})
        )

        self.assertEqual(report.status, STATUS_MESSAGE_REJECTED)
        self.assertEqual(engine.expected_in_seq_num, 6)
        self.assertEqual(reject.msg_type, MSG_REJECT)

    def test_reset_mode_cannot_rewind_expected_sequence(self):
        engine = self.establish()
        self._advance_to(engine, 6)

        report, reject = engine.process_inbound_msg(
            inbound(MSG_SEQUENCE_RESET, 6, {36: "2", 123: "N"})
        )

        self.assertEqual(report.status, STATUS_MESSAGE_REJECTED)
        self.assertEqual(engine.expected_in_seq_num, 6)
        self.assertEqual(reject.msg_type, MSG_REJECT)

    def test_reset_mode_ignores_its_own_msg_seq_num(self):
        """Regression: SequenceReset-Reset must not provoke a ResendRequest.

        The specification states that a Reset-mode SequenceReset arriving with
        an out-of-sequence MsgSeqNum "should not generate resend requests".
        The pre-fix engine ran gap detection first and answered with a
        ResendRequest, which the venue answers with another reset -- a loop.
        """
        engine = self.establish()

        report, response = engine.process_inbound_msg(
            inbound(MSG_SEQUENCE_RESET, 9, {36: "20", 123: "N"})
        )

        self.assertIsNone(response)
        self.assertEqual(report.status, STATUS_SESSION_ACTIVE)
        self.assertEqual(engine.expected_in_seq_num, 20)

    def test_reset_mode_clears_outstanding_resend_state(self):
        engine = self.establish()
        engine.process_inbound_msg(inbound("D", 5))
        self.assertEqual(engine.state, STATE_RESEND_REQUEST_SENT)

        engine.process_inbound_msg(inbound(MSG_SEQUENCE_RESET, 99, {36: "6"}))
        self.assertEqual(engine.state, STATE_LOGGED_IN)

    def test_malformed_new_seq_no_is_rejected_not_raised(self):
        """NewSeqNo arrives from the wire and may be anything at all."""
        engine = self.establish()

        for garbage in ("NaN", "", "1e5", None, "  "):
            with self.subTest(new_seq_no=garbage):
                body = {} if garbage is None else {36: garbage}
                report, _ = engine.process_inbound_msg(
                    inbound(MSG_SEQUENCE_RESET, 2, body)
                )
                self.assertEqual(report.status, STATUS_MESSAGE_REJECTED)
                self.assertEqual(engine.expected_in_seq_num, 2)


class TestDuplicateAndLowSequence(FixSessionTestBase):

    def test_poss_dup_replay_is_discarded_without_rewinding(self):
        """Regression: the duplicate-execution bug.

        A legitimate PossDup retransmission below the expected sequence must be
        dropped. The pre-fix engine fell through to normal processing and set
        expected = seq + 1, rewinding the session so every message after the
        duplicate was applied a second time.
        """
        engine = self.establish()
        for seq in (2, 3, 4, 5):
            engine.process_inbound_msg(inbound("8", seq))
        self.assertEqual(engine.expected_in_seq_num, 6)

        report, response = engine.process_inbound_msg(inbound("8", 3, poss_dup=True))

        self.assertEqual(report.status, STATUS_MESSAGE_DISCARDED)
        self.assertEqual(engine.expected_in_seq_num, 6)
        self.assertIsNone(response)
        self.assertEqual(engine.state, STATE_LOGGED_IN)

    def test_low_sequence_without_poss_dup_logs_out_and_terminates(self):
        """Regression: the report claimed termination while the engine kept going."""
        engine = self.establish()
        engine.process_inbound_msg(inbound("8", 2))
        engine.process_inbound_msg(inbound("8", 3))

        report, logout = engine.process_inbound_msg(inbound("8", 2))

        self.assertEqual(report.status, STATUS_SESSION_TERMINATED)
        self.assertEqual(logout.msg_type, MSG_LOGOUT)
        self.assertIn("MsgSeqNum too low", logout.body_fields[58])
        # The report's state must match the engine's actual state.
        self.assertEqual(report.state, engine.state)
        self.assertEqual(engine.state, STATE_LOGOUT_SENT)

    def test_poss_dup_at_expected_sequence_is_processed_normally(self):
        """A retransmission filling the actual gap is not a duplicate."""
        engine = self.establish()
        report, _ = engine.process_inbound_msg(inbound("8", 2, poss_dup=True))

        self.assertEqual(report.status, STATUS_SESSION_ACTIVE)
        self.assertEqual(engine.expected_in_seq_num, 3)


class TestSessionGuards(FixSessionTestBase):

    def test_application_message_before_logon_is_refused(self):
        report, _ = self.engine.process_inbound_msg(inbound("8", 1))

        self.assertEqual(report.status, STATUS_SESSION_TERMINATED)
        self.assertEqual(self.engine.state, STATE_DISCONNECTED)
        self.assertEqual(self.engine.expected_in_seq_num, 1)

    def test_sequence_reset_before_logon_cannot_establish_a_session(self):
        """Regression: a SequenceReset used to transition the engine to LOGGED_IN."""
        self.engine.process_inbound_msg(inbound(MSG_SEQUENCE_RESET, 2, {36: "9"}))

        self.assertEqual(self.engine.state, STATE_DISCONNECTED)
        self.assertEqual(self.engine.expected_in_seq_num, 1)

    def test_foreign_comp_ids_are_rejected(self):
        """A multi-venue deployment must not cross-apply another venue's stream."""
        engine = self.establish()
        foreign = FixMessage(
            "8", 2, sender_comp_id="OTHER_VENUE", target_comp_id="OTHER_FIRM",
            sending_time_iso="20260730-10:00:00.000",
        )
        recv_type_before = engine.last_recv_type
        recv_time_before = engine.last_recv_time
        report, logout = engine.process_inbound_msg(foreign)

        self.assertEqual(report.status, STATUS_SESSION_TERMINATED)
        self.assertEqual(logout.msg_type, MSG_LOGOUT)
        self.assertEqual(engine.expected_in_seq_num, 2)  # unchanged
        # The foreign message must not count as session activity -- otherwise it
        # would keep a dead session's liveness timers alive.
        self.assertEqual(engine.last_recv_type, recv_type_before)
        self.assertEqual(engine.last_recv_time, recv_time_before)

    def test_comp_id_validation_can_be_disabled(self):
        config = FixSessionConfig(
            session_id="S", sender_comp_id=FIRM, target_comp_id=VENUE,
            validate_comp_ids=False,
        )
        engine = FixProtocolSessionManagerEngine(config, clock=lambda: self.now)
        engine.initiate_logon()
        report, _ = engine.process_inbound_msg(
            FixMessage(MSG_LOGON, 1, "ANY", "ANY", "20260730-10:00:00.000")
        )
        self.assertEqual(report.state, STATE_LOGGED_IN)


class TestLivenessAndHeartbeats(FixSessionTestBase):

    def test_heartbeat_emitted_after_idle_interval(self):
        engine = self.establish()
        self.assertEqual(engine.check_liveness(), [])

        self.now += 31
        due = engine.check_liveness()
        self.assertEqual([m.msg_type for m in due], [MSG_HEARTBEAT])

    def test_test_request_issued_once_after_inbound_silence(self):
        engine = self.establish()
        self.now += 46  # >= 1.5 x 30s

        due = engine.check_liveness()
        test_requests = [m for m in due if m.msg_type == MSG_TEST_REQUEST]
        self.assertEqual(len(test_requests), 1)
        self.assertTrue(test_requests[0].body_fields[112])

        # A second poll must not spam another TestRequest.
        self.assertEqual(
            [m for m in engine.check_liveness() if m.msg_type == MSG_TEST_REQUEST], []
        )

    def test_timeout_declared_only_past_disconnect_multiplier(self):
        engine = self.establish()
        self.now += 46
        engine.check_liveness()
        self.assertFalse(engine.is_timed_out())

        self.now = 1_000_000.0 + 73  # >= 2.4 x 30s of inbound silence
        self.assertTrue(engine.is_timed_out())

    def test_heartbeat_echoing_test_req_id_clears_the_probe(self):
        engine = self.establish()
        self.now += 46
        probe = [m for m in engine.check_liveness() if m.msg_type == MSG_TEST_REQUEST][0]
        test_req_id = probe.body_fields[112]

        engine.process_inbound_msg(inbound(MSG_HEARTBEAT, 2, {112: test_req_id}))
        self.assertIsNone(engine.outstanding_test_req_id)

    def test_inbound_test_request_is_answered_with_matching_heartbeat(self):
        """Regression: an unanswered TestRequest gets the session disconnected."""
        engine = self.establish()
        report, response = engine.process_inbound_msg(
            inbound(MSG_TEST_REQUEST, 2, {112: "VENUE-PROBE-1"})
        )

        self.assertIsNotNone(response)
        self.assertEqual(response.msg_type, MSG_HEARTBEAT)
        self.assertEqual(response.body_fields[112], "VENUE-PROBE-1")
        self.assertEqual(report.expected_in_seq_num, 3)

    def test_zero_heartbeat_interval_disables_liveness_traffic(self):
        config = FixSessionConfig(
            session_id="S", sender_comp_id=FIRM, target_comp_id=VENUE,
            heartbeat_interval_sec=0,
        )
        engine = FixProtocolSessionManagerEngine(config, clock=lambda: self.now)
        engine.initiate_logon()
        engine.process_inbound_msg(inbound(MSG_LOGON, 1))

        self.now += 100_000
        self.assertEqual(engine.check_liveness(), [])
        self.assertFalse(engine.is_timed_out())

    def test_no_liveness_traffic_before_the_session_is_established(self):
        self.now += 10_000
        self.assertEqual(self.engine.check_liveness(), [])
        self.assertFalse(self.engine.is_timed_out())


class TestResendServing(FixSessionTestBase):

    def test_application_messages_replay_under_original_sequence_numbers(self):
        engine = self.establish()
        for _ in range(3):
            engine.create_outbound_msg("D", {11: "ORD-1"})
        out_seq_before = engine.out_seq_num

        report, _ = engine.process_inbound_msg(
            inbound(MSG_RESEND_REQUEST, 2, {7: "2", 16: "0"})
        )

        replays = [m for m in report.responses if m.msg_type == "D"]
        self.assertEqual([m.msg_seq_num for m in replays], [2, 3, 4])
        for replay in replays:
            self.assertTrue(replay.poss_dup_flag)      # Tag 43
            self.assertIn(122, replay.body_fields)      # OrigSendingTime
        # Retransmission must not burn new outbound sequence numbers.
        self.assertEqual(engine.out_seq_num, out_seq_before)

    def test_admin_messages_collapse_into_a_gap_fill(self):
        engine = self.establish()  # seq 1 outbound is the Logon (administrative)
        engine.create_outbound_msg("D", {11: "ORD-1"})

        report, _ = engine.process_inbound_msg(
            inbound(MSG_RESEND_REQUEST, 2, {7: "1", 16: "0"})
        )

        gap_fills = [m for m in report.responses if m.msg_type == MSG_SEQUENCE_RESET]
        self.assertEqual(len(gap_fills), 1)
        self.assertEqual(gap_fills[0].msg_seq_num, 1)
        self.assertEqual(gap_fills[0].body_fields[123], "Y")
        self.assertEqual(gap_fills[0].body_fields[36], "2")
        self.assertTrue(gap_fills[0].poss_dup_flag)

    def test_end_seq_no_zero_means_infinity(self):
        engine = self.establish()
        for _ in range(3):
            engine.create_outbound_msg("D")

        open_ended = engine.build_resend_response(2, 0)
        closed = engine.build_resend_response(2, 4)
        self.assertEqual(
            [m.msg_seq_num for m in open_ended], [m.msg_seq_num for m in closed]
        )

    def test_resend_request_for_an_empty_range_returns_nothing(self):
        engine = self.establish()
        self.assertEqual(engine.build_resend_response(99, 120), [])
        self.assertEqual(engine.build_resend_response(0, 0), [])

    def test_malformed_resend_request_is_rejected(self):
        engine = self.establish()
        report, reject = engine.process_inbound_msg(
            inbound(MSG_RESEND_REQUEST, 2, {7: "abc", 16: "0"})
        )
        self.assertEqual(report.status, STATUS_MESSAGE_REJECTED)
        self.assertEqual(reject.msg_type, MSG_REJECT)

    def test_resend_buffer_is_bounded(self):
        config = FixSessionConfig(
            session_id="S", sender_comp_id=FIRM, target_comp_id=VENUE,
            resend_buffer_size=5,
        )
        engine = FixProtocolSessionManagerEngine(config, clock=lambda: self.now)
        for _ in range(50):
            engine.create_outbound_msg("D")

        self.assertEqual(len(engine.sent_messages), 5)
        self.assertEqual(sorted(engine.sent_messages), [46, 47, 48, 49, 50])

    def test_evicted_range_is_gap_filled_not_silently_dropped(self):
        config = FixSessionConfig(
            session_id="S", sender_comp_id=FIRM, target_comp_id=VENUE,
            resend_buffer_size=2,
        )
        engine = FixProtocolSessionManagerEngine(config, clock=lambda: self.now)
        for _ in range(6):
            engine.create_outbound_msg("D")

        responses = engine.build_resend_response(1, 4)
        self.assertEqual([m.msg_type for m in responses], [MSG_SEQUENCE_RESET])
        self.assertEqual(responses[0].msg_seq_num, 1)
        self.assertEqual(responses[0].body_fields[36], "5")


class TestGracefulLogout(FixSessionTestBase):

    def test_initiated_logout_completes_on_venue_response(self):
        engine = self.establish()
        logout = engine.initiate_logout("End of day")
        self.assertEqual(logout.msg_type, MSG_LOGOUT)
        self.assertEqual(engine.state, STATE_LOGOUT_SENT)

        report, echo = engine.process_inbound_msg(inbound(MSG_LOGOUT, 2))
        self.assertIsNone(echo)  # we initiated; no second Logout
        self.assertEqual(report.status, STATUS_SESSION_TERMINATED)
        self.assertEqual(engine.state, STATE_DISCONNECTED)

    def test_venue_initiated_logout_is_acknowledged(self):
        engine = self.establish()
        report, echo = engine.process_inbound_msg(inbound(MSG_LOGOUT, 2, {58: "Venue EOD"}))

        self.assertIsNotNone(echo)
        self.assertEqual(echo.msg_type, MSG_LOGOUT)
        self.assertEqual(engine.state, STATE_DISCONNECTED)
        self.assertEqual(report.status, STATUS_SESSION_TERMINATED)


class TestWireFormatting(FixSessionTestBase):

    def test_sending_time_is_a_valid_fix_utc_timestamp(self):
        """Tag 52 is YYYYMMDD-HH:MM:SS.sss -- not ISO-8601."""
        engine = self.establish()
        stamp = engine.create_outbound_msg(MSG_HEARTBEAT).sending_time_iso

        self.assertRegex(stamp, r"^\d{8}-\d{2}:\d{2}:\d{2}\.\d{3}$")
        self.assertNotIn("T", stamp)
        self.assertNotIn("Z", stamp)

    def test_fix_utc_timestamp_known_epoch(self):
        # 1735689600 == 2025-01-01T00:00:00Z
        self.assertEqual(fix_utc_timestamp(1_735_689_600.0), "20250101-00:00:00.000")
        self.assertEqual(fix_utc_timestamp(1_735_689_600.25), "20250101-00:00:00.250")

    def test_fix_utc_timestamp_rounding_carries_into_next_second(self):
        self.assertEqual(fix_utc_timestamp(1_735_689_600.9999), "20250101-00:00:01.000")

    def test_outbound_body_is_copied_not_aliased(self):
        engine = self.establish()
        body = {11: "ORD-1"}
        msg = engine.create_outbound_msg("D", body)
        body[11] = "MUTATED"
        self.assertEqual(msg.body_fields[11], "ORD-1")


class TestConfigValidation(FixSessionTestBase):

    def test_rejects_negative_heartbeat_interval(self):
        with self.assertRaises(ValueError):
            FixSessionConfig("S", FIRM, VENUE, heartbeat_interval_sec=-1)

    def test_rejects_test_request_multiplier_at_or_below_one(self):
        with self.assertRaises(ValueError):
            FixSessionConfig("S", FIRM, VENUE, test_request_multiplier=1.0)

    def test_rejects_disconnect_multiplier_below_test_request_multiplier(self):
        with self.assertRaises(ValueError):
            FixSessionConfig(
                "S", FIRM, VENUE,
                test_request_multiplier=2.0, disconnect_multiplier=1.5,
            )

    def test_rejects_empty_resend_buffer(self):
        with self.assertRaises(ValueError):
            FixSessionConfig("S", FIRM, VENUE, resend_buffer_size=0)


class TestAuditReport(FixSessionTestBase):

    def test_tuple_response_matches_first_of_responses_list(self):
        engine = self.establish()
        report, response = engine.process_inbound_msg(inbound("D", 5))
        self.assertIs(response, report.responses[0])

    def test_report_state_always_mirrors_engine_state(self):
        engine = self.establish()
        cases = [
            inbound("D", 5),
            inbound(MSG_SEQUENCE_RESET, 2, {36: "6", 123: "Y"}),
            inbound(MSG_TEST_REQUEST, 6, {112: "X"}),
            inbound(MSG_LOGOUT, 7),
        ]
        for msg in cases:
            with self.subTest(msg_type=msg.msg_type):
                report, _ = engine.process_inbound_msg(msg)
                self.assertEqual(report.state, engine.state)
                self.assertIsInstance(report, FixSessionAuditReport)

    def test_audit_notes_describe_the_actual_wire_content(self):
        """The log used to claim a closed range while sending EndSeqNo=0."""
        engine = self.establish()
        report, resend = engine.process_inbound_msg(inbound("D", 5))

        self.assertEqual(resend.body_fields[16], "0")
        self.assertIn("EndSeqNo=0", report.audit_notes)
        self.assertNotIn("to 4", report.audit_notes)


if __name__ == "__main__":
    unittest.main()
