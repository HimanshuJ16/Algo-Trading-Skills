"""Deterministic tests for the cross-strategy correlation monitor.

Expected values are derived independently of the implementation:

* Columns are built from mutually orthogonal, zero-mean sign vectors
  (Walsh/Hadamard rows), so every pairwise Pearson correlation is exactly 0
  and every column has identical variance.
* For M orthogonal, equal-volatility pods at equal weight,
  DR = (M * (1/M) * s) / sqrt(M * (1/M)^2 * s^2) = sqrt(M).
* Blending two orthogonal equal-variance columns as
  z = rho*u + sqrt(1-rho^2)*v gives corr(u, z) = rho exactly.
* Under EWMA weighting the same blend is built by Gram-Schmidt *under the EWMA
  weight measure*, which makes the target rho exact rather than approximate,
  and the weighting convention itself is cross-checked against pandas'
  independent ``ewm(span=...).cov()`` implementation.
"""

import logging
import math
import unittest

import numpy as np
import pandas as pd

from cross_strategy_correlation_monitoring import (
    MIN_EWMA_SPAN,
    MIN_OBSERVATIONS_FLOOR,
    CorrelationPairBreach,
    CrossStrategyCorrelationMonitor,
    StrategyCorrelationReport,
)


def ewma_weights(n_obs: int, span: int) -> np.ndarray:
    """Normalized EWMA weights from the documented pandas convention alpha = 2/(span+1)."""
    alpha = 2.0 / (span + 1.0)
    weights = (1.0 - alpha) ** np.arange(n_obs - 1, -1, -1, dtype=float)
    return weights / weights.sum()


def weighted_orthonormal_pair(weights: np.ndarray, seed: int) -> tuple:
    """Two vectors that are centred, unit-variance and uncorrelated under ``weights``."""
    rng = np.random.default_rng(seed)
    n_obs = len(weights)

    def dot(x, y):
        return float(weights @ (x * y))

    def centre(x):
        return x - float(weights @ x)

    u = centre(rng.standard_normal(n_obs))
    u /= math.sqrt(dot(u, u))
    v = centre(rng.standard_normal(n_obs))
    v = v - dot(u, v) * u
    v /= math.sqrt(dot(v, v))
    return u, v


def ewma_paired_matrix(rho: float, n_obs: int, span: int, seed: int = 11) -> np.ndarray:
    """Two PnL columns whose EWMA correlation at the final observation is exactly ``rho``."""
    weights = ewma_weights(n_obs, span)
    u, v = weighted_orthonormal_pair(weights, seed)
    z = rho * u + math.sqrt(1.0 - rho ** 2) * v
    return np.column_stack([0.01 * u, 0.01 * z])

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

    def test_invalid_ewma_and_shrinkage_configuration_raises(self):
        for bad_span in (0, 1, -5, 1.5, True):
            with self.subTest(ewma_span=bad_span):
                with self.assertRaises(ValueError):
                    CrossStrategyCorrelationMonitor(ewma_span=bad_span)
        # The floor itself is accepted.
        CrossStrategyCorrelationMonitor(ewma_span=MIN_EWMA_SPAN, min_observations=3)

        for bad_delta in (-0.01, 1.01, float("nan")):
            with self.subTest(shrinkage_delta=bad_delta):
                with self.assertRaises(ValueError):
                    CrossStrategyCorrelationMonitor(shrinkage_delta=bad_delta)

        with self.assertRaises(ValueError):
            CrossStrategyCorrelationMonitor(max_avg_correlation_threshold=-2.0)


# ---------------------------------------------------------------------- #
# EWMA weighting (folded in from cross-strategy-correlation-monitoring)
# ---------------------------------------------------------------------- #
class TestEwmaWeighting(unittest.TestCase):

    def setUp(self):
        self.monitor = CrossStrategyCorrelationMonitor(ewma_span=30, min_observations=30)

    def test_matches_pandas_ewm_covariance_implementation(self):
        """Cross-check the weighting convention against pandas' independent implementation."""
        rng = np.random.default_rng(17)
        matrix = rng.normal(0.0, 0.01, (200, 4))
        corr = self.monitor.compute_correlation_matrix(matrix)

        frame = pd.DataFrame(matrix, columns=["A", "B", "C", "D"])
        reference_cov = frame.ewm(span=30).cov().iloc[-4:].to_numpy()
        reference_sd = np.sqrt(np.diag(reference_cov))
        reference_corr = reference_cov / np.outer(reference_sd, reference_sd)

        np.testing.assert_allclose(corr, reference_corr, rtol=1e-10, atol=1e-12)

    def test_constructed_rho_is_recovered_exactly(self):
        for rho in (0.0, 0.35, 0.75, 0.95, -0.60):
            with self.subTest(rho=rho):
                matrix = ewma_paired_matrix(rho, n_obs=150, span=30)
                corr = self.monitor.compute_correlation_matrix(matrix)
                self.assertAlmostEqual(float(corr[0, 1]), rho, places=10)

    def test_perfectly_scaled_and_negated_series_are_exact_under_ewma(self):
        rng = np.random.default_rng(5)
        base = rng.normal(0.0, 0.01, 120)

        scaled = self.monitor.analyze_strategy_correlations(
            ["A", "B"], np.column_stack([base, 2.5 * base])
        )
        self.assertAlmostEqual(float(scaled.correlation_matrix[0, 1]), 1.0, places=12)
        self.assertEqual(len(scaled.high_correlation_breaches), 1)

        # One-sided by design: a perfect hedge is diversification, not concentration risk.
        negated = self.monitor.analyze_strategy_correlations(
            ["A", "B"], np.column_stack([base, -3.0 * base])
        )
        self.assertAlmostEqual(float(negated.correlation_matrix[0, 1]), -1.0, places=12)
        self.assertEqual(negated.high_correlation_breaches, [])

    def test_ewma_tracks_a_recent_regime_change_that_equal_weighting_misses(self):
        """A recent convergence must surface even when most of the history is uncorrelated."""
        rng = np.random.default_rng(9)
        quiet = rng.normal(0.0, 0.01, (400, 2))
        stressed_base = rng.normal(0.0, 0.01, 40)
        stressed = np.column_stack([stressed_base, stressed_base])  # rho = +1 on the tail
        matrix = np.vstack([quiet, stressed])

        fast = CrossStrategyCorrelationMonitor(ewma_span=20, min_observations=30)
        slow = CrossStrategyCorrelationMonitor(ewma_span=2000, min_observations=30)

        self.assertGreater(float(fast.compute_correlation_matrix(matrix)[0, 1]), 0.95)
        self.assertLess(float(slow.compute_correlation_matrix(matrix)[0, 1]), 0.50)

    def test_effective_sample_size_is_the_span_not_the_row_count(self):
        rng = np.random.default_rng(61)
        matrix = rng.normal(0.0, 0.01, (5000, 2))

        ewma = CrossStrategyCorrelationMonitor(ewma_span=60, min_observations=30)
        report = ewma.analyze_strategy_correlations(["A", "B"], matrix)
        self.assertEqual(report.observations_used, 5000)
        # Kish effective sample size of the EWMA weights converges to the span itself:
        # 5,000 rows at span 60 is a 60-observation estimator, not a 5,000-observation one.
        self.assertAlmostEqual(report.effective_observations, 60.0, places=3)
        self.assertEqual(report.ewma_span, 60)

        equal = CrossStrategyCorrelationMonitor(min_observations=30)
        equal_report = equal.analyze_strategy_correlations(["A", "B"], matrix)
        self.assertIsNone(equal_report.ewma_span)
        self.assertEqual(equal_report.effective_observations, 5000.0)

    def test_threshold_is_applied_before_rounding_under_ewma(self):
        # Both correlations below round to 0.7000 at the reported 4 decimal places,
        # but they sit on opposite sides of the 0.70 threshold. Comparing the raw
        # value must breach on one and not the other; comparing the rounded value
        # would breach on both.
        #
        # Deliberately NOT constructed at exactly 0.70. The weighted covariance is a
        # matrix product, so its last bit depends on the BLAS build and the CPU's
        # vectorisation: an exactly-0.70 fixture lands one ULP either side of the
        # threshold depending on the machine, which made this test flip between
        # otherwise identical CI runners.
        monitor = CrossStrategyCorrelationMonitor(
            ewma_span=30, high_correlation_threshold=0.70, min_observations=30
        )
        at = monitor.analyze_strategy_correlations(
            ["A", "B"], ewma_paired_matrix(0.70004, n_obs=150, span=30)
        )
        self.assertEqual(len(at.high_correlation_breaches), 1)
        self.assertEqual(at.high_correlation_breaches[0].correlation, 0.7)

        # Just under: the pre-rounding comparison must not promote 0.69996 to 0.7000.
        near = monitor.analyze_strategy_correlations(
            ["A", "B"], ewma_paired_matrix(0.69996, n_obs=150, span=30)
        )
        self.assertEqual(near.high_correlation_breaches, [])
        self.assertEqual(round(float(near.correlation_matrix[0, 1]), 4), 0.7)


# ---------------------------------------------------------------------- #
# Covariance shrinkage (folded in from cross-strategy-correlation-monitoring)
# ---------------------------------------------------------------------- #
class TestShrinkageSemantics(unittest.TestCase):

    def test_shrinkage_does_not_move_the_reported_correlations(self):
        """Regression for the alert-deflation defect.

        A diagonal shrinkage target deflates every off-diagonal correlation by
        exactly (1 - delta). When thresholds were applied to that deflated matrix,
        a pair at a true rho of 0.75 was reported as 0.525 at delta = 0.30 and no
        alert fired -- the engine silently under-reported the breakdown it exists
        to catch.
        """
        monitor = CrossStrategyCorrelationMonitor(
            ewma_span=30,
            shrinkage_delta=0.30,
            high_correlation_threshold=0.70,
            min_observations=30,
        )
        report = monitor.analyze_strategy_correlations(
            ["A", "B"], ewma_paired_matrix(0.75, n_obs=150, span=30)
        )
        self.assertAlmostEqual(float(report.correlation_matrix[0, 1]), 0.75, places=9)
        self.assertEqual(len(report.high_correlation_breaches), 1)
        self.assertAlmostEqual(report.high_correlation_breaches[0].correlation, 0.75, places=4)
        self.assertFalse(report.is_diversification_healthy)

    def test_shrunk_covariance_keeps_variances_and_deflates_covariances(self):
        delta = 0.25
        monitor = CrossStrategyCorrelationMonitor(
            ewma_span=30, shrinkage_delta=delta, min_observations=30
        )
        report = monitor.analyze_strategy_correlations(
            ["A", "B"], ewma_paired_matrix(0.80, n_obs=150, span=30)
        )
        shrunk = report.shrunk_covariance_matrix
        sd = np.sqrt(np.diag(shrunk))

        # Diagonal untouched by a diagonal target; off-diagonal rho scaled by (1-delta).
        self.assertAlmostEqual(shrunk[0, 1] / (sd[0] * sd[1]), (1.0 - delta) * 0.80, places=10)
        self.assertEqual(report.shrinkage_delta, delta)

    def test_zero_delta_leaves_the_covariance_unshrunk(self):
        monitor = CrossStrategyCorrelationMonitor(ewma_span=30, min_observations=30)
        report = monitor.analyze_strategy_correlations(
            ["A", "B"], ewma_paired_matrix(0.60, n_obs=120, span=30)
        )
        shrunk = report.shrunk_covariance_matrix
        sd = np.sqrt(np.diag(shrunk))
        self.assertEqual(report.shrinkage_delta, 0.0)
        self.assertAlmostEqual(shrunk[0, 1] / (sd[0] * sd[1]), 0.60, places=10)

    def test_shrinkage_makes_a_singular_covariance_invertible(self):
        """The well-conditioning claim: more pods than observations."""
        rng = np.random.default_rng(31)
        matrix = rng.normal(0.0, 0.01, (4, 6))
        monitor = CrossStrategyCorrelationMonitor(
            ewma_span=10, shrinkage_delta=0.15, min_observations=MIN_OBSERVATIONS_FLOOR
        )
        report = monitor.analyze_strategy_correlations(list("ABCDEF"), matrix)

        self.assertGreater(np.linalg.eigvalsh(report.shrunk_covariance_matrix).min(), 0.0)
        # The unshrunk estimate over 4 observations of 6 pods is rank-deficient.
        self.assertLess(np.linalg.matrix_rank(monitor.compute_correlation_matrix(matrix)), 6)


# ---------------------------------------------------------------------- #
# Average-correlation breakdown (folded in from
# cross-strategy-correlation-monitoring)
# ---------------------------------------------------------------------- #
class TestAverageCorrelationBreakdown(unittest.TestCase):

    def setUp(self):
        self.u, self.v, self.x = _tile(_H1), _tile(_H2), _tile(_H3)

    def test_average_threshold_alone_flags_the_portfolio(self):
        """No pair breaches and DR is fine, but the book is uniformly converged."""
        monitor = CrossStrategyCorrelationMonitor(
            high_correlation_threshold=0.70,
            max_avg_correlation_threshold=0.55,
            min_diversification_ratio=1.0,
        )
        matrix = np.column_stack([self.u, _blend(self.u, self.v, 0.60)])
        report = monitor.analyze_strategy_correlations(["A", "B"], matrix)

        self.assertEqual(report.high_correlation_breaches, [])
        self.assertAlmostEqual(report.average_inter_strategy_correlation, 0.60, places=4)
        self.assertFalse(report.is_diversification_healthy)
        self.assertTrue(any("AVERAGE CORRELATION BREACH" in r for r in report.recommendations))

    def test_average_is_the_mean_of_unique_off_diagonal_pairs(self):
        rng = np.random.default_rng(53)
        matrix = rng.normal(0.0, 0.01, (200, 4))
        monitor = CrossStrategyCorrelationMonitor()
        report = monitor.analyze_strategy_correlations(list("ABCD"), matrix)

        corr = monitor.compute_correlation_matrix(matrix)
        expected = float(np.mean(corr[np.triu_indices(4, k=1)]))
        self.assertAlmostEqual(report.average_inter_strategy_correlation, expected, places=4)

    def test_signed_average_nets_offsetting_pairs_so_pair_alerts_still_fire(self):
        # +1 and -1 average to 0, well below the 0.55 average threshold, yet the
        # converged pair must still be flagged. This is why the health flag ORs
        # the two conditions rather than reading the average alone.
        matrix = np.column_stack([self.u, self.u, -self.u])
        monitor = CrossStrategyCorrelationMonitor(min_diversification_ratio=1.0)
        report = monitor.analyze_strategy_correlations(["A", "B", "C"], matrix)

        self.assertLess(report.average_inter_strategy_correlation, 0.55)
        self.assertEqual(len(report.high_correlation_breaches), 1)
        self.assertFalse(report.is_diversification_healthy)


if __name__ == '__main__':
    logging.disable(logging.CRITICAL)
    unittest.main()
