import unittest
from graceful_degradation_router import (
    GracefulDegradationRouterEngine, SystemHealthMetrics, TradingTask, GracefulDegradationAuditReport
)

class TestGracefulDegradationRouterEngine(unittest.TestCase):

    def setUp(self):
        self.engine = GracefulDegradationRouterEngine(
            partial_degradation_cpu_pct=75.0,
            partial_degradation_packet_loss_pct=1.0,
            critical_outage_packet_loss_pct=10.0
        )
        self.tasks = [
            TradingTask("T1", "MASS_CANCEL", "P1_CRITICAL"),
            TradingTask("T2", "STOP_LOSS_EXIT", "P2_HIGH"),
            TradingTask("T3", "NEW_SIGNAL_ENTRY", "P3_MEDIUM"),
            TradingTask("T4", "ANALYTICS_TICK_LOG", "P4_LOW")
        ]

    def test_normal_healthy_processes_all_priorities(self):
        health = SystemHealthMetrics(cpu_utilization_pct=40.0, network_packet_loss_pct=0.1, db_connection_latency_ms=10.0)
        report = self.engine.process_and_filter_tasks(health, self.tasks)

        self.assertEqual(report.system_mode, "NORMAL_HEALTHY")
        self.assertEqual(report.processed_tasks_count, 4)
        self.assertEqual(report.shed_tasks_count, 0)

    def test_partial_degradation_sheds_p4_analytics(self):
        # CPU = 85.0% -> PARTIAL DEGRADATION! Drops P4 (T4)
        health = SystemHealthMetrics(cpu_utilization_pct=85.0, network_packet_loss_pct=0.5, db_connection_latency_ms=50.0)
        report = self.engine.process_and_filter_tasks(health, self.tasks)

        self.assertEqual(report.system_mode, "PARTIAL_DEGRADATION")
        self.assertEqual(report.processed_tasks_count, 3)
        self.assertEqual(report.shed_tasks_count, 1)
        self.assertIn("T4", report.shed_task_ids)

    def test_critical_outage_executes_p1_only(self):
        # Packet Loss = 15.0% -> CRITICAL OUTAGE! Drops P2, P3, P4 (T2, T3, T4). Executes P1 (T1) ONLY!
        health = SystemHealthMetrics(cpu_utilization_pct=95.0, network_packet_loss_pct=15.0, db_connection_latency_ms=500.0)
        report = self.engine.process_and_filter_tasks(health, self.tasks)

        self.assertEqual(report.system_mode, "CRITICAL_OUTAGE")
        self.assertEqual(report.processed_tasks_count, 1)
        self.assertEqual(report.shed_tasks_count, 3)
        self.assertIn("T1", report.processed_task_ids)

if __name__ == '__main__':
    unittest.main()
