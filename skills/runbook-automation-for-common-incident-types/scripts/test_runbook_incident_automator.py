import unittest
from runbook_incident_automator import (
    RunbookIncidentAutomator, Config,
    RunbookIncidentAutomationEngine, IncidentAlert, IncidentType,
    RemediationAction, IncidentStatus, IncidentRunbookReport
)

class TestRunbookIncidentAutomatorLegacy(unittest.TestCase):
    def test_init(self):
        obj = RunbookIncidentAutomator(Config("test"))
        self.assertEqual(obj.config.name, "test")

    def test_process(self):
        obj = RunbookIncidentAutomator()
        self.assertTrue(obj.process())

class TestRunbookIncidentAutomationEngine(unittest.TestCase):

    def setUp(self):
        self.engine = RunbookIncidentAutomationEngine(is_dry_run=False)

    def test_drawdown_breach_runbook_execution(self):
        alert = IncidentAlert(
            incident_id="INC_9001",
            incident_type=IncidentType.DRAWDOWN_BREACH,
            severity="CRITICAL",
            source_service="RISK_ENGINE",
            metric_value=-150000.0,
            threshold_value=-100000.0,
            timestamp_iso="2026-08-05T10:00:00Z"
        )
        report = self.engine.execute_runbook(alert)
        self.assertEqual(report.status, IncidentStatus.RESOLVED)
        self.assertEqual(len(report.steps_executed), 2)
        self.assertEqual(report.steps_executed[0].action, RemediationAction.CANCEL_OPEN_ORDERS)
        self.assertEqual(report.steps_executed[1].action, RemediationAction.TRIGGER_KILL_SWITCH)

    def test_feed_disconnect_runbook_execution(self):
        alert = IncidentAlert(
            incident_id="INC_9002",
            incident_type=IncidentType.FEED_DISCONNECT,
            severity="HIGH",
            source_service="MARKET_DATA_FEED",
            metric_value=5.0,  # 5 seconds without heartbeat
            threshold_value=1.0,
            timestamp_iso="2026-08-05T10:05:00Z"
        )
        report = self.engine.execute_runbook(alert)
        self.assertEqual(report.status, IncidentStatus.RESOLVED)
        self.assertEqual(report.steps_executed[0].action, RemediationAction.RECONNECT_SOCKET)
        self.assertEqual(report.steps_executed[1].action, RemediationAction.FAILOVER_VENUE)

    def test_dry_run_mode(self):
        dry_engine = RunbookIncidentAutomationEngine(is_dry_run=True)
        alert = IncidentAlert(
            incident_id="INC_9003",
            incident_type=IncidentType.BROKER_API_OUTAGE,
            severity="CRITICAL",
            source_service="BROKER_GATEWAY",
            metric_value=500.0,
            threshold_value=200.0,
            timestamp_iso="2026-08-05T10:10:00Z"
        )
        report = dry_engine.execute_runbook(alert)
        self.assertEqual(report.status, IncidentStatus.RESOLVED)
        self.assertEqual(report.steps_executed[0].status, "SKIPPED_DRY_RUN")

    def test_audit_history_recorded(self):
        alert = IncidentAlert("INC_1", IncidentType.ORDER_THROTTLE, "MEDIUM", "SOR", 50.0, 20.0, "2026-08-05T10:00:00Z")
        self.engine.execute_runbook(alert)
        history = self.engine.get_audit_history()
        self.assertEqual(len(history), 1)
        self.assertEqual(history[0].incident_id, "INC_1")

if __name__ == '__main__':
    unittest.main()
