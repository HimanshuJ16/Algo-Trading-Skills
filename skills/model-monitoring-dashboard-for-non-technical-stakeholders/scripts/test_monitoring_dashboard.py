"""Unit tests for model-monitoring-dashboard-for-non-technical-stakeholders."""
import unittest
from monitoring_dashboard import NonTechnicalMonitoringDashboard, HealthStatus


class TestNonTechnicalMonitoringDashboard(unittest.TestCase):
    def setUp(self):
        self.dashboard = NonTechnicalMonitoringDashboard()

    def test_healthy_model_returns_green(self):
        report = self.dashboard.evaluate_health(
            model_name="alpha_v1",
            accuracy_pct=58.5,
            staleness_days=5,
            feature_drift_psi=0.04,
            latency_ms=12.0,
        )

        self.assertEqual(report.overall_status, HealthStatus.GREEN)
        self.assertEqual(report.recommended_action, "NO_ACTION_REQUIRED")

    def test_severe_feature_drift_returns_red_and_halt_action(self):
        report = self.dashboard.evaluate_health(
            model_name="alpha_v1",
            accuracy_pct=58.5,
            staleness_days=5,
            feature_drift_psi=0.35,  # RED breach!
            latency_ms=12.0,
        )

        self.assertEqual(report.overall_status, HealthStatus.RED)
        self.assertEqual(report.recommended_action, "HALT_TRADING_IMMEDIATELY")
        self.assertIn("CRITICAL DASHBOARD ALERT", report.summary_message)


if __name__ == "__main__":
    unittest.main()
