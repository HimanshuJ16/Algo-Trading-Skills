import unittest
from risk_escalation_matrix import (
    RiskEscalationMatrix, ResponseAction, SeverityLevel, NotificationChannel,
    BreachEvent, EscalationDecision, InvalidBreachError
)

class TestRiskEscalationMatrixLegacy(unittest.TestCase):

    def test_warn(self):
        matrix = RiskEscalationMatrix()
        res = matrix.evaluate(110, 100)
        self.assertEqual(res.action, ResponseAction.WARN)
        self.assertEqual(res.level, 1.0)

    def test_reduce(self):
        matrix = RiskEscalationMatrix()
        res = matrix.evaluate(125, 100)
        self.assertEqual(res.action, ResponseAction.REDUCE)

    def test_halt(self):
        matrix = RiskEscalationMatrix()
        res = matrix.evaluate(160, 100)
        self.assertEqual(res.action, ResponseAction.HALT)

    def test_flatten(self):
        matrix = RiskEscalationMatrix()
        res = matrix.evaluate(210, 100)
        self.assertEqual(res.action, ResponseAction.FLATTEN)
        self.assertEqual(res.level, 2.0)

    def test_no_breach(self):
        matrix = RiskEscalationMatrix()
        res = matrix.evaluate(90, 100)
        self.assertEqual(res.action, ResponseAction.NONE)

class TestRiskEscalationMatrixAdvanced(unittest.TestCase):

    def setUp(self):
        self.matrix = RiskEscalationMatrix()

    def test_process_breach_event_critical(self):
        event = BreachEvent(
            event_id="EVT_1001",
            metric_name="DAILY_DRAWDOWN",
            strategy_id="STAT_ARB_01",
            current_value=25000.0,
            limit_value=10000.0,  # 2.5x limit
            timestamp_iso="2026-08-05T10:00:00Z"
        )
        decision = self.matrix.process_breach_event(event)
        self.assertEqual(decision.severity, SeverityLevel.CRITICAL)
        self.assertEqual(decision.action, ResponseAction.FLATTEN)
        self.assertIn(NotificationChannel.PAGERDUTY, decision.notification_channels)
        self.assertEqual(decision.ack_deadline_seconds, 60)

    def test_sustained_breach_auto_escalation(self):
        event = BreachEvent(
            event_id="EVT_1002",
            metric_name="POSITION_CAP",
            strategy_id="MOMENTUM_02",
            current_value=105.0,
            limit_value=100.0,  # 1.05x limit -> normally WARN
            timestamp_iso="2026-08-05T10:05:00Z",
            duration_seconds=360.0  # > 300s sustained
        )
        decision = self.matrix.process_breach_event(event)
        self.assertTrue(decision.is_sustained_breach)
        self.assertEqual(decision.action, ResponseAction.REDUCE)  # Escalated from WARN to REDUCE
        self.assertEqual(decision.severity, SeverityLevel.AMBER)

    def test_invalid_limit_raises_error(self):
        event = BreachEvent(
            event_id="EVT_1003",
            metric_name="LEVERAGE",
            strategy_id="HFT_01",
            current_value=5.0,
            limit_value=0.0,
            timestamp_iso="2026-08-05T10:10:00Z"
        )
        with self.assertRaises(InvalidBreachError):
            self.matrix.process_breach_event(event)

    def test_audit_trail_recorded(self):
        event = BreachEvent("E1", "METRIC1", "S1", 130.0, 100.0, "2026-08-05T10:00:00Z")
        self.matrix.process_breach_event(event)
        trail = self.matrix.get_audit_trail()
        self.assertEqual(len(trail), 1)
        self.assertEqual(trail[0].event_id, "E1")

if __name__ == '__main__':
    unittest.main()
