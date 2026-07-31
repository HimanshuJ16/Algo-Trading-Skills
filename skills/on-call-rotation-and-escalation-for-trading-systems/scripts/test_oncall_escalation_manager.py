import unittest
from oncall_escalation_manager import (
    OnCallEscalationManagerEngine, OnCallEngineer, SystemIncident, EscalationPolicyConfig, OnCallEscalationReport
)

class TestOnCallEscalationManagerEngine(unittest.TestCase):

    def setUp(self):
        roster = [
            OnCallEngineer("E1", "Alice Primary", "PRIMARY", "+1234", "alice@firm.com"),
            OnCallEngineer("E2", "Bob Secondary", "SECONDARY", "+5678", "bob@firm.com"),
            OnCallEngineer("E3", "Carol CTO", "EXECUTIVE", "+9999", "carol@firm.com"),
        ]
        self.engine = OnCallEscalationManagerEngine(roster)

    def test_sev1_incident_escalation_timeline(self):
        t0 = 1000.0
        inc = SystemIncident("INC_001", "SEV_1", "Kill Switch Triggered", "Drawdown limit reached", t0)
        self.engine.create_incident(inc)

        # t = 0 min -> Primary (Alice)
        rep0 = self.engine.evaluate_escalation("INC_001", t0)
        self.assertEqual(rep0.current_assigned_tier, "PRIMARY")
        self.assertEqual(rep0.current_responder_name, "Alice Primary")
        self.assertFalse(rep0.is_sla_breached)

        # t = 3.5 mins -> Escalated to Secondary (Bob)
        rep3 = self.engine.evaluate_escalation("INC_001", t0 + 210.0)
        self.assertEqual(rep3.current_assigned_tier, "SECONDARY")
        self.assertEqual(rep3.current_responder_name, "Bob Secondary")
        self.assertFalse(rep3.is_sla_breached)

        # t = 5.5 mins -> Escalated to Executive (Carol) + SLA Breach
        rep5 = self.engine.evaluate_escalation("INC_001", t0 + 330.0)
        self.assertEqual(rep5.current_assigned_tier, "EXECUTIVE")
        self.assertEqual(rep5.current_responder_name, "Carol CTO")
        self.assertTrue(rep5.is_sla_breached)

    def test_incident_acknowledgment(self):
        t0 = 1000.0
        inc = SystemIncident("INC_002", "SEV_2", "High Latency Warning", "Feed delay > 500ms", t0)
        self.engine.create_incident(inc)

        # Acknowledge incident at t = 2 mins by Bob
        self.engine.acknowledge_incident("INC_002", "Bob Secondary", t0 + 120.0)

        rep = self.engine.evaluate_escalation("INC_002", t0 + 300.0)
        self.assertEqual(rep.status, "ACKNOWLEDGED")
        self.assertEqual(rep.current_responder_name, "Bob Secondary")
        self.assertFalse(rep.is_sla_breached)

if __name__ == '__main__':
    unittest.main()
