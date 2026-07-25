"""Unit tests for backtest-parameter-sensitivity-analysis."""
import unittest
from sensitivity_analyzer import ParameterSensitivityAnalyzer


class TestParameterSensitivityAnalyzer(unittest.TestCase):
    def setUp(self):
        self.analyzer = ParameterSensitivityAnalyzer(max_neighborhood_degradation_pct=0.15) # 15% max drop-off

    def test_robust_parameter_plateau(self):
        # Sharpe is stable across parameter range (1.49 to 1.51) -> robust
        results = self.analyzer.run_grid_sweep(
            "lookback", [10, 20, 30, 40, 50],
            lambda x: 1.5 + (x - 30) * 0.001  # Very flat plateau
        )
        report = self.analyzer.analyze_sensitivity(results, "lookback")
        self.assertTrue(report.is_robust)
        self.assertIn("ROBUST PLATEAU", report.message)

    def test_fragile_overfit_peak(self):
        # Sharpe spikes only at one point -> fragile peak
        results = self.analyzer.run_grid_sweep(
            "threshold", [0.01, 0.02, 0.03, 0.04, 0.05],
            lambda x: 5.0 if abs(x - 0.03) < 0.005 else 0.5  # Massive isolated spike at 0.03
        )
        report = self.analyzer.analyze_sensitivity(results, "threshold")
        self.assertFalse(report.is_robust)
        self.assertIn("FRAGILE PEAK", report.message)

if __name__ == "__main__":
    unittest.main()
