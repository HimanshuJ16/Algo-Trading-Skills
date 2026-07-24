"""
Unit tests for benchmark-relative-performance-attribution skill.

Tests:
1. Alpha, Beta, Tracking Error, and Information Ratio calculation.
2. Brinson-Fachler sector allocation and selection effect computation.
3. Exception handling for length mismatches.
"""
import unittest
from attribution_engine import (
    AttributionError,
    AttributionSummary,
    BrinsonSectorResult,
    PerformanceAttributionEngine,
)


class TestPerformanceAttributionEngine(unittest.TestCase):

    def setUp(self):
        self.engine = PerformanceAttributionEngine(risk_free_rate=0.02, annualization_factor=252)

    def test_alpha_beta_evaluation(self):
        # Synthetic daily returns: benchmark avg ~0.05% daily; portfolio avg ~0.08% daily
        benchmark_ret = [0.001, -0.002, 0.003, -0.001, 0.002] * 20
        portfolio_ret = [0.0015, -0.0022, 0.0035, -0.0011, 0.0025] * 20  # Beta ~ 1.15, positive alpha

        summary = self.engine.evaluate_alpha_beta(portfolio_ret, benchmark_ret)

        self.assertAlmostEqual(summary.beta, 1.15, delta=0.05)
        self.assertTrue(summary.is_alpha_positive)
        self.assertGreater(summary.information_ratio, 0.0)

    def test_brinson_sector_attribution(self):
        p_weights = {"Tech": 0.50, "Financials": 0.50}
        b_weights = {"Tech": 0.30, "Financials": 0.70}

        p_sector_ret = {"Tech": 0.10, "Financials": 0.05}
        b_sector_ret = {"Tech": 0.08, "Financials": 0.04}
        total_benchmark_ret = 0.052  # 0.30*0.08 + 0.70*0.04

        results = self.engine.compute_brinson_attribution(
            p_weights, b_weights, p_sector_ret, b_sector_ret, total_benchmark_ret
        )

        self.assertEqual(len(results), 2)
        tech_res = [r for r in results if r.sector == "Tech"][0]

        # Tech allocation effect: (0.50 - 0.30) * (0.08 - 0.052) = 0.20 * 0.028 = +0.0056
        self.assertAlmostEqual(tech_res.allocation_effect, 0.0056, delta=0.001)

    def test_length_mismatch_error(self):
        with self.assertRaises(AttributionError):
            self.engine.evaluate_alpha_beta([0.01, 0.02], [0.01])


if __name__ == "__main__":
    unittest.main()
