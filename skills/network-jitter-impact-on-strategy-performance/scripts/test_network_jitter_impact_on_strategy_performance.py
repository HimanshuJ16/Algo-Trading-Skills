import unittest
from network_jitter_impact_on_strategy_performance import (
    NetworkJitterImpactAnalyzerEngine, LatencyPacketSample, JitterSimulationConfig, JitterImpactReport
)

class TestNetworkJitterImpactAnalyzerEngine(unittest.TestCase):

    def setUp(self):
        self.engine = NetworkJitterImpactAnalyzerEngine()

    def test_healthy_low_jitter_impact(self):
        # 100 packet samples with 2.0ms mean latency and low jitter (std ~ 0.2ms)
        t0 = 1000000000
        samples = []
        for i in range(100):
            delay_ns = int((2.0 + (0.1 if i % 2 == 0 else -0.1)) * 1000000)
            samples.append(LatencyPacketSample(f"PKT_{i}", t0, t0 + delay_ns))

        report = self.engine.analyze_jitter_impact(samples)

        self.assertEqual(report.status, "JITTER_HEALTHY")
        self.assertTrue(report.is_jitter_acceptable)
        self.assertAlmostEqual(report.mean_latency_ms, 2.0, delta=0.2)
        self.assertGreater(report.simulated_degraded_sharpe, 2.0)

    def test_high_jitter_warning_and_sharpe_degradation(self):
        # High jitter samples (std ~ 4.0ms) -> Sharpe drops below target min 1.0 -> JITTER_HIGH_RISK_WARNING
        cfg = JitterSimulationConfig(base_sharpe=2.5, jitter_penalty_coeff=0.5, target_sharpe_min=1.0)
        engine = NetworkJitterImpactAnalyzerEngine(cfg)

        t0 = 1000000000
        samples = []
        for i in range(100):
            # Alternating 1.0ms and 9.0ms delays
            delay_ms = 1.0 if i % 2 == 0 else 9.0
            samples.append(LatencyPacketSample(f"PKT_{i}", t0, t0 + int(delay_ms * 1000000)))

        report = engine.analyze_jitter_impact(samples)

        self.assertEqual(report.status, "JITTER_HIGH_RISK_WARNING")
        self.assertFalse(report.is_jitter_acceptable)
        self.assertGreater(report.jitter_std_ms, 3.5)

if __name__ == '__main__':
    unittest.main()
