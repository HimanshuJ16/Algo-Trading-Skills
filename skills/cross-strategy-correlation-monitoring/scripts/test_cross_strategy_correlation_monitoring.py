"""Deterministic tests for the cross-strategy correlation monitor.

Expected values are derived independently of the implementation:

* Columns are built from mutually orthogonal, zero-mean sign vectors
  (Walsh/Hadamard rows), so every pairwise Pearson correlation is exactly 0
  and every column has identical variance.
* For M orthogonal, equal-volatility pods at equal weight,
  DR = (M * (1/M) * s) / sqrt(M * (1/M)^2 * s^2) = sqrt(M).
* Blending two orthogonal equal-variance columns as
  z = rho*u + sqrt(1-rho^2)*v gives corr(u, z) = rho exactly.
"""

import logging
import math
import unittest

import numpy as np

from cross_strategy_correlation_monitoring import (
    MIN_OBSERVATIONS_FLOOR,
    CorrelationPairBreach,
    CrossStrategyCorrelationMonitor,
    StrategyCorrelationReport,
)

# Walsh rows of order 8: pairwise orthogonal and zero-mean.
_H1 = np.array([1.0, -1.0, 1.0, -1.0, 1.0, -1.0, 1.0, -1.0])
_H2 = np.array([1.0, 1.0, -1.0, -1.0, 1.0, 1.0, -1.0, -1.0])
_H3 = np.array([1.0, 1.0, 1.0, 1.0, -1.0, -1.0, -1.0, -1.0])


def _tile(vec: np.ndarray, reps: int = 4) -> np.ndarray:
    """Repeats a Walsh row; tiling preserves zero mean and orthogonality."""
    return np.tile(vec, reps)


def _blend(u: np.ndarray, v: np.ndarray, rho: float) -> np.ndarray:
    """Column with corr(u, .) == rho exactly, for orthogonal equal-variance u, v."""
    return rho * u + math.sqrt(1.0 - rho ** 2) * v


class TestCrossStrategyCorrelationMonitor(unittest.TestCase):

    def setUp(self):
        self.monitor = CrossStrategyCorrelationMonitor(
            high_correlation_threshold=0.70,
            redundancy_threshold=0.85,
            min_diversification_ratio=1.20,
        )
        self.u, self.v, self.x = _tile(_H1), _tile(_H2), _tile(_H3)
        self.names = ["StatArb", "TrendFollow", "OptionsArb"]

    # ------------------------------------------------------------------ #
    # Core quantitative behaviour
    # ------------------------------------------------------------------ #
    def test_orthogonal_pods_give_dr_sqrt_m_and_no_breaches(self):
        matrix = np.column_stack([self.u, self.v, self.x])
        report = self.monitor.analyze_strategy_correlations(self.names, matrix)

        self.assertIsInstance(report, StrategyCorrelationReport)
        np.testing.assert_allclose(report.correlation_matrix, np.eye(3), atol=1e-9)
        # Independently derived: sqrt(3) = 1.7320508...
        self.assertAlmostEqual(report.diversification_ratio, round(math.sqrt(3), 4), places=9)
        self.assertEqual(report.high_correlation_breaches, [])
        self.assertTrue(report.is_diversification_healthy)
        self.assertEqual(report.observations_used, 32)
        np.testing.assert_allclose(report.weights, np.full(3, 1 / 3))

    def test_exact_rho_and_severity_classification(self):
        # StatArb vs TrendFollow: rho = 0.90 exactly -> REDUNDANT_POD.
        # StatArb vs OptionsArb / TrendFollow vs OptionsArb: orthogonal third leg.
        matrix = np.column_stack([self.u, _blend(self.u, self.v, 0.90), self.x])
        report = self.monitor.analyze_strategy_correlations(self.names, matrix)

        self.assertAlmostEqual(float(report.correlation_matrix[0, 1]), 0.90, places=9)
        self.assertAlmostEqual(float(report.correlation_matrix[0, 2]), 0.0, places=9)
        self.assertEqual(len(report.high_correlation_breaches), 1)
        breach = report.high_correlation_breaches[0]
        self.assertIsInstance(breach, CorrelationPairBreach)
        self.assertEqual((breach.strategy_a, breach.strategy_b), ("StatArb", "TrendFollow"))
        self.assertEqual(breach.severity, "REDUNDANT_POD")
        self.assertAlmostEqual(breach.correlation, 0.90, places=4)
        self.assertFalse(report.is_diversification_healthy)

    def test_threshold_band_is_inclusive_on_the_high_side(self):
        matrix = np.column_stack([self.u, _blend(self.u, self.v, 0.75), self.x])

        strict = CrossStrategyCorrelationMonitor(
            high_correlation_threshold=0.7499, redundancy_threshold=0.85
        )
        self.assertEqual(
            [b.severity for b in strict.analyze_strategy_correlations(self.names, matrix).high_correlation_breaches],
            ["HIGH_CORRELATION"],
        )

        lenient = CrossStrategyCorrelationMonitor(
            high_correlation_threshold=0.7501, redundancy_threshold=0.85
        )
        self.assertEqual(
            lenient.analyze_strategy_correlations(self.names, matrix).high_correlation_breaches, []
        )

    def test_negative_correlation_is_not_flagged_as_a_breach(self):
        # rho = -1 is a diversification benefit, not a concentration breach.
        matrix = np.column_stack([self.u, -self.u, self.x])
        report = self.monitor.analyze_strategy_correlations(self.names, matrix)
        self.assertAlmostEqual(float(report.correlation_matrix[0, 1]), -1.0, places=9)
        self.assertEqual(report.high_correlation_breaches, [])

    def test_perfectly_hedged_portfolio_reports_infinite_dr_not_one(self):
        # Regression: the previous implementation returned DR=1.0 ("no diversification")
        # for a portfolio whose variance is zero, inverting the metric's meaning.
        matrix = np.column_stack([self.u, -self.u])
        dr = self.monitor.calculate_diversification_ratio(matrix, np.array([0.5, 0.5]))
        self.assertTrue(math.isinf(dr))

    def test_public_dr_method_matches_report_dr(self):
        # Regression: analyze_* previously duplicated the DR formula and the two
        # paths disagreed by four orders of magnitude on degenerate input.
        matrix = np.column_stack([self.u, _blend(self.u, self.v, 0.999), self.x])
        weights = np.array([2.0, 1.0, 1.0])
        report = self.monitor.analyze_strategy_correlations(self.names, matrix, weights)
        self.assertEqual(
            report.diversification_ratio,
            self.monitor.calculate_diversification_ratio(matrix, weights),
        )

    def test_weights_are_normalized_and_change_the_ratio(self):
        matrix = np.column_stack([self.u, self.v, self.x])
        report = self.monitor.analyze_strategy_correlations(self.names, matrix, [2.0, 1.0, 1.0])
        np.testing.assert_allclose(report.weights, np.array([0.5, 0.25, 0.25]))
        # Independently derived for orthogonal unit-vol columns:
        # DR = 1 / sqrt(0.5^2 + 0.25^2 + 0.25^2) = 1/sqrt(0.375).
        self.assertAlmostEqual(report.diversification_ratio, round(1 / math.sqrt(0.375), 4), places=9)

    # ------------------------------------------------------------------ #
    # Reporting completeness
    # ------------------------------------------------------------------ #
    def test_dr_shortfall_is_reported_even_when_a_breach_exists(self):
        # Regression: a DR breach used to be suppressed whenever any correlation
        # breach was present, hiding it from the operator-facing recommendations.
        matrix = np.column_stack([self.u, _blend(self.u, self.v, 0.99)])
        report = self.monitor.analyze_strategy_correlations(["A", "B"], matrix)

        self.assertGreaterEqual(len(report.high_correlation_breaches), 1)
        self.assertLess(report.diversification_ratio, 1.20)
        self.assertTrue(any("Diversification Ratio" in r for r in report.recommendations))
        self.assertTrue(any("REDUNDANT PODS" in r for r in report.recommendations))
        self.assertFalse(report.is_diversification_healthy)

    # ------------------------------------------------------------------ #
    # Rolling window
    # ------------------------------------------------------------------ #
    def test_lookback_window_uses_only_trailing_observations(self):
        history = np.column_stack([self.u, self.v])            # 32 rows, rho = 0
        recent = np.column_stack([self.u, self.u])             # 32 rows, rho = 1
        full = np.vstack([history, recent])

        windowed = CrossStrategyCorrelationMonitor(lookback_window=32)
        report = windowed.analyze_strategy_correlations(["A", "B"], full)
        self.assertEqual(report.observations_used, 32)
        self.assertAlmostEqual(float(report.correlation_matrix[0, 1]), 1.0, places=9)
        self.assertEqual(report.high_correlation_breaches[0].severity, "REDUNDANT_POD")

        unwindowed = self.monitor.analyze_strategy_correlations(["A", "B"], full)
        self.assertEqual(unwindowed.observations_used, 64)
        self.assertLess(float(unwindowed.correlation_matrix[0, 1]), 0.70)

    # ------------------------------------------------------------------ #
    # Input validation
    # ------------------------------------------------------------------ #
    def test_stale_zero_variance_pod_raises_instead_of_faking_diversification(self):
        # Regression: a flat pod used to be imputed to rho=0.0, reporting a
        # healthy portfolio (DR > 1.2) built on a dead feed.
        matrix = np.column_stack([self.u, np.zeros(32), self.x])
        with self.assertRaises(ValueError) as ctx:
            self.monitor.analyze_strategy_correlations(self.names, matrix)
        self.assertIn("Zero-variance", str(ctx.exception))

    def test_non_finite_returns_raise(self):
        for bad in (np.nan, np.inf):
            matrix = np.column_stack([self.u, self.v, self.x])
            matrix[5, 1] = bad
            with self.assertRaises(ValueError):
                self.monitor.analyze_strategy_correlations(self.names, matrix)

    def test_short_window_raises_rather_than_flagging_artificial_redundancy(self):
        # With 2 rows every off-diagonal correlation is algebraically +/-1.
        two_rows = np.array([[0.01, -0.02, 0.005], [0.02, -0.01, 0.007]])
        with self.assertRaises(ValueError):
            self.monitor.analyze_strategy_correlations(self.names, two_rows)

        floor_monitor = CrossStrategyCorrelationMonitor(min_observations=MIN_OBSERVATIONS_FLOOR)
        with self.assertRaises(ValueError):
            floor_monitor.analyze_strategy_correlations(self.names, two_rows)

    def test_column_and_name_mismatches_raise_clear_errors(self):
        matrix = np.column_stack([self.u, self.v, self.x])
        with self.assertRaises(ValueError):
            self.monitor.analyze_strategy_correlations(["A", "B"], matrix)
        with self.assertRaises(ValueError):
            self.monitor.analyze_strategy_correlations(["A", "A", "B"], matrix)
        with self.assertRaises(ValueError):
            self.monitor.analyze_strategy_correlations(["A"], self.u.reshape(-1, 1))

    def test_invalid_weights_raise(self):
        matrix = np.column_stack([self.u, self.v, self.x])
        for bad_weights in ([1.0, 1.0], [1.0, -1.0, 1.0], [0.0, 0.0, 0.0], [1.0, np.nan, 1.0]):
            with self.assertRaises(ValueError):
                self.monitor.analyze_strategy_correlations(self.names, matrix, bad_weights)

    def test_invalid_configuration_raises(self):
        with self.assertRaises(ValueError):
            CrossStrategyCorrelationMonitor(high_correlation_threshold=1.5)
        with self.assertRaises(ValueError):
            CrossStrategyCorrelationMonitor(high_correlation_threshold=0.90, redundancy_threshold=0.80)
        with self.assertRaises(ValueError):
            CrossStrategyCorrelationMonitor(min_diversification_ratio=0.5)
        with self.assertRaises(ValueError):
            CrossStrategyCorrelationMonitor(min_observations=2)
        with self.assertRaises(ValueError):
            CrossStrategyCorrelationMonitor(min_observations=30, lookback_window=20)


if __name__ == '__main__':
    logging.disable(logging.CRITICAL)
    unittest.main()
