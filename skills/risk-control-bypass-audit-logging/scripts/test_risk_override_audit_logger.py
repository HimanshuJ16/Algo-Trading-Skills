import unittest
from risk_override_audit_logger import (
    RiskControlBypassAuditEngine, RiskBypassEvent
)

class TestRiskControlBypassAuditEngine(unittest.TestCase):

    def setUp(self):
        self.engine = RiskControlBypassAuditEngine()

    def test_authorized_critical_bypass_logged(self):
        event = RiskBypassEvent(
            event_id="BYP_001",
            timestamp_iso="2024-06-15T10:30:00Z",
            bypassed_control="MAX_POSITION_SIZE",
            original_limit_value="1000",
            override_value="2000",
            authorized_by="risk_officer",
            justification="Temporary increase for block trade execution per CRO approval.",
            strategy_id="STRAT_ALPHA",
            instrument="AAPL"
        )
        entry = self.engine.log_bypass(event)
        self.assertEqual(entry.severity, "CRITICAL")
        self.assertFalse(entry.is_suspicious)  # authorized principal with justification
        self.assertIsNone(entry.flag_reason)

    def test_unauthorized_bypass_flagged_suspicious(self):
        event = RiskBypassEvent(
            event_id="BYP_002",
            timestamp_iso="2024-06-15T11:00:00Z",
            bypassed_control="DAILY_LOSS_LIMIT",
            original_limit_value="50000",
            override_value="100000",
            authorized_by="junior_trader",
            justification="Need more room",
            strategy_id="STRAT_BETA"
        )
        entry = self.engine.log_bypass(event)
        self.assertEqual(entry.severity, "CRITICAL")
        self.assertTrue(entry.is_suspicious)
        self.assertIn("Unauthorized principal", entry.flag_reason)

    def test_audit_report_detects_suspicious(self):
        # Log one authorized and one unauthorized bypass
        self.engine.log_bypass(RiskBypassEvent(
            event_id="BYP_A", timestamp_iso="2024-06-15T09:00:00Z",
            bypassed_control="SPREAD_VETO", original_limit_value="0.5",
            override_value="1.0", authorized_by="risk_officer",
            justification="Approved for illiquid instrument exception."
        ))
        self.engine.log_bypass(RiskBypassEvent(
            event_id="BYP_B", timestamp_iso="2024-06-15T09:05:00Z",
            bypassed_control="KILL_SWITCH", original_limit_value="ON",
            override_value="OFF", authorized_by="unknown_user",
            justification=""
        ))
        report = self.engine.generate_audit_report()
        self.assertEqual(report.total_bypass_events, 2)
        self.assertEqual(report.status, "SUSPICIOUS_BYPASSES_DETECTED")
        self.assertGreater(report.suspicious_count, 0)
        self.assertGreater(report.critical_count, 0)

if __name__ == '__main__':
    unittest.main()
