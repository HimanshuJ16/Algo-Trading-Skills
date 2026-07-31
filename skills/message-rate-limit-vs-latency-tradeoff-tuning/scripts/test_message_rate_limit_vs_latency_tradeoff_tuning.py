import unittest
from message_rate_limit_vs_latency_tradeoff_tuning import (
    MessageRateLatencyTunerEngine, TuningConfig, MarketState, TuningReport
)

class TestMessageRateLatencyTunerEngine(unittest.TestCase):

    def setUp(self):
        self.engine = MessageRateLatencyTunerEngine()

    def test_rate_limit_tuning_applied(self):
        # CME iLink 3 Limit = 500 MPS, Target Safety = 80% (400 MPS Target)
        # 100 ticks/sec * 5 quoting pairs = 500 MPS Unthrottled (> 400 MPS Target)
        # Optimal Reprice Delay = (5 / 400) * 1000 = 12.5 ms -> RATE_LIMIT_TUNING_APPLIED!
        cfg = TuningConfig("ES", exchange_max_mps=500.0, target_safety_buffer_pct=80.0, min_reprice_delay_ms=1.0)
        state = MarketState(ticks_per_sec=100.0, price_volatility_bps=5.0, active_quoting_pairs=5)

        report = self.engine.tune_quote_reprice_parameters(cfg, state)

        self.assertEqual(report.status, "RATE_LIMIT_TUNING_APPLIED")
        self.assertTrue(report.is_tuning_required)
        self.assertEqual(report.unthrottled_message_rate_mps, 500.0)
        self.assertEqual(report.target_safety_limit_mps, 400.0)
        self.assertEqual(report.recommended_reprice_delay_ms, 12.5)
        self.assertEqual(report.projected_message_rate_mps, 400.0)

    def test_direct_pass_no_tuning_required(self):
        # Unthrottled = 10 ticks/sec * 2 pairs = 20 MPS <= 400 MPS Target -> DIRECT_PASS!
        cfg = TuningConfig("AAPL", exchange_max_mps=500.0, target_safety_buffer_pct=80.0, min_reprice_delay_ms=1.0)
        state = MarketState(ticks_per_sec=10.0, price_volatility_bps=2.0, active_quoting_pairs=2)

        report = self.engine.tune_quote_reprice_parameters(cfg, state)

        self.assertEqual(report.status, "DIRECT_PASS_NO_TUNING_REQUIRED")
        self.assertFalse(report.is_tuning_required)
        self.assertEqual(report.recommended_reprice_delay_ms, 1.0)

if __name__ == '__main__':
    unittest.main()
