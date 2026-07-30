import unittest
from deployment_freeze_guard import (
    DeploymentFreezeGuardEngine, MacroEventFreezeWindow, DeploymentRequest, DeploymentFreezeAuditReport
)

class TestDeploymentFreezeGuardEngine(unittest.TestCase):

    def setUp(self):
        self.guard = DeploymentFreezeGuardEngine()
        
        # FOMC Rate Decision at t = 10,000s, Pre-buffer = 60m (3600s), Post-buffer = 60m (3600s)
        # Freeze active from t = 6,400s to 13,600s
        self.guard.register_freeze_event(MacroEventFreezeWindow(
            event_id="FOMC_2026_Q2",
            event_name="FOMC Rate Decision Announcement",
            event_start_epoch_sec=10000.0,
            pre_event_buffer_minutes=60.0,
            post_event_buffer_minutes=60.0
        ))

    def test_routine_production_deployment_blocked_during_freeze(self):
        # Deploy at t = 8,000s (inside 60m pre-FOMC window!) -> BLOCKED
        req = DeploymentRequest(
            deployment_id="DEP_101", service_name="execution-router",
            target_environment="PRODUCTION", requested_epoch_sec=8000.0
        )
        report = self.guard.evaluate_deployment_request(req)
        
        self.assertFalse(report.is_approved)
        self.assertEqual(report.status, "DEPLOYMENT_BLOCKED_FREEZE_ACTIVE")
        self.assertEqual(report.active_freeze_event_name, "FOMC Rate Decision Announcement")

    def test_break_glass_emergency_hotfix_approved_with_dual_signoff(self):
        # Emergency hotfix at t = 8,000s with Risk Officer + Head of Trading approvals -> APPROVED!
        req = DeploymentRequest(
            deployment_id="DEP_102", service_name="execution-router",
            target_environment="PRODUCTION", requested_epoch_sec=8000.0,
            is_emergency_hotfix=True, risk_officer_approval=True, head_of_trading_approval=True
        )
        report = self.guard.evaluate_deployment_request(req)
        
        self.assertTrue(report.is_approved)
        self.assertEqual(report.status, "BREAK_GLASS_HOTFIX_APPROVED")

if __name__ == '__main__':
    unittest.main()
