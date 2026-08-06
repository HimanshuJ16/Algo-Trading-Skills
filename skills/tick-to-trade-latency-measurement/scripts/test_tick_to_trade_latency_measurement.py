import unittest
from tick_to_trade_latency_measurement import (
    LatencyStage,
    LatencySample,
    SLAConfig,
    TickToTradeLatencyEngine,
    LatencyError,
)


class TestTickToTradeLatencyEngine(unittest.TestCase):
    def setUp(self):
        self.engine = TickToTradeLatencyEngine()

    def test_sample_validation_monotonic(self):
        sample = LatencySample(
            sample_id="SMPL-1",
            symbol="AAPL",
            ingress_ns=1_000_000,
            decoded_ns=1_000_500,    # +500 ns
            strategy_ns=1_001_500,   # +1,000 ns
            serialized_ns=1_002_000, # +500 ns
            egress_ns=1_002_500,     # +500 ns (Total = 2.5 µs)
        )
        sample.validate()
        self.assertEqual(sample.total_t2t_ns, 2_500)
        self.assertEqual(sample.total_t2t_us, 2.5)

    def test_non_monotonic_sample_raises_error(self):
        invalid_sample = LatencySample(
            sample_id="SMPL-BAD",
            symbol="AAPL",
            ingress_ns=1_000_000,
            decoded_ns=999_000, # Ingress > Decoded -> Non-monotonic!
            strategy_ns=1_001_500,
            serialized_ns=1_002_000,
            egress_ns=1_002_500,
        )
        with self.assertRaises(LatencyError):
            invalid_sample.validate()

    def test_percentile_calculation(self):
        vals = [1.0, 2.0, 3.0, 4.0, 5.0, 6.0, 7.0, 8.0, 9.0, 10.0]
        p50 = TickToTradeLatencyEngine.calculate_percentile(vals, 50.0)
        self.assertEqual(p50, 5.5)

        p90 = TickToTradeLatencyEngine.calculate_percentile(vals, 90.0)
        self.assertEqual(p90, 9.1)

    def test_evaluate_latency_distribution(self):
        # Add 100 samples with varying latencies
        for i in range(100):
            base = i * 10_000
            sample = LatencySample(
                sample_id=f"SMPL-{i}",
                symbol="AAPL",
                ingress_ns=base,
                decoded_ns=base + 1_000 + (i * 10),      # ~1 µs
                strategy_ns=base + 3_000 + (i * 20),     # ~2 µs
                serialized_ns=base + 4_000 + (i * 30),   # ~1 µs
                egress_ns=base + 5_000 + (i * 40),       # ~1 µs (Total ~5 µs)
            )
            self.engine.record_sample(sample)

        sla = SLAConfig(max_p50_us=10.0, max_p99_us=20.0, max_tail_us=30.0)
        summary = self.engine.evaluate_latency_distribution(sla)

        self.assertEqual(summary.total_samples, 100)
        self.assertTrue(summary.t2t_p50_us > 0)
        self.assertTrue(summary.t2t_p99_us > summary.t2t_p50_us)
        self.assertIn(LatencyStage.NIC_INGRESS, summary.stage_breakdowns)
        self.assertIn(LatencyStage.DECODER_PARSING, summary.stage_breakdowns)
        self.assertIn(LatencyStage.STRATEGY_EVALUATION, summary.stage_breakdowns)
        self.assertIn(LatencyStage.ORDER_SERIALIZATION, summary.stage_breakdowns)

    def test_sla_breach_detection(self):
        # Record 1 sample with 50 µs total latency
        sample = LatencySample(
            sample_id="SMPL-SLOW",
            symbol="AAPL",
            ingress_ns=1_000_000,
            decoded_ns=1_010_000,    # +10 µs
            strategy_ns=1_030_000,   # +20 µs
            serialized_ns=1_040_000, # +10 µs
            egress_ns=1_050_000,     # +10 µs (Total = 50 µs)
        )
        self.engine.record_sample(sample)

        strict_sla = SLAConfig(max_p50_us=5.0, max_p99_us=15.0, max_tail_us=25.0)
        summary = self.engine.evaluate_latency_distribution(strict_sla)

        self.assertTrue(len(summary.sla_breaches) > 0)
        self.assertTrue(any("P50 SLA Breach" in b for b in summary.sla_breaches))


if __name__ == "__main__":
    unittest.main()

