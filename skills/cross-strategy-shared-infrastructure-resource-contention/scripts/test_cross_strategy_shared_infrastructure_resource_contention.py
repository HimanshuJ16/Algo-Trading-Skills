import unittest
from cross_strategy_shared_infrastructure_resource_contention import (
    SharedInfrastructureContentionManager, StrategyProcessInfo, ContentionMitigationReport
)

class TestSharedInfrastructureContentionManager(unittest.TestCase):

    def setUp(self):
        self.manager = SharedInfrastructureContentionManager(
            max_fix_gateway_rate_sec=1000.0,
            elevated_threshold_pct=75.0,
            critical_threshold_pct=85.0
        )
        
        self.p_hft = StrategyProcessInfo("HFT_MM", "HIGH_HFT", pinned_cpu_core=1, current_cpu_utilization_pct=25.0, current_msg_rate_per_sec=200.0, status="RUNNING")
        self.p_arb = StrategyProcessInfo("StatArb", "MEDIUM_ARB", pinned_cpu_core=2, current_cpu_utilization_pct=30.0, current_msg_rate_per_sec=150.0, status="RUNNING")
        self.p_batch = StrategyProcessInfo("EOD_Report", "LOW_BATCH", pinned_cpu_core=3, current_cpu_utilization_pct=40.0, current_msg_rate_per_sec=10.0, status="RUNNING")

        self.manager.register_process(self.p_hft)
        self.manager.register_process(self.p_arb)
        self.manager.register_process(self.p_batch)

    def test_normal_utilization(self):
        report = self.manager.evaluate_contention_state(overall_cpu_pct=45.0, overall_ram_pct=50.0, current_fix_msg_rate_sec=360.0)
        
        self.assertEqual(report.contention_state, "NORMAL")
        self.assertEqual(len(report.paused_strategies), 0)
        self.assertEqual(len(report.throttled_strategies), 0)
        self.assertEqual(self.p_batch.status, "RUNNING")

    def test_critical_contention_preempts_low_priority(self):
        # CPU spike to 92.0% >= 85.0%
        report = self.manager.evaluate_contention_state(overall_cpu_pct=92.0, overall_ram_pct=60.0, current_fix_msg_rate_sec=400.0)
        
        self.assertEqual(report.contention_state, "CRITICAL_CONTENTION")
        self.assertIn("EOD_Report", report.paused_strategies)
        self.assertIn("StatArb", report.throttled_strategies)
        
        self.assertEqual(self.p_batch.status, "PAUSED")
        self.assertEqual(self.p_arb.status, "THROTTLED")
        self.assertEqual(self.p_hft.status, "RUNNING") # Protected!

if __name__ == '__main__':
    unittest.main()
