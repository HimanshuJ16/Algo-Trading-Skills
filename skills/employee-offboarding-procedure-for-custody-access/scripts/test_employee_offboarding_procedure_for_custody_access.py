import time
import unittest
from employee_offboarding_procedure_for_custody_access import (
    CustodyOffboardingEngine, EmployeeOffboardingRecord, CustodyOffboardingAuditReport
)

class TestCustodyOffboardingEngine(unittest.TestCase):

    def setUp(self):
        self.engine = CustodyOffboardingEngine(key_rotation_sla_hours=24.0)

    def test_full_offboarding_compliance(self):
        now = time.time()
        record = EmployeeOffboardingRecord(
            employee_id="EMP_KEY_01",
            employee_name="John Doe",
            role="KEY_CUSTODIAN",
            termination_time_epoch=now - 3600.0,
            held_custody_keys=True,
            completed_steps=[
                "IDP_SSO_REVOKED",
                "EXCHANGE_API_KEYS_REVOKED",
                "CUSTODY_PORTAL_REVOKED",
                "MULTISIG_MPC_KEY_ROTATED",
                "HARDWARE_TOKEN_WIPED"
            ]
        )
        report = self.engine.evaluate_offboarding_status(record, current_time_epoch=now)
        
        self.assertTrue(report.is_fully_compliant)
        self.assertEqual(report.completion_percentage, 100.0)
        self.assertEqual(report.key_exposure_risk, "LOW_RISK")
        self.assertEqual(len(report.pending_steps), 0)

    def test_unresolved_key_exposure_risk_alert(self):
        now = time.time()
        # Terminated 30 hours ago (> 24h SLA), MPC key NOT rotated -> CRITICAL RISK!
        record = EmployeeOffboardingRecord(
            employee_id="EMP_KEY_02",
            employee_name="Jane Smith",
            role="DEVOPS",
            termination_time_epoch=now - (30.0 * 3600.0),
            held_custody_keys=True,
            completed_steps=["IDP_SSO_REVOKED", "EXCHANGE_API_KEYS_REVOKED"]
        )
        report = self.engine.evaluate_offboarding_status(record, current_time_epoch=now)
        
        self.assertFalse(report.is_fully_compliant)
        self.assertEqual(report.completion_percentage, 40.0)
        self.assertEqual(report.key_exposure_risk, "CRITICAL_KEY_EXPOSURE_RISK")
        self.assertIn("MULTISIG_MPC_KEY_ROTATED", report.pending_steps)

if __name__ == '__main__':
    unittest.main()
