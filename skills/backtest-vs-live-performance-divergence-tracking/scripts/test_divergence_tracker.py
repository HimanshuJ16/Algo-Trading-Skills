"""
Unit tests for backtest-vs-live-performance-divergence-tracking skill.
"""
import unittest
from divergence_tracker import (
    BacktestLiveDivergenceTracker,
    DivergenceSeverity,
    PerformanceSnapshot,
)


class TestBacktestLiveDivergenceTracker(unittest.TestCase):

    def setUp(self):
        self.tracker = BacktestLiveDivergenceTracker()

    def test_acceptable_divergence(self):
        bt = PerformanceSnapshot(sharpe_ratio=2.0, max_drawdown_pct=10.0, win_rate_pct=55.0, fill_rate_pct=98.0, avg_slippage_bps=3.0)
        live = PerformanceSnapshot(sharpe_ratio=1.85, max_drawdown_pct=11.0, win_rate_pct=53.0, fill_rate_pct=96.0, avg_slippage_bps=4.0)

        report = self.tracker.evaluate_divergence("momentum_v1", bt, live)

        self.assertEqual(report.overall_severity, DivergenceSeverity.ACCEPTABLE)
        self.assertFalse(report.is_suspension_recommended)

    def test_critical_sharpe_decay_triggers_suspension(self):
        bt = PerformanceSnapshot(sharpe_ratio=2.5, max_drawdown_pct=8.0, win_rate_pct=60.0, fill_rate_pct=99.0, avg_slippage_bps=2.0)
        live = PerformanceSnapshot(sharpe_ratio=0.8, max_drawdown_pct=20.0, win_rate_pct=45.0, fill_rate_pct=80.0, avg_slippage_bps=10.0)

        report = self.tracker.evaluate_divergence("broken_strat", bt, live)

        self.assertEqual(report.overall_severity, DivergenceSeverity.CRITICAL)
        self.assertTrue(report.is_suspension_recommended)
        self.assertIn("CRITICAL DIVERGENCE", report.message)

    def test_warning_on_moderate_divergence(self):
        bt = PerformanceSnapshot(sharpe_ratio=2.0, max_drawdown_pct=10.0, win_rate_pct=55.0, fill_rate_pct=98.0, avg_slippage_bps=3.0)
        live = PerformanceSnapshot(sharpe_ratio=1.5, max_drawdown_pct=12.0, win_rate_pct=52.0, fill_rate_pct=95.0, avg_slippage_bps=5.0)

        report = self.tracker.evaluate_divergence("moderate_drift", bt, live)

        self.assertEqual(report.overall_severity, DivergenceSeverity.WARNING)
        self.assertFalse(report.is_suspension_recommended)


if __name__ == "__main__":
    unittest.main()
