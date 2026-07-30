import unittest
from data_quality_monitoring_dashboard import (
    DataQualityMonitoringEngine, FeedTelemetryBatch, DataQualityMonitoringReport
)

class TestDataQualityMonitoringEngine(unittest.TestCase):

    def setUp(self):
        self.engine = DataQualityMonitoringEngine(min_healthy_score=85.0, critical_failover_score=70.0)

    def test_healthy_feed_dq_score(self):
        batch = FeedTelemetryBatch(
            vendor_id="BLOOMBERG", symbol="AAPL", total_records=1000,
            null_records_count=0, duplicate_records_count=0, outlier_records_count=0,
            avg_latency_ms=2.0, ticks_per_second=50.0
        )
        report = self.engine.audit_feed_quality(batch)
        
        self.assertEqual(report.status, "HEALTHY")
        self.assertGreaterEqual(report.composite_dq_score, 95.0)
        self.assertFalse(report.is_failover_recommended)

    def test_critical_degraded_feed_triggers_failover(self):
        # Degraded feed: 50 nulls (5%), 600ms latency, 0 TPS (dead feed)
        batch = FeedTelemetryBatch(
            vendor_id="REFINITIV", symbol="AAPL", total_records=1000,
            null_records_count=50, duplicate_records_count=10, outlier_records_count=10,
            avg_latency_ms=600.0, ticks_per_second=0.0
        )
        report = self.engine.audit_feed_quality(batch)
        
        self.assertEqual(report.status, "CRITICAL")
        self.assertTrue(report.is_failover_recommended)
        self.assertIn("DEAD FEED", report.alerts[0])

if __name__ == '__main__':
    unittest.main()
