"""Unit tests for risk-metric-recalculation-frequency-tuning."""
import unittest
from risk_frequency_tuner import RiskMetricFrequencyTuner


class TestRiskMetricFrequencyTuner(unittest.TestCase):
    def setUp(self):
        self.tuner = RiskMetricFrequencyTuner(pnl_velocity_threshold_usd_per_sec=500.0)

    def test_tiered_cadence_normal_mode(self):
        # Tick 1 @ t=0s -> Tier 1 (TICK_DRAWDOWN), Tier 2, Tier 3, Tier 4 all due at start
        rep1 = self.tuner.evaluate_due_metrics(current_pnl_usd=1000.0, current_timestamp_sec=100.0)
        self.assertIn("TICK_DRAWDOWN", rep1.metrics_due_for_calc)
        self.assertFalse(rep1.is_accelerated_mode)

        # Tick 2 @ t=101s (1 sec later, small PnL change) -> Only Tier 1 due
        rep2 = self.tuner.evaluate_due_metrics(current_pnl_usd=1010.0, current_timestamp_sec=101.0)
        self.assertEqual(rep2.metrics_due_for_calc, ["TICK_DRAWDOWN"])
        self.assertFalse(rep2.is_accelerated_mode)

    def test_high_pnl_velocity_triggers_acceleration(self):
        # Initial PnL @ t=100s
        self.tuner.evaluate_due_metrics(current_pnl_usd=1000.0, current_timestamp_sec=100.0)

        # Sudden $2,000 PnL drop 1 sec later (t=101s) -> Velocity = $2,000/sec >= $500/sec
        # Accelerated GREEKS_DELTA interval = 0.5s (due), TICK_DRAWDOWN (0s due)
        rep = self.tuner.evaluate_due_metrics(current_pnl_usd=-1000.0, current_timestamp_sec=101.0)

        self.assertTrue(rep.is_accelerated_mode)
        self.assertIn("GREEKS_DELTA", rep.metrics_due_for_calc)
        self.assertIn("TICK_DRAWDOWN", rep.metrics_due_for_calc)


if __name__ == "__main__":
    unittest.main()
