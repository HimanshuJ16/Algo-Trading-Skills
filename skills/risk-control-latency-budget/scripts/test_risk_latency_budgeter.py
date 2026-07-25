"""Unit tests for risk-control-latency-budget."""
import unittest
from risk_latency_budgeter import RiskControlLatencyBudgeter


class TestRiskControlLatencyBudgeter(unittest.TestCase):
    def setUp(self):
        self.budgeter = RiskControlLatencyBudgeter(default_sla_budget_ms=50.0)

    def test_latency_within_budget_passes(self):
        # 10ms ingest, 5ms eval, 15ms trans -> total 30ms <= 50ms budget
        trace = self.budgeter.record_trace(
            control_name="drawdown_circuit_breaker",
            t_event_ms=1000.0,
            t_start_ms=1010.0,
            t_end_ms=1015.0,
            t_order_sent_ms=1030.0,
        )

        self.assertFalse(trace.is_sla_violated)
        self.assertEqual(trace.total_latency_ms, 30.0)
        self.assertEqual(trace.primary_bottleneck, "TRANSMISSION")

    def test_latency_sla_breach_detected(self):
        # Slow evaluation: 100ms eval -> total 120ms > 50ms budget
        trace = self.budgeter.record_trace(
            control_name="kill_switch",
            t_event_ms=1000.0,
            t_start_ms=1010.0,
            t_end_ms=1110.0,
            t_order_sent_ms=1120.0,
        )

        self.assertTrue(trace.is_sla_violated)
        self.assertEqual(trace.total_latency_ms, 120.0)
        self.assertEqual(trace.primary_bottleneck, "EVALUATION")

        summary = self.budgeter.summarize_audit()
        self.assertFalse(summary.is_risk_pipeline_healthy)
        self.assertEqual(summary.sla_breaches_count, 1)


if __name__ == "__main__":
    unittest.main()
