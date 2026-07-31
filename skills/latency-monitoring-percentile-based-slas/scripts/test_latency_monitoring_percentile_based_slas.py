import random
import unittest
from latency_monitoring_percentile_based_slas import (
    LatencyPercentileSlaEngine, LatencySampleSeries, LatencySlaReport
)

class TestLatencyPercentileSlaEngine(unittest.TestCase):

    def setUp(self):
        self.engine = LatencyPercentileSlaEngine()
        random.seed(42)

    def test_healthy_latency_sla_compliance(self):
        # 1,000 samples centered around 30us with minor tail up to 150us -> SLA APPROVED!
        samples = [random.uniform(20.0, 40.0) for _ in range(990)] + [random.uniform(100.0, 150.0) for _ in range(10)]
        series = LatencySampleSeries(
            pipeline_stage="TICK_TO_TRADE",
            samples_microseconds=samples,
            sla_p50_target_us=50.0,
            sla_p99_target_us=200.0,
            sla_p999_target_us=1000.0
        )
        report = self.engine.audit_latency_sla(series)

        self.assertEqual(report.status, "SLA_COMPLIANCE_APPROVED")
        self.assertTrue(report.is_p50_sla_passed)
        self.assertTrue(report.is_p99_sla_passed)
        self.assertTrue(report.is_p999_sla_passed)
        self.assertLess(report.p50_latency_us, 50.0)
        self.assertLess(report.p99_latency_us, 200.0)

    def test_critical_tail_latency_breach(self):
        # 1,000 samples with 10 extreme GC tail spikes > 2,000us -> SLA_BREACH_P999_CRITICAL!
        samples = [random.uniform(20.0, 40.0) for _ in range(990)] + [random.uniform(2000.0, 3000.0) for _ in range(10)]
        series = LatencySampleSeries(
            pipeline_stage="ORDER_GATEWAY_ACK",
            samples_microseconds=samples,
            sla_p50_target_us=50.0,
            sla_p99_target_us=200.0,
            sla_p999_target_us=1000.0
        )
        report = self.engine.audit_latency_sla(series)

        self.assertEqual(report.status, "SLA_BREACH_P999_CRITICAL")
        self.assertFalse(report.is_p999_sla_passed)
        self.assertGreater(report.p999_latency_us, 1000.0)

if __name__ == '__main__':
    unittest.main()
