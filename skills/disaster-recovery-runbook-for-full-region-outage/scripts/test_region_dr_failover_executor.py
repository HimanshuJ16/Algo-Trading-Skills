import unittest
from region_dr_failover_executor import (
    RegionDrFailoverExecutorEngine, RegionDrFailoverReport, FailoverStepResult
)

class TestRegionDrFailoverExecutorEngine(unittest.TestCase):

    def setUp(self):
        self.engine = RegionDrFailoverExecutorEngine(
            primary_region="us-east-1",
            secondary_region="us-west-2",
            rto_sla_sec=300.0                       # 5-minute RTO SLA
        )

    def test_full_region_dr_failover_execution_success(self):
        # Failover sequence with total duration = 105s (well under 300s RTO!)
        report = self.engine.execute_region_failover(
            replication_lag_sec=1.5,
            simulated_step_latencies={
                "OUTAGE_VERIFICATION": 5.0,
                "CANCEL_OPEN_ORDERS": 10.0,
                "PROMOTE_SECONDARY_DB": 45.0,
                "DNS_SWITCHOVER": 15.0,
                "COMPUTE_BOOTSTRAP_RECONCILE": 30.0
            }
        )
        
        self.assertTrue(report.is_failover_successful)
        self.assertTrue(report.is_rto_compliant)
        self.assertEqual(report.total_elapsed_seconds, 105.0)
        self.assertEqual(len(report.executed_steps), 5)
        self.assertEqual(report.executed_steps[1].step_name, "CANCEL_OPEN_ORDERS")
        self.assertEqual(report.executed_steps[2].step_name, "PROMOTE_SECONDARY_DB")

if __name__ == '__main__':
    unittest.main()
