import unittest
from risk_latency_budgeter import LatencyError, MeasurementStatus, RiskControlLatencyBudgeter


class TestRiskControlLatencyBudgeter(unittest.TestCase):
    def setUp(self): self.budgeter = RiskControlLatencyBudgeter(50.0, max_traces=2)
    def test_pass_and_ack_measurement(self):
        t = self.budgeter.record_trace("drawdown", 1000, 1010, 1015, 1030, t_ack_ms=1040)
        self.assertEqual((t.total_to_send_ms, t.total_to_ack_ms, t.primary_bottleneck, t.status), (30.0, 40.0, "TRANSMISSION", MeasurementStatus.PASS))
    def test_breach_and_per_control_summary(self):
        self.budgeter.record_trace("kill", 0, 10, 110, 120)
        self.budgeter.record_trace("other", 0, 1, 2, 3)
        s = self.budgeter.summarize_audit("kill")
        self.assertEqual((s.total_traces, s.sla_breaches_count, s.p99_total_latency_ms), (1, 1, 120.0))
    def test_unsynchronized_clock_is_not_healthy(self):
        t = self.budgeter.record_trace("kill", 0, 1, 2, 3, clock_synchronized=False)
        self.assertEqual(t.status, MeasurementStatus.UNCERTAIN)
        self.assertFalse(self.budgeter.summarize_audit().is_risk_pipeline_healthy)
    def test_invalid_time_and_values_fail_closed(self):
        with self.assertRaises(LatencyError): self.budgeter.record_trace("x", 10, 9, 11, 12)
        with self.assertRaises(LatencyError): RiskControlLatencyBudgeter(float("nan"))
        with self.assertRaises(LatencyError): self.budgeter.record_trace("x", 0, 1, 2, 3, sla_budget_ms=0)
    def test_storage_is_bounded(self):
        for i in range(3): self.budgeter.record_trace("x", i, i + 1, i + 2, i + 3)
        self.assertEqual(self.budgeter.summarize_audit().total_traces, 2)

if __name__ == "__main__": unittest.main()
