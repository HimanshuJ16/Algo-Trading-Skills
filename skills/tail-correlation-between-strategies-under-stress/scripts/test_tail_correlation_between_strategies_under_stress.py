"""Unit tests for tail-correlation-between-strategies-under-stress.

Expected values for the quantitative tests are derived independently of the
implementation: the joint-tail index set is worked out by hand from the
construction of the fixture, and the expected Pearson correlation is recomputed
with a pure-Python routine (``_reference_pearson``) rather than by calling the
same numpy helpers the module uses.

Two tests are explicit regressions against defects in v1.0.0:

* ``test_gaussian_pair_is_not_reported_as_negatively_tail_correlated`` -- v1.0.0
  conditioned on the UNION of the two lower tails, an L-shaped selection region
  that manufactures strong negative correlation. On a bivariate normal with
  rho=0.6 it reported a tail correlation near -0.19 and a delta near -0.79.
* ``test_thin_joint_tail_is_indeterminate_not_reassuring`` -- v1.0.0 silently
  substituted the unconditional correlation when the tail subsample was too
  small, forcing the delta to zero and printing "Tail diversification holds".
"""
import logging
import math
import unittest

import numpy as np
import pandas as pd

from tail_correlation_between_strategies_under_stress import (
    MIN_OBSERVATIONS_FLOOR,
    MIN_TAIL_OBSERVATIONS_FLOOR,
    Config,
    Engine,
    TailCorrelationAnalyzerEngine,
    TailCorrelationError,
    empirical_tail_dependence,
    exceedance_correlation,
)

# The module logs warnings for indeterminate pairs and dropped rows by design;
# keep the test output readable.
logging.getLogger(
    "tail_correlation_between_strategies_under_stress"
).setLevel(logging.CRITICAL)

# Fixture used by the analytic tests. x is strictly increasing, so with
# alpha=0.25 and n=20 the linear-interpolation quantile is 4.75 and the lower
# tail of x is exactly indices 0-4. y is a permutation of 0..19 whose five
# smallest values (2, 0, 4, 1, 3) also sit at indices 0-4, so the JOINT lower
# tail is exactly indices 0-4.
ANALYTIC_X = [float(v) for v in range(20)]
ANALYTIC_Y = [
    2.0, 0.0, 4.0, 1.0, 3.0,
    19.0, 18.0, 17.0, 16.0, 15.0,
    14.0, 13.0, 12.0, 11.0, 10.0,
    9.0, 8.0, 7.0, 6.0, 5.0,
]
ANALYTIC_JOINT_TAIL_INDICES = [0, 1, 2, 3, 4]


def _reference_pearson(xs, ys):
    """Pearson correlation via the algebraic definition, in pure Python."""
    n = len(xs)
    mx = math.fsum(xs) / n
    my = math.fsum(ys) / n
    cov = math.fsum((x - mx) * (y - my) for x, y in zip(xs, ys))
    vx = math.fsum((x - mx) ** 2 for x in xs)
    vy = math.fsum((y - my) ** 2 for y in ys)
    return cov / math.sqrt(vx * vy)


def _clayton_uniforms(n, theta, rng):
    """Clayton copula sample: lower tail dependence lambda_L = 2 ** (-1/theta).

    Marshall-Olkin mixture representation; needs only numpy.
    """
    v = rng.gamma(1.0 / theta, 1.0, n)
    e = rng.exponential(1.0, (n, 2))
    return (1.0 + e / v[:, None]) ** (-1.0 / theta)


class TestAnalyticCorrectness(unittest.TestCase):
    """Exact values on a hand-constructed fixture."""

    def test_exceedance_correlation_matches_hand_derived_value(self):
        x = np.array(ANALYTIC_X)
        y = np.array(ANALYTIC_Y)

        corr, count = exceedance_correlation(x, y, tail_quantile=0.25, min_tail_observations=5)

        self.assertEqual(count, len(ANALYTIC_JOINT_TAIL_INDICES))
        expected = _reference_pearson(
            [ANALYTIC_X[i] for i in ANALYTIC_JOINT_TAIL_INDICES],
            [ANALYTIC_Y[i] for i in ANALYTIC_JOINT_TAIL_INDICES],
        )
        # Worked by hand: dx = [-2,-1,0,1,2], dy = [0,-2,2,-1,1],
        # cov = 3, varx = vary = 10  =>  rho = 0.30.
        self.assertAlmostEqual(expected, 0.30, places=12)
        self.assertAlmostEqual(corr, 0.30, places=12)

    def test_exceedance_correlation_is_symmetric(self):
        x = np.array(ANALYTIC_X)
        y = np.array(ANALYTIC_Y)
        forward, n_fwd = exceedance_correlation(x, y, 0.25, 5)
        reverse, n_rev = exceedance_correlation(y, x, 0.25, 5)
        self.assertEqual(n_fwd, n_rev)
        self.assertAlmostEqual(forward, reverse, places=12)

    def test_exceedance_correlation_nan_when_joint_tail_too_small(self):
        x = np.array(ANALYTIC_X)
        y = np.array(ANALYTIC_Y)
        corr, count = exceedance_correlation(x, y, 0.25, min_tail_observations=6)
        self.assertEqual(count, 5)
        self.assertTrue(math.isnan(corr))

    def test_exceedance_correlation_nan_on_flat_tail_slice(self):
        # Five identical worst observations: the tail slice has zero variance,
        # so its correlation is undefined and must not be reported as 1.0.
        x = np.array([-1.0] * 5 + [float(v) for v in range(1, 16)])
        y = np.array([-1.0] * 5 + [float(v) for v in range(1, 16)])
        corr, count = exceedance_correlation(x, y, 0.25, 5)
        self.assertEqual(count, 5)
        self.assertTrue(math.isnan(corr))

    def test_empirical_tail_dependence_exact_values(self):
        x = np.array(ANALYTIC_X)
        y = np.array(ANALYTIC_Y)
        # Both lower tails are indices 0-4, so every x-tail day is a y-tail day.
        self.assertAlmostEqual(empirical_tail_dependence(x, y, 0.25), 1.0, places=12)

        # Countermonotone: the lower tail of x maps onto the upper tail of y.
        y_rev = np.array(list(reversed(ANALYTIC_X)))
        self.assertAlmostEqual(empirical_tail_dependence(x, y_rev, 0.25), 0.0, places=12)

    def test_empirical_tail_dependence_independence_baseline_is_alpha(self):
        # Under independence chi_hat(alpha) estimates alpha, not zero. This is
        # the calibration point that makes "lambda >= 0.50 is severe" meaningful.
        rng = np.random.default_rng(11)
        n, alpha = 4000, 0.10
        vals = [
            empirical_tail_dependence(
                rng.standard_normal(n), rng.standard_normal(n), alpha
            )
            for _ in range(60)
        ]
        self.assertAlmostEqual(float(np.mean(vals)), alpha, delta=0.02)


class TestRegressions(unittest.TestCase):
    """Explicit regressions against v1.0.0 defects."""

    def setUp(self):
        self.analyzer = TailCorrelationAnalyzerEngine(
            Config(benchmark_simulations=400)
        )

    def test_gaussian_pair_is_not_reported_as_negatively_tail_correlated(self):
        rng = np.random.default_rng(3)
        z = rng.multivariate_normal([0.0, 0.0], [[1.0, 0.6], [0.6, 1.0]], 1500)
        res = self.analyzer.analyze_pair(
            pd.Series(z[:, 0]), pd.Series(z[:, 1]), "Alpha", "Beta"
        )

        self.assertTrue(res.is_determinate)
        # v1.0.0's union mask produced roughly -0.19 here.
        self.assertGreater(res.lower_tail_correlation, 0.0)
        # A Gaussian copula is the null: excess over the benchmark is ~0 and the
        # pair must not be flagged however high its unconditional correlation.
        self.assertLess(abs(res.tail_correlation_excess), 0.25)
        self.assertFalse(res.diversification_breakdown)

    def test_thin_joint_tail_is_indeterminate_not_reassuring(self):
        # n=30 at alpha=0.10 leaves ~0.10^2 * 30 < 1 points in the joint tail.
        rng = np.random.default_rng(5)
        a = pd.Series(rng.standard_normal(30))
        b = pd.Series(rng.standard_normal(30))
        res = self.analyzer.analyze_pair(a, b, "Thin_A", "Thin_B")

        self.assertFalse(res.is_determinate)
        self.assertTrue(math.isnan(res.lower_tail_correlation))
        self.assertFalse(res.diversification_breakdown)
        self.assertIn("INDETERMINATE", res.details[0])
        self.assertIn("joint lower", res.details[0])
        # v1.0.0 asserted diversification here on no tail evidence at all.
        self.assertNotIn("Tail diversification holds", " ".join(res.details))

    def test_indeterminate_message_names_the_actual_cause(self):
        # n=20 at alpha=0.25 clears min_tail_observations=5 in the sample but the
        # Gaussian null cannot populate a 5-point joint tail, so the blocker is
        # the benchmark. The message must say so rather than blaming the sample.
        analyzer = TailCorrelationAnalyzerEngine(
            Config(tail_quantile=0.25, min_tail_observations=5, benchmark_simulations=300)
        )
        res = analyzer.analyze_pair(
            pd.Series(ANALYTIC_X), pd.Series(ANALYTIC_Y), "F1", "F2"
        )

        self.assertFalse(res.is_determinate)
        self.assertEqual(res.joint_tail_observations, 5)
        # The observed statistic itself was computable and is still reported.
        self.assertAlmostEqual(res.lower_tail_correlation, 0.30, places=12)
        self.assertTrue(math.isnan(res.gaussian_benchmark_correlation))
        self.assertIn("Gaussian benchmark could not be estimated", res.details[0])
        self.assertNotIn("minimum 5", res.details[0])
        self.assertFalse(res.diversification_breakdown)

    def test_flat_joint_tail_is_reported_as_flat(self):
        rng = np.random.default_rng(71)
        base = rng.standard_normal(400)
        a, b = base.copy(), base.copy()
        # Identical constant crash block: the joint tail slice has zero variance.
        a[:60] = -5.0
        b[:60] = -5.0
        analyzer = TailCorrelationAnalyzerEngine(Config(benchmark_simulations=200))
        res = analyzer.analyze_pair(pd.Series(a), pd.Series(b), "Flat_A", "Flat_B")

        self.assertFalse(res.is_determinate)
        self.assertTrue(math.isnan(res.lower_tail_correlation))
        self.assertIn("flat", res.details[0])
        self.assertFalse(res.diversification_breakdown)


class TestBreakdownDetection(unittest.TestCase):

    def setUp(self):
        self.analyzer = TailCorrelationAnalyzerEngine(
            Config(benchmark_simulations=400)
        )

    def test_clayton_lower_tail_dependence_is_flagged(self):
        # theta=2 gives lambda_L = 2**(-0.5) ~ 0.71: genuine lower-tail coupling
        # that no correlation number explains.
        rng = np.random.default_rng(17)
        u = _clayton_uniforms(2000, 2.0, rng)
        res = self.analyzer.analyze_pair(
            pd.Series(u[:, 0]), pd.Series(u[:, 1]), "TailDep_A", "TailDep_B"
        )

        self.assertTrue(res.is_determinate)
        self.assertTrue(res.diversification_breakdown)
        self.assertIn("DIVERSIFICATION BREAKDOWN WARNING", res.details[0])
        self.assertGreater(res.tail_correlation_excess, 0.0)
        self.assertLessEqual(res.benchmark_pvalue, 0.05)
        self.assertGreater(res.empirical_tail_dependence, 0.50)

    def test_independent_pair_is_not_flagged(self):
        rng = np.random.default_rng(23)
        res = self.analyzer.analyze_pair(
            pd.Series(rng.standard_normal(2000)),
            pd.Series(rng.standard_normal(2000)),
            "Ind_A",
            "Ind_B",
        )
        self.assertTrue(res.is_determinate)
        self.assertFalse(res.diversification_breakdown)
        self.assertIn("Tail diversification holds", res.details[0])

    def test_detection_is_one_sided(self):
        # Strong NEGATIVE joint-tail comovement is a diversification benefit and
        # must never raise a breakdown warning.
        rng = np.random.default_rng(29)
        u = _clayton_uniforms(2000, 2.0, rng)
        res = self.analyzer.analyze_pair(
            pd.Series(u[:, 0]), pd.Series(-u[:, 1]), "Neg_A", "Neg_B"
        )
        self.assertFalse(res.diversification_breakdown)

    def test_results_are_deterministic(self):
        rng = np.random.default_rng(31)
        u = _clayton_uniforms(1200, 1.5, rng)
        a, b = pd.Series(u[:, 0]), pd.Series(u[:, 1])
        first = self.analyzer.analyze_pair(a, b, "D_A", "D_B")
        second = self.analyzer.analyze_pair(a, b, "D_A", "D_B")
        self.assertEqual(
            first.gaussian_benchmark_correlation, second.gaussian_benchmark_correlation
        )
        self.assertEqual(first.benchmark_pvalue, second.benchmark_pvalue)
        self.assertEqual(first.lower_tail_correlation, second.lower_tail_correlation)

    def test_reported_counts_are_consistent(self):
        rng = np.random.default_rng(37)
        u = _clayton_uniforms(1000, 1.5, rng)
        res = self.analyzer.analyze_pair(
            pd.Series(u[:, 0]), pd.Series(u[:, 1]), "C_A", "C_B"
        )
        self.assertEqual(res.observations_used, 1000)
        self.assertAlmostEqual(
            res.joint_crash_probability,
            res.joint_tail_observations / res.observations_used,
            places=12,
        )
        self.assertEqual(res.independence_tail_dependence, 0.10)


class TestInputValidation(unittest.TestCase):

    def setUp(self):
        self.analyzer = TailCorrelationAnalyzerEngine(Config(benchmark_simulations=200))
        rng = np.random.default_rng(41)
        self.a = pd.Series(rng.standard_normal(100))
        self.b = pd.Series(rng.standard_normal(100))

    def test_rejects_infinite_values(self):
        bad = self.a.copy()
        bad.iloc[10] = np.inf
        with self.assertRaises(TailCorrelationError):
            self.analyzer.analyze_pair(bad, self.b, "A", "B")

    def test_rejects_non_numeric_values(self):
        bad = self.a.astype(object).copy()
        bad.iloc[3] = "flat"
        with self.assertRaises(TailCorrelationError):
            self.analyzer.analyze_pair(bad, self.b, "A", "B")

    def test_rejects_duplicate_index_labels(self):
        idx = list(range(99)) + [98]
        bad = pd.Series(self.a.to_numpy(), index=idx)
        with self.assertRaises(TailCorrelationError):
            self.analyzer.analyze_pair(bad, self.b, "A", "B")

    def test_rejects_zero_variance_series(self):
        flat = pd.Series(np.zeros(100))
        with self.assertRaises(TailCorrelationError):
            self.analyzer.analyze_pair(flat, self.b, "Stale", "B")

    def test_rejects_insufficient_overlap(self):
        with self.assertRaises(TailCorrelationError):
            self.analyzer.analyze_pair(self.a.iloc[:15], self.b.iloc[:15], "A", "B")

    def test_rejects_identical_strategy_names(self):
        with self.assertRaises(TailCorrelationError):
            self.analyzer.analyze_pair(self.a, self.b, "Same", "Same")

    def test_rejects_non_series_input(self):
        with self.assertRaises(TailCorrelationError):
            self.analyzer.analyze_pair(self.a.to_numpy(), self.b, "A", "B")

    def test_config_rejects_out_of_range_tail_quantile(self):
        for bad in (0.0, -0.1, 0.5, 1.5):
            with self.subTest(tail_quantile=bad):
                with self.assertRaises(TailCorrelationError):
                    Config(tail_quantile=bad)

    def test_config_enforces_observation_floors(self):
        with self.assertRaises(TailCorrelationError):
            Config(min_observations=MIN_OBSERVATIONS_FLOOR - 1)
        with self.assertRaises(TailCorrelationError):
            Config(min_tail_observations=MIN_TAIL_OBSERVATIONS_FLOOR - 1)
        with self.assertRaises(TailCorrelationError):
            Config(benchmark_simulations=50)
        with self.assertRaises(TailCorrelationError):
            Config(breakdown_max_pvalue=0.0)


class TestIndexAlignment(unittest.TestCase):

    def setUp(self):
        self.analyzer = TailCorrelationAnalyzerEngine(Config(benchmark_simulations=200))

    def test_uses_only_the_overlapping_dates(self):
        rng = np.random.default_rng(43)
        idx_a = pd.date_range("2024-01-01", periods=120, freq="D")
        idx_b = pd.date_range("2024-02-01", periods=120, freq="D")
        a = pd.Series(rng.standard_normal(120), index=idx_a)
        b = pd.Series(rng.standard_normal(120), index=idx_b)

        res = self.analyzer.analyze_pair(a, b, "A", "B")
        expected_overlap = len(idx_a.intersection(idx_b))
        self.assertEqual(res.observations_used, expected_overlap)

    def test_disjoint_indices_raise_rather_than_silently_pass(self):
        rng = np.random.default_rng(47)
        a = pd.Series(rng.standard_normal(60), index=pd.date_range("2024-01-01", periods=60))
        b = pd.Series(rng.standard_normal(60), index=pd.date_range("2030-01-01", periods=60))
        with self.assertRaises(TailCorrelationError):
            self.analyzer.analyze_pair(a, b, "A", "B")


class TestPortfolioMatrix(unittest.TestCase):

    def setUp(self):
        self.analyzer = TailCorrelationAnalyzerEngine(Config(benchmark_simulations=200))

    def test_matrix_shape_symmetry_and_results(self):
        rng = np.random.default_rng(53)
        df = pd.DataFrame(
            {
                "S1": rng.standard_normal(600),
                "S2": rng.standard_normal(600),
                "S3": rng.standard_normal(600),
            }
        )
        out = self.analyzer.analyze_portfolio_matrix(df)

        uncond = out["unconditional_matrix"]
        tail = out["lower_tail_matrix"]
        self.assertEqual(uncond.shape, (3, 3))
        self.assertEqual(tail.shape, (3, 3))
        np.testing.assert_allclose(np.diag(uncond.to_numpy()), np.ones(3))
        np.testing.assert_allclose(
            uncond.to_numpy(), uncond.to_numpy().T, rtol=0, atol=1e-12
        )
        self.assertEqual(len(out["results"]), 3)

    def test_indeterminate_pairs_are_reported_separately(self):
        rng = np.random.default_rng(59)
        # 40 rows at alpha=0.10 cannot populate a 10-point joint tail.
        df = pd.DataFrame(
            {"S1": rng.standard_normal(40), "S2": rng.standard_normal(40)}
        )
        out = self.analyzer.analyze_portfolio_matrix(df)

        self.assertEqual(len(out["indeterminate_pairs"]), 1)
        self.assertEqual(out["breakdown_pairs"], [])
        self.assertTrue(math.isnan(out["lower_tail_matrix"].loc["S1", "S2"]))

    def test_matrix_rejects_malformed_frames(self):
        rng = np.random.default_rng(61)
        with self.assertRaises(TailCorrelationError):
            self.analyzer.analyze_portfolio_matrix(
                pd.DataFrame({"only": rng.standard_normal(100)})
            )
        with self.assertRaises(TailCorrelationError):
            self.analyzer.analyze_portfolio_matrix({"S1": [1, 2, 3]})

        dupe = pd.DataFrame(rng.standard_normal((100, 2)), columns=["S1", "S1"])
        with self.assertRaises(TailCorrelationError):
            self.analyzer.analyze_portfolio_matrix(dupe)


class TestLegacyEngine(unittest.TestCase):

    def test_legacy_engine(self):
        eng = Engine(Config())
        self.assertTrue(eng.config.enabled)
        self.assertTrue(eng.run())
        self.assertIsInstance(eng.analyzer, TailCorrelationAnalyzerEngine)


if __name__ == "__main__":
    unittest.main()
