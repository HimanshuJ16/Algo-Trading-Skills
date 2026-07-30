import unittest
from custody_solution_uptime_and_liveness_guarantees import (
    CustodyLivenessMonitorEngine, ProviderSlaConfig, CustodyHealthProbe, CustodyLivenessAuditReport
)

class TestCustodyLivenessMonitorEngine(unittest.TestCase):

    def setUp(self):
        # Config: Target Uptime 99.9%, Max signing latency 2000ms, MPC 2-of-3
        self.cfg = ProviderSlaConfig(
            provider_id="FIREBLOCKS_01",
            provider_name="Fireblocks Institutional Custody",
            target_uptime_pct=99.9,
            max_signing_latency_ms=2000.0,
            mpc_threshold_k=2,
            mpc_total_n=3
        )
        self.engine = CustodyLivenessMonitorEngine(self.cfg)

    def test_healthy_custody_liveness(self):
        probes = [
            CustodyHealthProbe(f"P_{i}", 1000.0 + i * 10, True, 450.0, active_mpc_nodes=3)
            for i in range(100)
        ]
        report = self.engine.audit_liveness(probes)
        
        self.assertEqual(report.status, "HEALTHY")
        self.assertEqual(report.rolling_uptime_pct, 100.0)
        self.assertTrue(report.is_mpc_quorum_maintained)
        self.assertFalse(report.is_failover_recommended)

    def test_mpc_quorum_loss_triggers_liveness_halt_and_failover(self):
        probes = [
            CustodyHealthProbe(f"P_{i}", 1000.0 + i * 10, True, 450.0, active_mpc_nodes=3)
            for i in range(10)
        ]
        # Latest probe loses 2 nodes -> 1 active node < k=2
        probes.append(CustodyHealthProbe("P_FAIL", 2000.0, True, 450.0, active_mpc_nodes=1))
        
        report = self.engine.audit_liveness(probes)
        
        self.assertEqual(report.status, "QUORUM_LOST_LIVENESS_HALT")
        self.assertFalse(report.is_mpc_quorum_maintained)
        self.assertTrue(report.is_failover_recommended)

if __name__ == '__main__':
    unittest.main()
