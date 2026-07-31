import unittest
from breach_rca_generator import (
    InputData, BreachRcaGenerator,
    BreachIncidentSpec, RCAReport
)

class TestBreachRcaGenerator(unittest.TestCase):

    def setUp(self):
        self.engine = BreachRcaGenerator()

    def test_process_valid_legacy(self):
        data = InputData("test", 10.0)
        self.assertTrue(self.engine.process(data))

    def test_process_invalid_legacy(self):
        data = InputData("test", -10.0)
        self.assertFalse(self.engine.process(data))

    def test_history_legacy(self):
        data = InputData("test", 10.0)
        self.engine.process(data)
        self.assertEqual(len(self.engine.history), 1)

    def test_generate_rca_report_success(self):
        spec = BreachIncidentSpec(
            incident_id="INC-2026-001",
            incident_date="2026-07-31",
            strategy_id="STAT_ARB_PROD",
            breach_type="POSITION_LIMIT_EXCEEDED",
            severity="CRITICAL",
            financial_loss_usd=25000.0,
            five_whys=[
                "Order size exceeded position limit.",
                "Pre-trade risk limit check was bypassed.",
                "Risk gateway configuration flag was toggled off.",
                "Staging deployment script lacked automated config validation.",
                "CI/CD pipeline omitted pre-deployment risk config assertions."
            ],
            timeline_events=[
                ("14:00:00 UTC", "Deployment script ran."),
                ("14:05:00 UTC", "Limit breached by order #9921."),
                ("14:05:01 UTC", "Kill switch engaged automatically.")
            ],
            action_items=[
                "Add CI/CD pre-deployment assertion for risk gateway flags.",
                "Implement dual-approval requirement for risk configuration changes."
            ]
        )

        report = self.engine.generate_rca_report(spec)
        self.assertEqual(report.status, "RCA_GENERATED_SUCCESS")
        self.assertTrue(report.is_valid_rca)
        self.assertEqual(report.five_whys_depth, 5)
        self.assertIn("# ROOT CAUSE ANALYSIS (RCA) REPORT: INC-2026-001", report.markdown_document)

    def test_insufficient_five_whys_depth(self):
        spec = BreachIncidentSpec(
            incident_id="INC-BAD-01",
            incident_date="2026-07-31",
            strategy_id="TEST",
            breach_type="RUNAWAY_ALGO",
            severity="MAJOR",
            financial_loss_usd=1000.0,
            five_whys=["Human error"], # Only 1 level
            timeline_events=[],
            action_items=["Fix bug"]
        )
        report = self.engine.generate_rca_report(spec)
        self.assertEqual(report.status, "INSUFFICIENT_5_WHYS_DEPTH")
        self.assertFalse(report.is_valid_rca)

if __name__ == '__main__':
    unittest.main()
