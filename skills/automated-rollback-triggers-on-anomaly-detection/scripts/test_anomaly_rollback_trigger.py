import unittest
from anomaly_rollback_trigger import (
    AutomatedRollbackEngine,
    RollbackThresholdConfig,
    DeploymentHealthMetrics
)

class TestAutomatedRollbackEngine(unittest.TestCase):
    def setUp(self):
        self.config = RollbackThresholdConfig(
            max_latency_ms=50.0,
            max_http_5xx_error_rate=0.01, # 1%
            max_order_reject_rate=0.02    # 2%
        )
        self.engine = AutomatedRollbackEngine(self.config)

    def test_healthy_deployment(self):
        metrics = DeploymentHealthMetrics(
            latency_ms=12.5,
            http_5xx_error_rate=0.001,
            order_reject_rate=0.005
        )
        result = self.engine.evaluate_metrics(metrics)
        self.assertFalse(result.should_rollback)
        self.assertEqual(len(result.anomalies_detected), 0)

    def test_latency_anomaly_triggers_rollback(self):
        metrics = DeploymentHealthMetrics(
            latency_ms=150.0, # Breaches 50.0
            http_5xx_error_rate=0.00,
            order_reject_rate=0.00
        )
        result = self.engine.evaluate_metrics(metrics)
        self.assertTrue(result.should_rollback)
        self.assertTrue(any("Latency Spike" in a for a in result.anomalies_detected))

    def test_trading_anomaly_triggers_rollback(self):
        metrics = DeploymentHealthMetrics(
            latency_ms=15.0,
            http_5xx_error_rate=0.00,
            order_reject_rate=0.05 # 5% breaches 2% threshold
        )
        result = self.engine.evaluate_metrics(metrics)
        self.assertTrue(result.should_rollback)
        self.assertTrue(any("Trading Anomaly" in a for a in result.anomalies_detected))

    def test_multiple_anomalies(self):
        metrics = DeploymentHealthMetrics(
            latency_ms=200.0,
            http_5xx_error_rate=0.10,
            order_reject_rate=0.15
        )
        result = self.engine.evaluate_metrics(metrics)
        self.assertTrue(result.should_rollback)
        self.assertEqual(len(result.anomalies_detected), 3)

if __name__ == '__main__':
    unittest.main()
