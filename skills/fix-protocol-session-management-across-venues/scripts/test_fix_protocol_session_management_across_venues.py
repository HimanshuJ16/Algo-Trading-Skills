import unittest
from fix_protocol_session_management_across_venues import (
    FixProtocolSessionManagerEngine, FixSessionConfig, FixMessage, FixSessionAuditReport
)

class TestFixProtocolSessionManagerEngine(unittest.TestCase):

    def setUp(self):
        config = FixSessionConfig(session_id="FIX_NASDAQ_01", sender_comp_id="FIRM_ALPHA", target_comp_id="NASDAQ")
        self.engine = FixProtocolSessionManagerEngine(config)

    def test_logon_handshake_transitions_to_logged_in(self):
        # Initiate Logon
        logon_out = self.engine.initiate_logon()
        self.assertEqual(self.engine.state, "LOGON_SENT")
        self.assertEqual(logon_out.msg_seq_num, 1)

        # Receive Logon response from venue (Tag 35=A, SeqNum=1)
        in_logon = FixMessage("A", msg_seq_num=1, sender_comp_id="NASDAQ", target_comp_id="FIRM_ALPHA", sending_time_iso="20260730T10:00:00Z")
        report, _ = self.engine.process_inbound_msg(in_logon)

        self.assertEqual(report.state, "LOGGED_IN")
        self.assertEqual(report.expected_in_seq_num, 2)
        self.assertFalse(report.gap_detected)

    def test_sequence_gap_triggers_resend_request(self):
        # Establish session
        self.engine.initiate_logon()
        self.engine.process_inbound_msg(FixMessage("A", 1, "NASDAQ", "FIRM_ALPHA", "20260730T10:00:00Z"))

        # Receive message with MsgSeqNum=5 when expected=2 -> GAP DETECTED!
        gap_msg = FixMessage("D", msg_seq_num=5, sender_comp_id="NASDAQ", target_comp_id="FIRM_ALPHA", sending_time_iso="20260730T10:00:05Z")
        report, resend_out = self.engine.process_inbound_msg(gap_msg)

        self.assertTrue(report.gap_detected)
        self.assertEqual(report.state, "RESEND_REQUEST_SENT")
        self.assertIsNotNone(resend_out)
        self.assertEqual(resend_out.msg_type, "2") # Tag 35=2 ResendRequest
        self.assertEqual(resend_out.body_fields[7], "2") # BeginSeqNo=2

    def test_gap_fill_resynchronizes_session(self):
        # Trigger gap
        self.engine.initiate_logon()
        self.engine.process_inbound_msg(FixMessage("A", 1, "NASDAQ", "FIRM_ALPHA", "20260730T10:00:00Z"))
        self.engine.process_inbound_msg(FixMessage("D", 5, "NASDAQ", "FIRM_ALPHA", "20260730T10:00:05Z"))

        # Venue sends SequenceReset / GapFill (Tag 35=4, NewSeqNo=6)
        gap_fill = FixMessage("4", msg_seq_num=2, sender_comp_id="NASDAQ", target_comp_id="FIRM_ALPHA", sending_time_iso="20260730T10:00:06Z", body_fields={36: "6", 123: "Y"})
        report, _ = self.engine.process_inbound_msg(gap_fill)

        self.assertEqual(report.state, "LOGGED_IN")
        self.assertEqual(report.expected_in_seq_num, 6)
        self.assertFalse(report.gap_detected)

if __name__ == '__main__':
    unittest.main()
