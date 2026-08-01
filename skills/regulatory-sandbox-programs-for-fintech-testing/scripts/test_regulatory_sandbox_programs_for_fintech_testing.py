import unittest
from regulatory_sandbox_programs_for_fintech_testing import (
    RegulatorySandboxProgramsForFintechTestingEngine, ComplianceResult,
    SandboxTelemetry, SandboxAuditReport
)

class TestRegulatorySandboxProgramsForFintechTesting(unittest.TestCase):

    def setUp(self):
        self.engine = RegulatorySandboxProgramsForFintechTestingEngine()

    def test_legacy_valid(self):
        res = self.engine.check({"valid": True})
        self.assertTrue(res.is_compliant)

    def test_legacy_invalid(self):
        res = self.engine.check({"valid": False})
        self.assertFalse(res.is_compliant)

    def test_legacy_edge(self):
        res = self.engine.check({})
        self.assertFalse(res.is_compliant)

    def test_fca_sandbox_compliant(self):
        telemetry = SandboxTelemetry(
            program_key="FCA_UK",
            active_clients=300,
            cumulative_volume_usd=2000000.0,
            current_aum_usd=4000000.0,
            elapsed_months=6,
            has_exit_plan=True
        )
        report = self.engine.audit_sandbox_telemetry(telemetry)
        self.assertEqual(report.status, "SANDBOX_COMPLIANT")
        self.assertTrue(report.is_within_limits)
        self.assertEqual(len(report.breaches), 0)
        self.assertEqual(report.client_capacity_pct, 60.0)
        self.assertEqual(report.time_remaining_months, 6)

    def test_client_limit_and_volume_breach(self):
        telemetry = SandboxTelemetry(
            program_key="FCA_UK",
            active_clients=600,
            cumulative_volume_usd=6000000.0,
            current_aum_usd=4000000.0,
            elapsed_months=4,
            has_exit_plan=True
        )
        report = self.engine.audit_sandbox_telemetry(telemetry)
        self.assertEqual(report.status, "SANDBOX_BREACHED")
        self.assertFalse(report.is_within_limits)
        self.assertEqual(len(report.breaches), 2)
        self.assertTrue(any(b.breach_type == "CLIENT_LIMIT_BREACH" for b in report.breaches))
        self.assertTrue(any(b.breach_type == "VOLUME_CAP_BREACH" for b in report.breaches))

    def test_sandbox_expired_breach(self):
        telemetry = SandboxTelemetry(
            program_key="SEBI_IN",
            active_clients=100,
            cumulative_volume_usd=1000000.0,
            current_aum_usd=1000000.0,
            elapsed_months=8,  # max 6
            has_exit_plan=True
        )
        report = self.engine.audit_sandbox_telemetry(telemetry)
        self.assertEqual(report.status, "SANDBOX_BREACHED")
        self.assertTrue(any(b.breach_type == "SANDBOX_EXPIRED" for b in report.breaches))

if __name__ == '__main__':
    unittest.main()
