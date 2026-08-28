"""Behavioural tests for the live strategy correlation matrix engine.

Expected values are derived independently of the engine's own code path:

* Perfectly scaled / negated columns give rho = +1 / -1 exactly under *any* weighting,
  so those cases need no reference implementation at all.
* Intermediate correlations are built by Gram-Schmidt under the EWMA weight measure
  (z = rho*u + sqrt(1-rho^2)*v with u, v weighted-orthonormal), which makes the target
  rho exact rather than approximate.
* The weighting convention itself is cross-checked against pandas' independent
  ``ewm(span=...).cov()`` implementation.
"""

import math
import unittest

import numpy as np
import pandas as pd

from strategy_correlation_matrix_live_recomputation import (
    MIN_EWMA_SPAN,
    MIN_OBSERVATIONS_FLOOR,
    StrategyCorrelationMatrixLiveRecomputation,
    StrategyCorrelationMatrixLiveRecomputationConfig,
    StrategyCorrelationMatrixEngine,
    LiveCorrelationMatrixReport,
    StrategyCorrelationPair,
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


def paired_series(rho: float, n_obs: int, span: int, seed: int = 11) -> pd.DataFrame:
    """Two return series whose EWMA correlation at the final observation is exactly ``rho``."""
    weights = ewma_weights(n_obs, span)
    u, v = weighted_orthonormal_pair(weights, seed)
    z = rho * u + math.sqrt(1.0 - rho ** 2) * v
    return pd.DataFrame({"Strat_A": 0.01 * u, "Strat_B": 0.01 * z})


class TestStrategyCorrelationMatrixLegacy(unittest.TestCase):
    def test_execute_true(self):
        config = StrategyCorrelationMatrixLiveRecomputationConfig(enabled=True)
        engine = StrategyCorrelationMatrixLiveRecomputation(config)
        self.assertTrue(engine.execute())

    def test_execute_false(self):
        config = StrategyCorrelationMatrixLiveRecomputationConfig(enabled=False)
        engine = StrategyCorrelationMatrixLiveRecomputation(config)
        self.assertFalse(engine.execute())


class TestConstructorValidation(unittest.TestCase):
    def test_rejects_span_below_floor(self):
        for bad_span in (0, 1, -5):
            with self.assertRaises(ValueError):
                StrategyCorrelationMatrixEngine(ewma_span=bad_span)
        # The floor itself is accepted.
        StrategyCorrelationMatrixEngine(ewma_span=MIN_EWMA_SPAN, min_observations=3)

    def test_rejects_shrinkage_delta_outside_unit_interval(self):
        for bad_delta in (-0.01, 1.01, float("nan")):
            with self.assertRaises(ValueError):
                StrategyCorrelationMatrixEngine(shrinkage_delta=bad_delta)

    def test_rejects_thresholds_outside_correlation_range(self):
        with self.assertRaises(ValueError):
            StrategyCorrelationMatrixEngine(high_correlation_threshold=1.5)
        with self.assertRaises(ValueError):
            StrategyCorrelationMatrixEngine(max_avg_correlation_threshold=-2.0)

    def test_rejects_min_observations_below_floor(self):
        with self.assertRaises(ValueError):
            StrategyCorrelationMatrixEngine(min_observations=MIN_OBSERVATIONS_FLOOR - 1)


class TestInputValidation(unittest.TestCase):
    def setUp(self):
        self.engine = StrategyCorrelationMatrixEngine(ewma_span=30, min_observations=30)
        rng = np.random.default_rng(3)
        self.good = pd.DataFrame({
            "Strat_A": rng.normal(0.0, 0.01, 100),
            "Strat_B": rng.normal(0.0, 0.01, 100),
        })

    def test_rejects_non_dataframe(self):
        with self.assertRaises(TypeError):
            self.engine.compute_live_correlation_matrix(self.good.to_numpy())

    def test_rejects_single_strategy(self):
        with self.assertRaises(ValueError):
            self.engine.compute_live_correlation_matrix(self.good[["Strat_A"]])

    def test_rejects_too_few_observations(self):
        with self.assertRaises(ValueError):
            self.engine.compute_live_correlation_matrix(self.good.iloc[:29])
        # Exactly min_observations is accepted.
        self.engine.compute_live_correlation_matrix(self.good.iloc[:30])

    def test_rejects_nan_and_inf(self):
        for bad_value in (np.nan, np.inf):
            frame = self.good.copy()
            frame.iloc[10, 0] = bad_value
            with self.assertRaises(ValueError) as ctx:
                self.engine.compute_live_correlation_matrix(frame)
            self.assertIn("Strat_A", str(ctx.exception))

    def test_rejects_flat_strategy_instead_of_reporting_zero_correlation(self):
        """Regression: a dead strategy used to be reported as rho = 0 with everything.

        That both invented a perfect diversifier and pulled the portfolio average below
        its breakdown threshold, masking a genuine breach on the live pairs.
        """
        frame = self.good.copy()
        frame["Strat_DEAD"] = 0.0
        with self.assertRaises(ValueError) as ctx:
            self.engine.compute_live_correlation_matrix(frame)
        self.assertIn("Strat_DEAD", str(ctx.exception))

    def test_rejects_duplicate_strategy_names(self):
        frame = self.good.copy()
        frame.columns = ["Strat_A", "Strat_A"]
        with self.assertRaises(ValueError):
            self.engine.compute_live_correlation_matrix(frame)

    def test_rejects_non_numeric_column(self):
        frame = self.good.copy()
        frame["Strat_TEXT"] = ["x"] * len(frame)
        with self.assertRaises(TypeError):
            self.engine.compute_live_correlation_matrix(frame)

    def test_rejects_boolean_and_complex_columns(self):
        """A complex column passes a naive numeric check and loses its imaginary part on cast."""
        for label, column in (
            ("bool", [True, False] * 50),
            ("complex", np.arange(100, dtype=float) + 1j),
        ):
            with self.subTest(dtype=label):
                frame = self.good.copy()
                frame["Strat_ODD"] = column
                with self.assertRaises(TypeError):
                    self.engine.compute_live_correlation_matrix(frame)

    def test_accepts_pandas_nullable_extension_dtypes(self):
        """Float64/Int64 are what read_parquet and convert_dtypes hand back."""
        rng = np.random.default_rng(83)
        frame = pd.DataFrame({
            "Strat_A": pd.array(rng.normal(0.0, 0.01, 100), dtype="Float64"),
            "Strat_B": pd.array(rng.integers(1, 50, 100), dtype="Int64"),
        })
        report = self.engine.compute_live_correlation_matrix(frame)
        self.assertEqual(report.observations_used, 100)

        # pd.NA must be caught by the finiteness gate, not silently dropped.
        with_na = frame.copy()
        with_na.loc[7, "Strat_A"] = pd.NA
        with self.assertRaises(ValueError) as ctx:
            self.engine.compute_live_correlation_matrix(with_na)
        self.assertIn("Strat_A", str(ctx.exception))


class TestCorrelationEstimation(unittest.TestCase):
    def setUp(self):
        self.engine = StrategyCorrelationMatrixEngine(
            ewma_span=30,
            shrinkage_delta=0.15,
            high_correlation_threshold=0.70,
            max_avg_correlation_threshold=0.55,
            min_observations=30,
        )

    def test_matches_pandas_ewm_covariance_implementation(self):
        """Cross-check the weighting convention against pandas' independent implementation."""
        rng = np.random.default_rng(17)
        frame = pd.DataFrame(
            rng.normal(0.0, 0.01, (200, 4)), columns=["A", "B", "C", "D"]
        )
        report = self.engine.compute_live_correlation_matrix(frame)

        reference_cov = frame.ewm(span=self.engine.ewma_span).cov().iloc[-4:].to_numpy()
        reference_sd = np.sqrt(np.diag(reference_cov))
        reference_corr = reference_cov / np.outer(reference_sd, reference_sd)

        np.testing.assert_allclose(
            np.array(report.correlation_matrix), reference_corr, rtol=1e-10, atol=1e-12
        )

    def test_perfectly_scaled_series_give_exactly_one(self):
        rng = np.random.default_rng(5)
        base = rng.normal(0.0, 0.01, 120)
        frame = pd.DataFrame({"Strat_A": base, "Strat_B": 2.5 * base})
        report = self.engine.compute_live_correlation_matrix(frame)
        self.assertAlmostEqual(report.correlation_matrix[0][1], 1.0, places=12)
        self.assertEqual(len(report.high_correlation_pairs), 1)

    def test_perfectly_negated_series_give_exactly_minus_one_and_never_alert(self):
        rng = np.random.default_rng(6)
        base = rng.normal(0.0, 0.01, 120)
        frame = pd.DataFrame({"Strat_A": base, "Strat_B": -3.0 * base})
        report = self.engine.compute_live_correlation_matrix(frame)
        self.assertAlmostEqual(report.correlation_matrix[0][1], -1.0, places=12)
        # One-sided by design: a perfect hedge is diversification, not concentration risk.
        self.assertEqual(report.high_correlation_pairs, [])
        self.assertFalse(report.is_portfolio_diversification_compromised)

    def test_constructed_rho_is_recovered_exactly(self):
        for rho in (0.0, 0.35, 0.75, 0.95, -0.60):
            with self.subTest(rho=rho):
                frame = paired_series(rho, n_obs=150, span=self.engine.ewma_span)
                report = self.engine.compute_live_correlation_matrix(frame)
                self.assertAlmostEqual(report.correlation_matrix[0][1], rho, places=10)

    def test_matrix_is_symmetric_with_unit_diagonal(self):
        rng = np.random.default_rng(21)
        frame = pd.DataFrame(rng.normal(0.0, 0.01, (150, 5)), columns=list("ABCDE"))
        matrix = np.array(
            self.engine.compute_live_correlation_matrix(frame).correlation_matrix
        )
        np.testing.assert_array_equal(np.diag(matrix), np.ones(5))
        np.testing.assert_allclose(matrix, matrix.T, rtol=0, atol=0)
        self.assertTrue(np.all(np.abs(matrix) <= 1.0))

    def test_ewma_tracks_a_recent_regime_change(self):
        """A recent convergence must surface even when most of the history is uncorrelated."""
        rng = np.random.default_rng(9)
        quiet = rng.normal(0.0, 0.01, (400, 2))
        stressed_base = rng.normal(0.0, 0.01, 40)
        stressed = np.column_stack([stressed_base, stressed_base])  # rho = +1 on the tail
        frame = pd.DataFrame(np.vstack([quiet, stressed]), columns=["Strat_A", "Strat_B"])

        fast = StrategyCorrelationMatrixEngine(ewma_span=20, min_observations=30)
        slow = StrategyCorrelationMatrixEngine(ewma_span=2000, min_observations=30)

        fast_rho = fast.compute_live_correlation_matrix(frame).correlation_matrix[0][1]
        slow_rho = slow.compute_live_correlation_matrix(frame).correlation_matrix[0][1]

        self.assertGreater(fast_rho, 0.95)
        self.assertLess(slow_rho, 0.50)


class TestShrinkageSemantics(unittest.TestCase):
    def test_shrinkage_does_not_move_the_reported_correlations(self):
        """Regression for the alert-deflation defect.

        The shrunken covariance deflates every off-diagonal correlation by exactly
        (1 - delta). When the thresholds were applied to that deflated matrix, a pair at a
        true rho of 0.75 was reported as 0.525 at delta = 0.30 and no alert fired -- the
        engine silently under-reported the breakdown it exists to catch.
        """
        frame = paired_series(0.75, n_obs=150, span=30)
        engine = StrategyCorrelationMatrixEngine(
            ewma_span=30,
            shrinkage_delta=0.30,
            high_correlation_threshold=0.70,
            min_observations=30,
        )
        report = engine.compute_live_correlation_matrix(frame)

        self.assertAlmostEqual(report.correlation_matrix[0][1], 0.75, places=10)
        self.assertEqual(len(report.high_correlation_pairs), 1)
        self.assertAlmostEqual(report.high_correlation_pairs[0].correlation, 0.75, places=4)
        self.assertTrue(report.is_portfolio_diversification_compromised)

    def test_shrunk_covariance_keeps_variances_and_deflates_covariances(self):
        frame = paired_series(0.80, n_obs=150, span=30)
        delta = 0.25
        engine = StrategyCorrelationMatrixEngine(
            ewma_span=30, shrinkage_delta=delta, min_observations=30
        )
        report = engine.compute_live_correlation_matrix(frame)

        shrunk = np.array(report.shrunk_covariance_matrix)
        sd = np.sqrt(np.diag(shrunk))
        implied_rho = shrunk[0, 1] / (sd[0] * sd[1])

        # Diagonal untouched by a diagonal target; off-diagonal correlation scaled by (1-delta).
        self.assertAlmostEqual(implied_rho, (1.0 - delta) * 0.80, places=10)
        self.assertEqual(report.shrinkage_delta, delta)

    def test_shrinkage_makes_a_singular_covariance_invertible(self):
        """The well-conditioning claim: more strategies than observations."""
        rng = np.random.default_rng(31)
        frame = pd.DataFrame(rng.normal(0.0, 0.01, (4, 6)), columns=list("ABCDEF"))
        engine = StrategyCorrelationMatrixEngine(
            ewma_span=10, shrinkage_delta=0.15, min_observations=MIN_OBSERVATIONS_FLOOR
        )
        report = engine.compute_live_correlation_matrix(frame)

        shrunk = np.array(report.shrunk_covariance_matrix)
        self.assertGreater(np.linalg.eigvalsh(shrunk).min(), 0.0)
        # The unshrunk estimate over 4 observations of 6 strategies is rank-deficient.
        self.assertLess(np.linalg.matrix_rank(np.array(report.correlation_matrix)), 6)

    def test_zero_delta_leaves_the_covariance_unshrunk(self):
        frame = paired_series(0.60, n_obs=120, span=30)
        engine = StrategyCorrelationMatrixEngine(
            ewma_span=30, shrinkage_delta=0.0, min_observations=30
        )
        report = engine.compute_live_correlation_matrix(frame)
        shrunk = np.array(report.shrunk_covariance_matrix)
        sd = np.sqrt(np.diag(shrunk))
        self.assertAlmostEqual(shrunk[0, 1] / (sd[0] * sd[1]), 0.60, places=10)


class TestAlertingAndReport(unittest.TestCase):
    def test_threshold_is_inclusive_and_applied_unrounded(self):
        frame = paired_series(0.70, n_obs=150, span=30)
        engine = StrategyCorrelationMatrixEngine(
            ewma_span=30, high_correlation_threshold=0.70, min_observations=30
        )
        self.assertEqual(
            len(engine.compute_live_correlation_matrix(frame).high_correlation_pairs), 1
        )

        # Just under the threshold: the pre-rounding comparison must not promote 0.69996.
        near = paired_series(0.69996, n_obs=150, span=30)
        self.assertEqual(
            len(engine.compute_live_correlation_matrix(near).high_correlation_pairs), 0
        )

    def test_average_threshold_alone_compromises_the_portfolio(self):
        """No single pair breaches, but the book is uniformly correlated."""
        weights = ewma_weights(150, 30)
        u, v = weighted_orthonormal_pair(weights, seed=44)
        rho = 0.60
        frame = pd.DataFrame({
            "Strat_A": 0.01 * u,
            "Strat_B": 0.01 * (rho * u + math.sqrt(1 - rho ** 2) * v),
        })
        engine = StrategyCorrelationMatrixEngine(
            ewma_span=30,
            high_correlation_threshold=0.70,
            max_avg_correlation_threshold=0.55,
            min_observations=30,
        )
        report = engine.compute_live_correlation_matrix(frame)

        self.assertEqual(report.high_correlation_pairs, [])
        self.assertAlmostEqual(report.average_inter_strategy_correlation, 0.60, places=4)
        self.assertTrue(report.is_portfolio_diversification_compromised)

    def test_average_is_the_mean_of_unique_off_diagonal_pairs(self):
        rng = np.random.default_rng(53)
        frame = pd.DataFrame(rng.normal(0.0, 0.01, (200, 4)), columns=list("ABCD"))
        report = self.report = StrategyCorrelationMatrixEngine(
            ewma_span=30, min_observations=30
        ).compute_live_correlation_matrix(frame)

        matrix = np.array(report.correlation_matrix)
        expected = float(np.mean(matrix[np.triu_indices(4, k=1)]))
        self.assertAlmostEqual(report.average_inter_strategy_correlation, expected, places=4)

    def test_report_records_sample_size_and_effective_sample_size(self):
        rng = np.random.default_rng(61)
        frame = pd.DataFrame(rng.normal(0.0, 0.01, (5000, 2)), columns=["A", "B"])
        engine = StrategyCorrelationMatrixEngine(ewma_span=60, min_observations=30)
        report = engine.compute_live_correlation_matrix(frame, "2026-08-28T09:15:00Z")

        self.assertIsInstance(report, LiveCorrelationMatrixReport)
        self.assertEqual(report.observations_used, 5000)
        # Kish effective sample size of the EWMA weights converges to the span itself:
        # 5000 rows at span 60 is a 60-observation estimator, not a 5000-observation one.
        self.assertAlmostEqual(report.effective_observations, 60.0, places=3)
        self.assertEqual(report.timestamp_iso, "2026-08-28T09:15:00Z")
        self.assertIn("2026-08-28T09:15:00Z", report.audit_notes)

    def test_pair_records_identify_the_strategies_in_column_order(self):
        rng = np.random.default_rng(71)
        base = rng.normal(0.0, 0.01, 150)
        frame = pd.DataFrame({
            "Trend": base,
            "StatArb": rng.normal(0.0, 0.01, 150),
            "Momentum": 1.5 * base,
        })
        engine = StrategyCorrelationMatrixEngine(ewma_span=30, min_observations=30)
        pairs = engine.compute_live_correlation_matrix(frame).high_correlation_pairs

        self.assertEqual(len(pairs), 1)
        pair = pairs[0]
        self.assertIsInstance(pair, StrategyCorrelationPair)
        self.assertEqual((pair.strategy_a, pair.strategy_b), ("Trend", "Momentum"))
        self.assertTrue(pair.is_high_correlation_alert)


if __name__ == "__main__":
    unittest.main()
