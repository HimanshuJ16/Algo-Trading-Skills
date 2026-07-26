import unittest
import numpy as np
from data_replication_monitoring import (
    CrossRegionReplicationLagMonitor, ReplicationHeartbeat, ReplicationLagHealthReport
)

class TestCrossRegionReplicationLagMonitor(unittest.TestCase):

    def setUp(self):
        self.monitor = CrossRegionReplicationLagMonitor(
            p99_warning_threshold_ms=100.0,
            p99_unsafe_threshold_ms=500.0
        )
        
        # Generate 100 heartbeats for us-east-1 -> eu-west-1 around 40ms lag
        self.heartbeats = []
        for i in range(100):
            write_t = 1000.0 + i * 100.0
            receive_t = write_t + 40.0 + (i % 5) # 40ms to 44ms
            self.heartbeats.append(ReplicationHeartbeat(
                heartbeat_id=f"HB_{i}", origin_region="us-east-1",
                replica_region="eu-west-1", primary_write_timestamp_ms=write_t,
                replica_receive_timestamp_ms=receive_t
            ))

    def test_healthy_replication_lag(self):
        report = self.monitor.evaluate_replica_health("us-east-1", "eu-west-1", self.heartbeats)
        
        self.assertEqual(report.status, "HEALTHY")
        self.assertFalse(report.is_read_failover_recommended)
        self.assertLessEqual(report.p99_lag_ms, 50.0)

    def test_unsafe_stale_replication_lag(self):
        # Inject 5 delayed heartbeats with 1200ms lag -> P99 > 500ms
        for i in range(5):
            write_t = 20000.0 + i * 100.0
            receive_t = write_t + 1200.0
            self.heartbeats.append(ReplicationHeartbeat(
                heartbeat_id=f"HB_STALE_{i}", origin_region="us-east-1",
                replica_region="eu-west-1", primary_write_timestamp_ms=write_t,
                replica_receive_timestamp_ms=receive_t
            ))
            
        report = self.monitor.evaluate_replica_health("us-east-1", "eu-west-1", self.heartbeats)
        
        self.assertEqual(report.status, "UNSAFE_STALE")
        self.assertTrue(report.is_read_failover_recommended)
        self.assertGreaterEqual(report.p99_lag_ms, 500.0)

if __name__ == '__main__':
    unittest.main()
