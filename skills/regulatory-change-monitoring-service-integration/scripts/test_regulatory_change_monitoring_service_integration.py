import unittest
from regulatory_change_monitoring_service_integration import (
    RegulatoryChangeMonitoringServiceIntegrationEngine, ComplianceResult,
    RegulatoryUpdate, RegulatoryChangeReport
)

class TestRegulatoryChangeMonitoringServiceIntegration(unittest.TestCase):

    def setUp(self):
        self.engine = RegulatoryChangeMonitoringServiceIntegrationEngine(
            monitored_regulators=["SEC", "FCA", "SEBI"]
        )

    def test_legacy_valid(self):
        res = self.engine.check({"valid": True})
        self.assertTrue(res.is_compliant)

    def test_legacy_invalid(self):
        res = self.engine.check({"valid": False})
        self.assertFalse(res.is_compliant)

    def test_legacy_edge(self):
        res = self.engine.check({})
        self.assertFalse(res.is_compliant)

    def test_process_critical_action_required_update(self):
        updates = [
            RegulatoryUpdate(
                update_id="REG_SEC_2024_01",
                regulator="SEC",
                title="T+1 Settlement Rule Transition",
                effective_date="2024-05-28",
                impacted_subdomains=["SETTLEMENT", "CLEARING"],
                severity="CRITICAL",
                action_required=True,
                summary="Transition from T+2 to T+1 settlement cycle for US equities."
            )
        ]
        report = self.engine.process_updates(updates, current_date_iso="2024-05-01")
        self.assertEqual(report.overall_status, "ACTION_REQUIRED")
        self.assertEqual(report.critical_count, 1)
        self.assertEqual(report.action_required_count, 1)
        self.assertTrue(report.assessments[0].requires_immediate_action)
        self.assertEqual(report.assessments[0].days_until_effective, 27)

    def test_unmonitored_regulator_filtered(self):
        updates = [
            RegulatoryUpdate(
                update_id="REG_UNMON_01",
                regulator="UNMONITORED_REG",
                title="Irrelevant Notice",
                effective_date="2024-12-31",
                impacted_subdomains=["MISC"],
                severity="LOW",
                action_required=False
            )
        ]
        report = self.engine.process_updates(updates)
        self.assertEqual(report.overall_status, "NO_UPDATES")
        self.assertEqual(report.total_updates, 0)

if __name__ == '__main__':
    unittest.main()
