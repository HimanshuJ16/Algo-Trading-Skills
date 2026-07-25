"""Unit tests for backtest-parameter-sensitivity-analysis."""
import unittest
from sensitivity_analyzer import ParameterSensitivityAnalyzer


class TestParameterSensitivityAnalyzer(unittest.TestCase):
    def setUp(self):
        self.analyzer = ParameterSensitivityAnalyzer(max_gradient_threshold=2.0)

    def test_robust_parameter_plateau(self):
        # Sharpe is stable across parameter range -> robust
        results = self.analyzer.run_grid_sweep(
            "lookback", [10, 20, 30, 40, 50],
            lambda x: 1.5 + (x - 30) * 0.001  # Very flat
        )
        report = self.analyzer.analyze_sensitivity(results, "lookback")
        self.assertTrue(report.is_robust)

    def test_fragile_overfit_peak(self):
        # Sharpe spikes only at one point -> fragile
        results = self.analyzer.run_grid_sweep(
            "threshold", [0.01, 0.02, 0.03, 0.04, 0.05],
            lambda x: 5.0 if abs(x - 0.03) < 0.005 else 0.5  # Spike at 0.03
        )
        report = self.analyzer.analyze_sensitivity(results, "threshold")
        self.assertFalse(report.is_robust)
        self.assertIn("FRAGILE", report.message)

if __name__ == "__main__":
    unittest.main()
