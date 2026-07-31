import unittest
from market_data_latency_monitoring_per_vendor import (
    MarketDataLatencyMonitorEngine, LatencySample, VendorLatencyReport
)

class TestMarketDataLatencyMonitorEngine(unittest.TestCase):

    def setUp(self):
        self.engine = MarketDataLatencyMonitorEngine(max_allowed_p99_latency_us=500.0)

    def test_healthy_vendor_latency_monitoring(self):
        # Generate 100 samples for BLOOMBERG with total latency ~ 100us (P99 <= 500us) -> HEALTHY!
        samples = []
        for i in range(100):
            t_ex = 1000000.0 + i * 100.0
            t_ven = t_ex + 30.0
            t_nic = t_ven + 20.0
            t_app = t_nic + 50.0 # Total = 100us
            samples.append(LatencySample("BLOOMBERG", "AAPL", t_ex, t_ven, t_nic, t_app))

        report = self.engine.audit_vendor_latencies(samples)

        self.assertEqual(report.status, "ALL_VENDORS_HEALTHY")
        self.assertIn("BLOOMBERG", report.vendor_metrics)
        metrics = report.vendor_metrics["BLOOMBERG"]
        self.assertTrue(metrics.is_sla_compliant)
        self.assertEqual(metrics.p50_us, 100.0)
        self.assertEqual(metrics.p99_us, 100.0)

    def test_sla_breach_detection(self):
        # Vendor A: Healthy (~100us total)
        # Vendor B: Breaching (Samples 90-99 spike to 1,500us -> P99 > 500us) -> BREACH!
        samples = []
        for i in range(100):
            t_ex = 1000000.0 + i * 100.0
            # Vendor A
            samples.append(LatencySample("VENDOR_A", "AAPL", t_ex, t_ex + 30, t_ex + 50, t_ex + 100))
            # Vendor B (Tail 10% spikes to 1500us)
            tot_b = 1500.0 if i >= 90 else 100.0
            samples.append(LatencySample("VENDOR_B", "AAPL", t_ex, t_ex + 30, t_ex + 50, t_ex + tot_b))

        report = self.engine.audit_vendor_latencies(samples)

        self.assertEqual(report.status, "VENDOR_SLA_BREACH_ALERT")
        self.assertIn("VENDOR_B", report.sla_breaching_vendors)
        self.assertNotIn("VENDOR_A", report.sla_breaching_vendors)
        self.assertFalse(report.vendor_metrics["VENDOR_B"].is_sla_compliant)

if __name__ == '__main__':
    unittest.main()
