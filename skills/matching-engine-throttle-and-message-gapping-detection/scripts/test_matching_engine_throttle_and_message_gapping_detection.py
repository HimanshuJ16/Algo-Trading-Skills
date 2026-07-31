import time
import unittest
from matching_engine_throttle_and_message_gapping_detection import (
    MatchingEngineMonitorEngine, OutboundMessageRecord, InboundMessageRecord, MatchingEngineAuditReport
)

class TestMatchingEngineMonitorEngine(unittest.TestCase):

    def setUp(self):
        self.engine = MatchingEngineMonitorEngine(max_allowed_mps=500.0, warning_threshold_pct=80.0)

    def test_outbound_rate_limit_throttle_warning(self):
        # 420 msgs in active 1s window (84% of 500 max) -> THROTTLE_WARNING_SLOW_DOWN!
        now = time.time()
        outbound = [OutboundMessageRecord("CME_SESSION_01", "NEW_ORDER", now - 0.1, i) for i in range(420)]
        inbound = [InboundMessageRecord("CME_SESSION_01", "EXEC_REPORT", 1, now - 0.1)]

        report = self.engine.audit_matching_engine_session("CME_SESSION_01", outbound, inbound)

        self.assertEqual(report.status, "THROTTLE_WARNING_SLOW_DOWN")
        self.assertEqual(report.outbound_rate_per_sec, 420.0)
        self.assertFalse(report.is_throttled)

    def test_exchange_rate_limit_hard_block(self):
        # 550 msgs in active 1s window (> 500 max) -> EXCHANGE_RATE_LIMIT_THROTTLED!
        now = time.time()
        outbound = [OutboundMessageRecord("CME_SESSION_01", "NEW_ORDER", now - 0.1, i) for i in range(550)]
        inbound = []

        report = self.engine.audit_matching_engine_session("CME_SESSION_01", outbound, inbound)

        self.assertEqual(report.status, "EXCHANGE_RATE_LIMIT_THROTTLED")
        self.assertTrue(report.is_throttled)

    def test_inbound_sequence_gap_detection(self):
        # Expecting Seq 1, got Seq 105 -> MESSAGE_SEQUENCE_GAP_DETECTED for missing [1, 104]!
        now = time.time()
        outbound = []
        inbound = [InboundMessageRecord("CME_SESSION_02", "EXEC_REPORT", 105, now)]

        report = self.engine.audit_matching_engine_session("CME_SESSION_02", outbound, inbound)

        self.assertEqual(report.status, "MESSAGE_SEQUENCE_GAP_DETECTED")
        self.assertTrue(report.has_sequence_gap)
        self.assertIsNotNone(report.sequence_gap_details)
        self.assertEqual(report.sequence_gap_details.missing_seq_start, 1)
        self.assertEqual(report.sequence_gap_details.missing_seq_end, 104)

if __name__ == '__main__':
    unittest.main()
