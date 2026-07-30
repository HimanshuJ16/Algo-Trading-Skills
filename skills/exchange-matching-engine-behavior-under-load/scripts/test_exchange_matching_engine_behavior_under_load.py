import unittest
from exchange_matching_engine_behavior_under_load import (
    ExchangeMatchingEngineLoadSimulator, EngineLoadMetrics, MatchingEngineLoadAuditReport
)

class TestExchangeMatchingEngineLoadSimulator(unittest.TestCase):

    def setUp(self):
        self.simulator = ExchangeMatchingEngineLoadSimulator(
            high_congestion_threshold=0.85, moderate_congestion_threshold=0.50
        )

    def test_normal_conditions_low_utilization(self):
        # Baseline = 20.0 us, Capacity = 50,000 msgs/s, Arrival = 10,000 msgs/s -> rho = 0.20
        # Latency = 20.0 / (1.0 - 0.20) = 25.0 us (1.25x) -> NORMAL_OPERATIONS
        metrics = EngineLoadMetrics(
            venue_id="CME_GLOBEX", baseline_latency_us=20.0,
            engine_capacity_msgs_per_sec=50_000, arrival_rate_msgs_per_sec=10_000
        )
        report = self.simulator.simulate_matching_engine_load(metrics)
        
        self.assertEqual(report.utilization_factor_rho, 0.20)
        self.assertEqual(report.effective_latency_us, 25.0)
        self.assertEqual(report.strategy_adaptation_directive, "NORMAL_OPERATIONS")
        self.assertEqual(report.adverse_selection_risk_level, "LOW")

    def test_high_congestion_spikes_latency_pauses_quoting(self):
        # Arrival = 45,000 msgs/s -> rho = 0.90 (>= 0.85 threshold)
        # Latency = 20.0 / (1.0 - 0.90) = 200.0 us (10x spike!) -> PAUSE_PASSIVE_QUOTING!
        metrics = EngineLoadMetrics(
            venue_id="CME_GLOBEX", baseline_latency_us=20.0,
            engine_capacity_msgs_per_sec=50_000, arrival_rate_msgs_per_sec=45_000
        )
        report = self.simulator.simulate_matching_engine_load(metrics)
        
        self.assertEqual(report.utilization_factor_rho, 0.90)
        self.assertEqual(report.effective_latency_us, 200.0)
        self.assertEqual(report.latency_multiplier, 10.0)
        self.assertEqual(report.strategy_adaptation_directive, "PAUSE_PASSIVE_QUOTING")
        self.assertEqual(report.adverse_selection_risk_level, "HIGH_SNIPING_RISK")

if __name__ == '__main__':
    unittest.main()
