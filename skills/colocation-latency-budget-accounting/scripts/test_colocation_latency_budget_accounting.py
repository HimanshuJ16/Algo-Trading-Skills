import unittest
from colocation_latency_budget_accounting import (
    LatencyBudgetAccountingEngine, HotPathTrace
)

class TestLatencyBudgetAccountingEngine(unittest.TestCase):

    def setUp(self):
        self.engine = LatencyBudgetAccountingEngine(total_sla_ns=10000) # 10 microsecond SLA

    def test_normal_trace_no_breach(self):
        # T2T = 5000 ns (5us), well within 10,000 ns SLA
        trace = HotPathTrace(
            trace_id="TR_1",
            t0_nic_ingress_ns=1000,
            t1_decode_ns=2000,     # +1000
            t2_signal_ns=3000,     # +1000
            t3_risk_ns=4000,       # +1000
            t4_encode_ns=5000,     # +1000
            t5_nic_egress_ns=6000  # +1000
        )
        report = self.engine.audit_trace(trace)
        
        self.assertFalse(report.is_sla_breach)
        self.assertEqual(report.total_t2t_ns, 5000)
        self.assertIsNone(report.primary_bottleneck_phase)

    def test_sla_breach_bottleneck_identification(self):
        # T2T = 15000 ns (15us), exceeds 10,000 ns SLA
        # Risk check takes 8000 ns (SLA 1500 ns -> Excess 6500 ns)
        trace = HotPathTrace(
            trace_id="TR_2",
            t0_nic_ingress_ns=1000,
            t1_decode_ns=2000,     # +1000
            t2_signal_ns=3000,     # +1000
            t3_risk_ns=11000,      # +8000 (Risk bottleneck)
            t4_encode_ns=12000,    # +1000
            t5_nic_egress_ns=16000 # +4000
        )
        report = self.engine.audit_trace(trace)
        
        self.assertTrue(report.is_sla_breach)
        self.assertEqual(report.total_t2t_ns, 15000)
        self.assertEqual(report.primary_bottleneck_phase, "signal_to_risk_ns")

    def test_compute_percentiles(self):
        traces = []
        for i in range(100):
            base = i * 100000
            traces.append(HotPathTrace(
                trace_id=f"T_{i}",
                t0_nic_ingress_ns=base,
                t1_decode_ns=base + 1000,
                t2_signal_ns=base + 2000,
                t3_risk_ns=base + 3000,
                t4_encode_ns=base + 4000,
                t5_nic_egress_ns=base + 5000 + (i * 10) # Adding small jitter
            ))
            
        stats = self.engine.compute_percentiles(traces)
        
        self.assertIn("total_t2t_ns", stats)
        self.assertIn("p50", stats["total_t2t_ns"])
        self.assertIn("p99", stats["total_t2t_ns"])
        self.assertGreaterEqual(stats["total_t2t_ns"]["p99"], stats["total_t2t_ns"]["p50"])

if __name__ == '__main__':
    unittest.main()
