import unittest
from override_access_control import (
    EmergencyOverrideAccessEngine, OverrideRequest, OverrideControlReport
)

class TestEmergencyOverrideAccessEngine(unittest.TestCase):

    def setUp(self):
        self.engine = EmergencyOverrideAccessEngine()

    def test_critical_kill_switch_dual_signoff_approved(self):
        # Dual Sign-off: Risk Officer + Head Trader -> APPROVED!
        req = OverrideRequest(
            request_id="REQ_OVERRIDE_01",
            target_system_id="ALL_ALGOS",
            action_type="KILL_SWITCH_ALL_ALGOS",
            primary_operator_id="USR_RISK_01",
            primary_operator_role="RISK_OFFICER",
            justification_reason="Flash crash in tech sector; halting all execution engines.",
            secondary_operator_id="USR_TRADER_01",
            secondary_operator_role="HEAD_TRADER"
        )
        report = self.engine.process_override_request(req)
        
        self.assertTrue(report.is_approved)
        self.assertEqual(report.severity_level, "SEVERITY_CRITICAL")
        self.assertEqual(len(report.audit_hash_sha256), 64) # SHA-256 length

    def test_critical_kill_switch_without_dual_signoff_rejected(self):
        # Single operator without secondary sign-off or Break-Glass token -> REJECTED!
        req = OverrideRequest(
            request_id="REQ_OVERRIDE_02",
            target_system_id="ALL_ALGOS",
            action_type="KILL_SWITCH_ALL_ALGOS",
            primary_operator_id="USR_DEV_01",
            primary_operator_role="RISK_OFFICER",
            justification_reason="Attempting unauthorized firm-wide kill switch."
        )
        report = self.engine.process_override_request(req)
        
        self.assertFalse(report.is_approved)
        self.assertIn("DUAL SIGN-OFF REQUIRED", report.rejection_reason)

if __name__ == '__main__':
    unittest.main()
