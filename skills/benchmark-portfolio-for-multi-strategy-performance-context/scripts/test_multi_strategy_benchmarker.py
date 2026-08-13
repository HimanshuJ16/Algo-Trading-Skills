import logging
import math
import statistics
import unittest

import numpy as np

from multi_strategy_benchmarker import BenchmarkingError, MultiStrategyBenchmarker


class TestMultiStrategyBenchmarker(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        # Degenerate-input cases legitimately emit logger.warning; keep test output clean.
        logging.getLogger("multi_strategy_benchmarker").setLevel(logging.ERROR)

    def setUp(self):
        self.benchmarker = MultiStrategyBenchmarker(risk_free_rate_annual=0.04, periods_per_year=252)
        self.bench = np.array([0.01, -0.005, 0.02, -0.01, 0.015])

    # ------------------------------------------------------------------
    # Core decomposition
    # ------------------------------------------------------------------

    def test_pure_beta_replication(self):
        # Portfolio is exactly 2x the benchmark: pure leverage, no security selection.
        portfolio = self.bench * 2.0

        res = self.benchmarker.evaluate_performance(portfolio, self.bench)

        self.assertAlmostEqual(res["Beta"], 2.0, places=3)
        self.assertAlmostEqual(res["Correlation"], 1.0, places=3)
        # Jensen's alpha for UNFUNDED leverage is +Rf, not zero:
        #   alpha = (2*Rb - Rf) - 2*(Rb - Rf) = Rf = 0.04
        # A 2x leveraged clone only shows zero alpha once the borrowing cost of the
        # second unit of exposure is charged at Rf inside the portfolio returns.
        self.assertAlmostEqual(res["Annualized Alpha"], 0.04, places=3)
        self.assertGreater(res["Tracking Error (Ann)"], 0.0)

    def test_beta_and_alpha_against_independently_derived_values(self):
        # portfolio = 1.5 * benchmark + 2bps/day of constant selection return.
        # Analytically (independent of the benchmark data):
        #   Beta  = Cov(1.5b + c, b) / Var(b) = 1.5
        #   Alpha = (1.5*mb*252 + 252c - Rf) - 1.5*(mb*252 - Rf) = 252c + 0.5*Rf
        daily_selection = 0.0002
        portfolio = self.bench * 1.5 + daily_selection

        res = self.benchmarker.evaluate_performance(portfolio, self.bench)

        self.assertAlmostEqual(res["Beta"], 1.5, places=6)
        expected_alpha = 252 * daily_selection + 0.5 * 0.04
        self.assertAlmostEqual(res["Annualized Alpha"], round(expected_alpha, 4), places=4)

        # Active return series is 0.5*b + c, so TE = 0.5 * stdev(b) * sqrt(252).
        # stdev computed via the stdlib, i.e. a different code path from the implementation.
        expected_te = 0.5 * statistics.stdev(self.bench.tolist()) * math.sqrt(252)
        self.assertAlmostEqual(res["Tracking Error (Ann)"], round(expected_te, 4), places=4)

    def test_t_stat_matches_direct_computation(self):
        portfolio = self.bench * 1.5 + 0.0002
        res = self.benchmarker.evaluate_performance(portfolio, self.bench)

        active = (portfolio - self.bench).tolist()
        # t = mean / (sd / sqrt(n)), computed directly from the stdlib.
        expected_t = statistics.mean(active) / (statistics.stdev(active) / math.sqrt(len(active)))
        self.assertAlmostEqual(res["Active Return t-stat"], round(expected_t, 4), places=4)
        self.assertEqual(res["Observations"], 5)
        self.assertAlmostEqual(res["Years"], round(5 / 252, 4), places=4)

    def test_pure_alpha_generation(self):
        # Flat (cash-like) benchmark, portfolio earns a steady 10bps a day.
        benchmark = np.zeros(5)
        portfolio = np.full(5, 0.001)

        res = self.benchmarker.evaluate_performance(portfolio, benchmark)

        # Beta is not identified against a constant benchmark; reported 0.0 BY CONVENTION,
        # and the convention must be surfaced rather than hidden.
        self.assertEqual(res["Beta"], 0.0)
        self.assertTrue(any("not identified" in w for w in res["Warnings"]))
        # Correlation with a constant series is undefined, not zero.
        self.assertTrue(math.isnan(res["Correlation"]))
        # alpha = 0.001*252 - 0.04 = 0.212
        self.assertAlmostEqual(res["Annualized Alpha"], 0.212, places=4)

    # ------------------------------------------------------------------
    # Regression tests for previously wrong behaviour
    # ------------------------------------------------------------------

    def test_zero_tracking_error_reports_undefined_ir_not_zero(self):
        # Regression: a portfolio that beats the benchmark by exactly 10bps EVERY day is
        # the most consistent active manager possible. Reporting IR = 0.0 (the old
        # behaviour) made it indistinguishable from a manager with no skill at all.
        portfolio = self.bench + 0.001

        res = self.benchmarker.evaluate_performance(portfolio, self.bench)

        self.assertEqual(res["Tracking Error (Ann)"], 0.0)
        self.assertTrue(math.isnan(res["Information Ratio"]))
        self.assertTrue(math.isnan(res["Active Return t-stat"]))
        self.assertAlmostEqual(res["Active Return (Ann)"], 0.252, places=4)
        self.assertTrue(any("Information Ratio is undefined" in w for w in res["Warnings"]))

    def test_low_volatility_benchmark_is_not_treated_as_constant(self):
        # Regression: the old `bench_var > 1e-8` guard misclassified a genuine low-volatility
        # benchmark (daily sigma ~1bp, variance ~1e-10) as constant, returning Beta = 0.0
        # alongside Correlation = 1.0 -- internally contradictory, and Alpha was wrong.
        low_vol = np.array([0.00005, 0.00004, 0.00006, 0.00005, 0.00003])
        portfolio = low_vol * 3.0

        res = self.benchmarker.evaluate_performance(portfolio, low_vol)

        self.assertAlmostEqual(res["Beta"], 3.0, places=4)
        self.assertAlmostEqual(res["Correlation"], 1.0, places=4)
        # alpha = (3*Rb - Rf) - 3*(Rb - Rf) = 2*Rf = 0.08
        self.assertAlmostEqual(res["Annualized Alpha"], 0.08, places=4)
        self.assertFalse(any("Beta is not identified" in w for w in res["Warnings"]))

    def test_insufficient_observations_raises_instead_of_returning_empty(self):
        # Regression: previously returned {}, so `res["Beta"]` raised an opaque KeyError
        # far from the real cause.
        with self.assertRaises(BenchmarkingError):
            self.benchmarker.evaluate_performance(np.array([0.01]), np.array([0.01]))
        with self.assertRaises(BenchmarkingError):
            self.benchmarker.evaluate_performance(np.array([]), np.array([]))

    def test_non_finite_input_is_rejected(self):
        # Regression: NaN used to propagate silently, yielding a dict of NaNs mixed with
        # spurious 0.0 values that looked like real metrics.
        with_nan = self.bench.copy()
        with_nan[2] = np.nan
        with self.assertRaises(BenchmarkingError):
            self.benchmarker.evaluate_performance(with_nan, self.bench)

        with_inf = self.bench.copy()
        with_inf[0] = np.inf
        with self.assertRaises(BenchmarkingError):
            self.benchmarker.evaluate_performance(self.bench, with_inf)

    # ------------------------------------------------------------------
    # Input validation
    # ------------------------------------------------------------------

    def test_length_mismatch_raises(self):
        with self.assertRaises(BenchmarkingError):
            self.benchmarker.evaluate_performance(self.bench, self.bench[:-1])

    def test_two_dimensional_input_is_rejected(self):
        two_d = np.tile(self.bench, (2, 1))
        with self.assertRaises(BenchmarkingError):
            self.benchmarker.evaluate_performance(two_d, two_d)

    def test_non_numeric_input_is_rejected(self):
        with self.assertRaises(BenchmarkingError):
            self.benchmarker.evaluate_performance(["a", "b", "c"], [0.01, 0.02, 0.03])

    def test_plain_lists_are_accepted(self):
        res = self.benchmarker.evaluate_performance(
            (self.bench * 2.0).tolist(), self.bench.tolist()
        )
        self.assertAlmostEqual(res["Beta"], 2.0, places=3)

    def test_invalid_constructor_arguments_rejected(self):
        with self.assertRaises(BenchmarkingError):
            MultiStrategyBenchmarker(periods_per_year=0)
        with self.assertRaises(BenchmarkingError):
            MultiStrategyBenchmarker(periods_per_year=-252)
        with self.assertRaises(BenchmarkingError):
            MultiStrategyBenchmarker(risk_free_rate_annual=float("nan"))
        # A non-integral frequency would silently truncate (252.9 -> 252) and
        # mis-annualize every metric.
        with self.assertRaises(BenchmarkingError):
            MultiStrategyBenchmarker(periods_per_year=252.9)
        # An integral float is an unambiguous frequency and is accepted.
        self.assertEqual(MultiStrategyBenchmarker(periods_per_year=12.0).ppy, 12)

    def test_identical_series_reports_replication_not_outperformance(self):
        res = self.benchmarker.evaluate_performance(self.bench, self.bench)

        self.assertAlmostEqual(res["Beta"], 1.0, places=6)
        self.assertEqual(res["Annualized Alpha"], 0.0)
        self.assertEqual(res["Active Return (Ann)"], 0.0)
        self.assertTrue(math.isnan(res["Information Ratio"]))
        # The 0/0 case must not be described as "deterministic outperformance".
        self.assertTrue(any("replicates the benchmark exactly" in w for w in res["Warnings"]))

    def test_short_sample_is_flagged(self):
        res = self.benchmarker.evaluate_performance(self.bench * 1.2, self.bench)
        self.assertTrue(any("year(s)" in w for w in res["Warnings"]))

    def test_full_year_sample_is_not_flagged_as_short(self):
        rng = np.random.default_rng(7)
        bench = rng.normal(0.0004, 0.01, 252)
        port = bench * 1.1 + rng.normal(0.0001, 0.002, 252)
        res = self.benchmarker.evaluate_performance(port, bench)
        self.assertFalse(any("year(s)" in w for w in res["Warnings"]))
        self.assertEqual(res["Observations"], 252)
        self.assertEqual(res["Warnings"], [])


if __name__ == '__main__':
    unittest.main()
