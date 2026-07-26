import unittest
from cloud_cost_anomaly_detector import (
    CloudCostAnomalyDetector, CostRecord, CostAnomalyReport
)

class TestCloudCostAnomalyDetector(unittest.TestCase):

    def setUp(self):
        self.detector = CloudCostAnomalyDetector(z_warning_threshold=2.0, z_critical_threshold=3.0)
        
        # 14 days of baseline compute cost around $100/day (std dev = $5)
        self.history = []
        for i in range(14):
            cost = 100.0 + (i % 3) * 2.5 - 2.5
            self.history.append(CostRecord(
                timestamp=f"2025-05-{i+1:02d}", service_name="EC2_Spot", category="COMPUTE",
                environment="PROD", cost_usd=cost, units_consumed=24.0
            ))

    def test_normal_cost_spending(self):
        current = CostRecord(
            timestamp="2025-05-15", service_name="EC2_Spot", category="COMPUTE",
            environment="PROD", cost_usd=102.0, units_consumed=24.0
        )
        report = self.detector.analyze_service_cost(current, self.history, trading_volume=1000.0)
        
        self.assertEqual(report.severity, "NORMAL")
        self.assertLess(report.z_score, 2.0)

    def test_critical_cost_spike_anomaly(self):
        # Current spend jumps to $500 (Baseline ~ $100 -> Z-score >> 3.0)
        current = CostRecord(
            timestamp="2025-05-15", service_name="EC2_Spot", category="COMPUTE",
            environment="PROD", cost_usd=500.0, units_consumed=24.0
        )
        report = self.detector.analyze_service_cost(current, self.history, trading_volume=1000.0)
        
        self.assertEqual(report.severity, "CRITICAL")
        self.assertGreaterEqual(report.z_score, 3.0)
        self.assertGreater(report.percentage_change_pct, 100.0)

    def test_unit_cost_calculation(self):
        current = CostRecord(
            timestamp="2025-05-15", service_name="EC2_Spot", category="COMPUTE",
            environment="PROD", cost_usd=200.0, units_consumed=24.0
        )
        report = self.detector.analyze_service_cost(current, self.history, trading_volume=10000.0)
        # Unit cost = $200 / 10,000 trades = $0.02 per trade
        self.assertEqual(report.unit_cost_usd, 0.02)

if __name__ == '__main__':
    unittest.main()
