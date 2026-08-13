import math
import statistics
import unittest

import numpy as np

from benchmark_selector import BenchmarkMetrics, BenchmarkSelector


def independent_tracking_error(strategy, benchmark, periods_per_year=252):
    """
    Tracking error computed with the stdlib, independent of the implementation's
    numpy path: annualised sample standard deviation of active returns.
    """
    active = [s - b for s, b in zip(strategy, benchmark)]
    return statistics.stdev(active) * math.sqrt(periods_per_year)


class TestBenchmarkSelector(unittest.TestCase):
    def setUp(self):
        # 10 days of data
        self.strat_returns = [0.01, -0.005, 0.02, 0.001, -0.01, 0.015, -0.002, 0.008, 0.004, -0.001]

        # SPY is highly correlated
        self.spy_returns = [0.009, -0.004, 0.018, 0.002, -0.009, 0.012, -0.001, 0.007, 0.003, 0.000]

        # TLT is negatively correlated
        self.tlt_returns = [-0.005, 0.01, -0.01, -0.002, 0.015, -0.008, 0.005, -0.003, -0.002, 0.004]

        # Risk-free is flat
        self.rf_returns = [0.0001] * 10

        self.benchmarks = {
            "SPY": self.spy_returns,
            "TLT": self.tlt_returns,
            "RF": self.rf_returns
        }
        self.selector = BenchmarkSelector(self.benchmarks)

    def test_evaluate_benchmarks(self):
        metrics = self.selector.evaluate_benchmarks(self.strat_returns)
        self.assertEqual(len(metrics), 3)

        # Ranked by ascending tracking error: closest-replicating benchmark first.
        self.assertEqual(metrics[0].name, "SPY")
        self.assertTrue(metrics[0].correlation > 0.9)

        self.assertEqual(metrics[-1].name, "TLT")
        self.assertTrue(metrics[-1].correlation < 0.0)

    def test_recommend_benchmark(self):
        recommendation = self.selector.recommend_benchmark(self.strat_returns)
        self.assertEqual(recommendation, "SPY")

    def test_ranking_is_by_ascending_tracking_error(self):
        # Includes a levered clone so that correlation-order and tracking-error
        # order genuinely disagree: ranking by correlation would put LEV3X first.
        levered = [3.0 * r for r in self.strat_returns]
        selector = BenchmarkSelector(dict(self.benchmarks, LEV3X=levered))

        metrics = selector.evaluate_benchmarks(self.strat_returns)
        errors = [m.tracking_error for m in metrics]
        self.assertEqual(errors, sorted(errors))
        self.assertEqual(metrics[0].name, "SPY")

    # --- The defect that motivated the ranking change -------------------------

    def test_leveraged_clone_is_not_recommended(self):
        # A 3x-levered copy of the strategy has correlation exactly 1.0, so a
        # correlation-ranked selector picks it — despite it being a far worse
        # benchmark than one that actually matches the strategy's scale.
        levered = [3.0 * r for r in self.strat_returns]
        selector = BenchmarkSelector({"LEV3X": levered, "MATCH": self.spy_returns})

        metrics = {m.name: m for m in selector.evaluate_benchmarks(self.strat_returns)}
        self.assertAlmostEqual(metrics["LEV3X"].correlation, 1.0, places=12)
        self.assertGreater(metrics["LEV3X"].tracking_error, metrics["MATCH"].tracking_error)

        self.assertEqual(selector.recommend_benchmark(self.strat_returns), "MATCH")

    def test_beta_exposes_scale_mismatch_correlation_hides(self):
        # Regressing the strategy on a 3x clone gives beta = 1/3 exactly, while
        # correlation is a perfect 1.0. Beta is what reveals the mismatch.
        levered = [3.0 * r for r in self.strat_returns]
        selector = BenchmarkSelector({"LEV3X": levered})
        metric = selector.evaluate_benchmarks(self.strat_returns)[0]

        self.assertAlmostEqual(metric.beta, 1.0 / 3.0, places=12)
        self.assertAlmostEqual(metric.correlation, 1.0, places=12)
        self.assertAlmostEqual(metric.r_squared, 1.0, places=12)

    # --- Quantitative correctness ---------------------------------------------

    def test_tracking_error_matches_independent_calculation(self):
        metric = {m.name: m for m in self.selector.evaluate_benchmarks(self.strat_returns)}["SPY"]
        expected = independent_tracking_error(self.strat_returns, self.spy_returns)
        self.assertAlmostEqual(metric.tracking_error, expected, places=12)

    def test_information_ratio_matches_independent_calculation(self):
        metric = {m.name: m for m in self.selector.evaluate_benchmarks(self.strat_returns)}["SPY"]
        active = [s - b for s, b in zip(self.strat_returns, self.spy_returns)]
        expected_active = statistics.fmean(active) * 252
        expected_ir = expected_active / independent_tracking_error(self.strat_returns, self.spy_returns)

        self.assertAlmostEqual(metric.annualized_active_return, expected_active, places=12)
        self.assertAlmostEqual(metric.information_ratio, expected_ir, places=12)

    def test_uses_sample_standard_deviation_by_default(self):
        # ddof=1 (sample) vs ddof=0 (population) differ by sqrt(n / (n-1)).
        sample = BenchmarkSelector({"SPY": self.spy_returns}, ddof=1)
        population = BenchmarkSelector({"SPY": self.spy_returns}, ddof=0)

        te_sample = sample.evaluate_benchmarks(self.strat_returns)[0].tracking_error
        te_population = population.evaluate_benchmarks(self.strat_returns)[0].tracking_error

        n = len(self.strat_returns)
        self.assertAlmostEqual(te_sample / te_population, math.sqrt(n / (n - 1)), places=12)

    def test_periods_per_year_scales_tracking_error(self):
        daily = BenchmarkSelector({"SPY": self.spy_returns}, periods_per_year=252)
        monthly = BenchmarkSelector({"SPY": self.spy_returns}, periods_per_year=12)

        te_daily = daily.evaluate_benchmarks(self.strat_returns)[0].tracking_error
        te_monthly = monthly.evaluate_benchmarks(self.strat_returns)[0].tracking_error

        self.assertAlmostEqual(te_daily / te_monthly, math.sqrt(252 / 12), places=12)

    def test_r_squared_is_correlation_squared(self):
        for metric in self.selector.evaluate_benchmarks(self.strat_returns):
            if math.isnan(metric.correlation):
                self.assertTrue(math.isnan(metric.r_squared))
            else:
                self.assertAlmostEqual(metric.r_squared, metric.correlation ** 2, places=12)

    # --- Undefined / degenerate cases -----------------------------------------

    def test_flat_benchmark_correlation_is_nan_not_zero(self):
        # Correlation against a zero-variance series is undefined. Reporting 0.0
        # fabricates a number and makes the benchmark look uncorrelated by design.
        metric = {m.name: m for m in self.selector.evaluate_benchmarks(self.strat_returns)}["RF"]
        self.assertTrue(math.isnan(metric.correlation))
        self.assertTrue(math.isnan(metric.beta))
        self.assertTrue(math.isnan(metric.r_squared))
        # Tracking error against a flat series is still well defined.
        self.assertGreater(metric.tracking_error, 0.0)

    def test_constant_outperformance_gives_infinite_ir(self):
        # Strategy beats the benchmark by exactly 10bps every period: zero active
        # risk, positive active return. IR is unbounded, not zero and not 2.7e16.
        benchmark = [r - 0.001 for r in self.strat_returns]
        metric = BenchmarkSelector({"CONST": benchmark}).evaluate_benchmarks(self.strat_returns)[0]

        self.assertLess(metric.tracking_error, 1e-12)
        self.assertEqual(metric.information_ratio, math.inf)

    def test_constant_underperformance_gives_negative_infinite_ir(self):
        benchmark = [r + 0.001 for r in self.strat_returns]
        metric = BenchmarkSelector({"CONST": benchmark}).evaluate_benchmarks(self.strat_returns)[0]
        self.assertEqual(metric.information_ratio, -math.inf)

    def test_identical_series_gives_zero_ir(self):
        metric = BenchmarkSelector(
            {"SELF": self.strat_returns}
        ).evaluate_benchmarks(self.strat_returns)[0]

        self.assertAlmostEqual(metric.tracking_error, 0.0, places=12)
        self.assertEqual(metric.information_ratio, 0.0)
        self.assertAlmostEqual(metric.correlation, 1.0, places=12)
        self.assertAlmostEqual(metric.beta, 1.0, places=12)

    def test_two_observations_is_the_minimum(self):
        selector = BenchmarkSelector({"B": [0.01, 0.02]})
        metrics = selector.evaluate_benchmarks([0.02, 0.03])
        self.assertEqual(len(metrics), 1)

        with self.assertRaises(ValueError):
            BenchmarkSelector({"B": [0.01]}).evaluate_benchmarks([0.02])

    # --- Input validation ------------------------------------------------------

    def test_nan_and_inf_rejected(self):
        with self.assertRaises(ValueError):
            BenchmarkSelector({"B": self.spy_returns}).evaluate_benchmarks(
                [float("nan")] + self.strat_returns[1:]
            )
        with self.assertRaises(ValueError):
            BenchmarkSelector(
                {"B": [float("inf")] + self.spy_returns[1:]}
            ).evaluate_benchmarks(self.strat_returns)

    def test_length_mismatch_reports_both_lengths(self):
        with self.assertRaises(ValueError) as ctx:
            BenchmarkSelector({"SHORT": self.spy_returns[:5]}).evaluate_benchmarks(
                self.strat_returns
            )
        self.assertIn("SHORT", str(ctx.exception))

    def test_empty_series_rejected(self):
        with self.assertRaises(ValueError):
            BenchmarkSelector({"B": []}).evaluate_benchmarks([])

    def test_non_numeric_and_2d_input_rejected(self):
        with self.assertRaises(ValueError):
            BenchmarkSelector({"B": self.spy_returns}).evaluate_benchmarks(["a", "b"])
        with self.assertRaises(ValueError):
            BenchmarkSelector({"B": self.spy_returns}).evaluate_benchmarks(
                np.array([[0.01, 0.02], [0.03, 0.04]])
            )

    def test_invalid_constructor_arguments_rejected(self):
        with self.assertRaises(ValueError):
            BenchmarkSelector({}, periods_per_year=0)
        with self.assertRaises(ValueError):
            BenchmarkSelector({}, periods_per_year=-252)
        with self.assertRaises(ValueError):
            BenchmarkSelector({}, ddof=2)

    def test_no_candidates_returns_empty_and_none(self):
        selector = BenchmarkSelector({})
        self.assertEqual(selector.evaluate_benchmarks(self.strat_returns), [])
        self.assertIsNone(selector.recommend_benchmark(self.strat_returns))

    def test_accepts_numpy_arrays(self):
        selector = BenchmarkSelector({"SPY": np.array(self.spy_returns)})
        metric = selector.evaluate_benchmarks(np.array(self.strat_returns))[0]
        expected = independent_tracking_error(self.strat_returns, self.spy_returns)
        self.assertAlmostEqual(metric.tracking_error, expected, places=12)

    def test_metrics_dataclass_stays_constructible_with_four_fields(self):
        # Backwards compatibility: the original four-field construction still works.
        metric = BenchmarkMetrics(
            name="X", correlation=0.5, tracking_error=0.1, information_ratio=1.0
        )
        self.assertTrue(math.isnan(metric.beta))


if __name__ == '__main__':
    unittest.main()
