"""
Unit tests for benchmark-relative-performance-attribution.

Expected values are derived independently of the implementation: analytically
where a closed form exists (the synthetic Rp = 1.2*Rb + a construction), and by
hand-computed arithmetic for the Brinson cases.
"""
import math
import statistics
import unittest

from attribution_engine import (
    AttributionError,
    BrinsonSectorResult,
    PerformanceAttributionEngine,
    StrategyComparisonRow,
)

# Two years of a deterministic, non-degenerate daily benchmark: mean 0.0004/day
# with a +/-0.001 alternation, so variance is exactly known to be non-zero and
# every derived statistic below is reproducible.
BENCHMARK_2Y = [0.0004 + (0.001 if i % 2 == 0 else -0.001) for i in range(504)]


class TestAlphaBeta(unittest.TestCase):

    def setUp(self):
        self.engine = PerformanceAttributionEngine(risk_free_rate=0.02, annualization_factor=252)

    def test_alpha_beta_evaluation(self):
        benchmark_ret = [0.001, -0.002, 0.003, -0.001, 0.002] * 20
        portfolio_ret = [0.0015, -0.0022, 0.0035, -0.0011, 0.0025] * 20

        summary = self.engine.evaluate_alpha_beta(portfolio_ret, benchmark_ret)

        self.assertAlmostEqual(summary.beta, 1.15, delta=0.05)
        self.assertTrue(summary.is_alpha_positive)
        self.assertGreater(summary.information_ratio, 0.0)
        self.assertEqual(summary.observations, 100)

    def test_documented_verification_case_beta_and_alpha(self):
        """
        SKILL.md's stated verification: Rp = 1.2*Rb + 0.05/252 with Rf = 0 must
        give beta = 1.20 and annualised alpha = 5.00%.

        Both are exact by construction, not approximations: the portfolio is an
        exact affine function of the benchmark, so the OLS slope is exactly 1.2 and
        the intercept is exactly 0.05/252 per period.
        """
        benchmark_ret = [0.004, -0.006, 0.011, -0.002, 0.007, 0.0, -0.009, 0.013] * 8
        portfolio_ret = [1.2 * r + 0.05 / 252 for r in benchmark_ret]

        engine = PerformanceAttributionEngine(risk_free_rate=0.0, annualization_factor=252)
        summary = engine.evaluate_alpha_beta(portfolio_ret, benchmark_ret)

        self.assertAlmostEqual(summary.beta, 1.20, places=9)
        self.assertAlmostEqual(summary.alpha_annualized, 0.05, places=9)

    def test_nonzero_risk_free_rate_shifts_alpha_by_beta_minus_one(self):
        """
        With Rp = beta*Rb + a, alpha = a + (beta - 1) * Rf. At beta 1.2 and
        Rf 2%, annualised alpha is 5% + 0.2*2% = 5.4% -- which is why the SKILL.md
        verification case is only exactly 5% at Rf = 0.
        """
        benchmark_ret = [0.004, -0.006, 0.011, -0.002, 0.007, 0.0, -0.009, 0.013] * 8
        portfolio_ret = [1.2 * r + 0.05 / 252 for r in benchmark_ret]

        summary = self.engine.evaluate_alpha_beta(portfolio_ret, benchmark_ret)

        self.assertAlmostEqual(summary.alpha_annualized, 0.054, places=9)

    def test_tracking_error_and_information_ratio_match_independent_computation(self):
        benchmark_ret = [0.003, -0.004, 0.006, -0.001, 0.002, 0.005, -0.007] * 10
        portfolio_ret = [0.004, -0.002, 0.005, 0.0, 0.003, 0.004, -0.005] * 10

        summary = self.engine.evaluate_alpha_beta(portfolio_ret, benchmark_ret)

        active = [p - b for p, b in zip(portfolio_ret, benchmark_ret)]
        expected_te = statistics.stdev(active) * math.sqrt(252)
        expected_ir = statistics.mean(active) * 252 / expected_te

        self.assertAlmostEqual(summary.tracking_error_annualized, expected_te, places=12)
        self.assertAlmostEqual(summary.information_ratio, expected_ir, places=9)
        self.assertAlmostEqual(
            summary.active_return_annualized, statistics.mean(active) * 252, places=12
        )

    def test_information_ratio_t_stat_equals_ir_times_sqrt_years(self):
        """t = sqrt(n) * mean(d) / stdev(d), computed here from the raw series."""
        benchmark_ret = [0.003, -0.004, 0.006, -0.001, 0.002, 0.005, -0.007] * 36  # 252 obs
        portfolio_ret = [0.004, -0.002, 0.005, 0.0, 0.003, 0.004, -0.005] * 36

        summary = self.engine.evaluate_alpha_beta(portfolio_ret, benchmark_ret)

        active = [p - b for p, b in zip(portfolio_ret, benchmark_ret)]
        expected_t = math.sqrt(len(active)) * statistics.mean(active) / statistics.stdev(active)

        self.assertEqual(summary.observations, 252)
        self.assertAlmostEqual(summary.information_ratio_t_stat, expected_t, places=9)
        # Exactly one year of daily data: t and IR coincide.
        self.assertAlmostEqual(summary.information_ratio_t_stat, summary.information_ratio, places=9)

    def test_beta_is_not_silently_one_against_flat_benchmark(self):
        """
        Regression test. A constant benchmark leaves beta unidentified; the previous
        behaviour returned 1.0, feeding an invented number into the sign-off gate.
        """
        flat_benchmark = [0.0004] * 40
        portfolio_ret = [0.001, -0.002, 0.003, -0.001, 0.002] * 8

        with self.assertRaises(AttributionError) as ctx:
            self.engine.evaluate_alpha_beta(portfolio_ret, flat_benchmark)
        self.assertIn("zero variance", str(ctx.exception))

    def test_zero_tracking_error_with_positive_active_return_is_infinite_ir(self):
        """
        Beating the benchmark by a constant every period is zero active risk, so the
        IR is +inf. Reporting 0.0 would score risk-free outperformance as average.
        """
        benchmark_ret = [0.001, -0.002, 0.003, -0.001, 0.002] * 8
        portfolio_ret = [r + 0.0001 for r in benchmark_ret]

        summary = self.engine.evaluate_alpha_beta(portfolio_ret, benchmark_ret)

        # Not exactly 0.0 -- adding a constant in floating point leaves ~1e-18 of
        # dispersion, which is exactly why the zero-risk branch uses a tolerance
        # rather than an equality test.
        self.assertLess(summary.tracking_error_annualized, 1e-12)
        self.assertEqual(summary.information_ratio, math.inf)
        self.assertEqual(summary.information_ratio_t_stat, math.inf)

    def test_nan_input_raises_instead_of_propagating(self):
        benchmark_ret = [0.001, -0.002, 0.003, -0.001, 0.002] * 8
        portfolio_ret = list(benchmark_ret)
        portfolio_ret[3] = float("nan")

        with self.assertRaises(AttributionError) as ctx:
            self.engine.evaluate_alpha_beta(portfolio_ret, benchmark_ret)
        self.assertIn("NaN/Inf", str(ctx.exception))

    def test_inf_input_raises(self):
        benchmark_ret = [0.001, -0.002, 0.003, -0.001, 0.002] * 8
        benchmark_ret[0] = float("inf")
        portfolio_ret = [0.001] * len(benchmark_ret)

        with self.assertRaises(AttributionError):
            self.engine.evaluate_alpha_beta(portfolio_ret, benchmark_ret)

    def test_non_numeric_input_raises_attribution_error(self):
        with self.assertRaises(AttributionError):
            self.engine.evaluate_alpha_beta(["a", "b", "c", "d", "e"], [0.1] * 5)

    def test_length_mismatch_error(self):
        with self.assertRaises(AttributionError):
            self.engine.evaluate_alpha_beta([0.01, 0.02], [0.01])

    def test_insufficient_samples_error(self):
        with self.assertRaises(AttributionError) as ctx:
            self.engine.evaluate_alpha_beta([0.01, 0.02, 0.03, 0.01], [0.01, 0.02, 0.01, 0.02])
        self.assertIn("Insufficient", str(ctx.exception))

    def test_thin_sample_warns(self):
        benchmark_ret = [0.001, -0.002, 0.003, -0.001, 0.002, 0.004]
        portfolio_ret = [0.002, -0.001, 0.004, -0.002, 0.003, 0.005]

        with self.assertLogs("attribution_engine", level="WARNING") as logs:
            self.engine.evaluate_alpha_beta(portfolio_ret, benchmark_ret)
        self.assertIn("statistically meaningful", "".join(logs.output))

    def test_invalid_annualization_factor_rejected(self):
        for bad in (0, -252, 252.0):
            with self.subTest(factor=bad):
                with self.assertRaises(AttributionError):
                    PerformanceAttributionEngine(annualization_factor=bad)

    def test_monthly_annualization_factor_changes_tracking_error(self):
        """TE scales with sqrt(periods_per_year); a monthly series scored at 252
        overstates it by sqrt(252/12) = 4.58x."""
        benchmark_ret = [0.01, -0.02, 0.03, -0.01, 0.02, 0.015] * 6
        portfolio_ret = [0.012, -0.018, 0.035, -0.008, 0.022, 0.017] * 6

        monthly = PerformanceAttributionEngine(annualization_factor=12)
        daily = PerformanceAttributionEngine(annualization_factor=252)

        te_m = monthly.evaluate_alpha_beta(portfolio_ret, benchmark_ret).tracking_error_annualized
        te_d = daily.evaluate_alpha_beta(portfolio_ret, benchmark_ret).tracking_error_annualized

        self.assertAlmostEqual(te_d / te_m, math.sqrt(252 / 12), places=9)


class TestBrinsonAttribution(unittest.TestCase):

    def setUp(self):
        self.engine = PerformanceAttributionEngine(risk_free_rate=0.02, annualization_factor=252)
        self.p_weights = {"Tech": 0.50, "Financials": 0.50}
        self.b_weights = {"Tech": 0.30, "Financials": 0.70}
        self.p_returns = {"Tech": 0.10, "Financials": 0.05}
        self.b_returns = {"Tech": 0.08, "Financials": 0.04}
        self.total_benchmark_return = 0.052  # 0.30*0.08 + 0.70*0.04

    def test_brinson_sector_attribution(self):
        results = self.engine.compute_brinson_attribution(
            self.p_weights,
            self.b_weights,
            self.p_returns,
            self.b_returns,
            self.total_benchmark_return,
        )

        self.assertEqual(len(results), 2)
        tech = next(r for r in results if r.sector == "Tech")
        fins = next(r for r in results if r.sector == "Financials")

        # Hand-computed Brinson-Fachler effects.
        # Tech:  alloc (0.50-0.30)*(0.08-0.052) = 0.20*0.028   = +0.0056
        #        select 0.30*(0.10-0.08)                       = +0.0060
        #        inter  (0.20)*(0.02)                          = +0.0040
        self.assertAlmostEqual(tech.allocation_effect, 0.0056, places=12)
        self.assertAlmostEqual(tech.selection_effect, 0.0060, places=12)
        self.assertAlmostEqual(tech.interaction_effect, 0.0040, places=12)
        # Financials: alloc (-0.20)*(0.04-0.052) = -0.20*-0.012 = +0.0024
        #             select 0.70*(0.05-0.04)                   = +0.0070
        #             inter  (-0.20)*(0.01)                     = -0.0020
        self.assertAlmostEqual(fins.allocation_effect, 0.0024, places=12)
        self.assertAlmostEqual(fins.selection_effect, 0.0070, places=12)
        self.assertAlmostEqual(fins.interaction_effect, -0.0020, places=12)

    def test_effects_reconcile_to_active_return(self):
        """
        The identity the checklist asserts. Portfolio return 0.50*0.10 + 0.50*0.05
        = 0.075; benchmark 0.052; active 0.023. Effects must sum to exactly that.
        """
        results = self.engine.compute_brinson_attribution(
            self.p_weights, self.b_weights, self.p_returns, self.b_returns
        )

        self.assertAlmostEqual(sum(r.total_effect for r in results), 0.023, places=12)

    def test_total_benchmark_return_derived_when_omitted(self):
        omitted = self.engine.compute_brinson_attribution(
            self.p_weights, self.b_weights, self.p_returns, self.b_returns
        )
        supplied = self.engine.compute_brinson_attribution(
            self.p_weights,
            self.b_weights,
            self.p_returns,
            self.b_returns,
            self.total_benchmark_return,
        )
        self.assertEqual(omitted, supplied)

    def test_inconsistent_total_benchmark_return_raises(self):
        """
        Regression test: an inconsistent total benchmark return -- e.g. a compounded
        annual figure pasted into a single-period call -- used to be accepted and
        silently produced allocation effects that did not reconcile.
        """
        with self.assertRaises(AttributionError) as ctx:
            self.engine.compute_brinson_attribution(
                self.p_weights, self.b_weights, self.p_returns, self.b_returns, 0.12
            )
        self.assertIn("does not match", str(ctx.exception))

    def test_weights_not_summing_to_one_raise(self):
        """
        Regression test: partial sector coverage used to return effects that looked
        fine but did not sum to active return.
        """
        partial = {"Tech": 0.50, "Financials": 0.30}
        with self.assertRaises(AttributionError) as ctx:
            self.engine.compute_brinson_attribution(
                partial, self.b_weights, self.p_returns, self.b_returns
            )
        self.assertIn("sums to", str(ctx.exception))

        with self.assertRaises(AttributionError):
            self.engine.compute_brinson_attribution(
                self.p_weights, partial, self.p_returns, self.b_returns
            )

    def test_missing_return_for_weighted_sector_raises(self):
        """A mistyped sector key must not silently become a 0.0 return."""
        with self.assertRaises(AttributionError) as ctx:
            self.engine.compute_brinson_attribution(
                self.p_weights,
                self.b_weights,
                {"Tech": 0.10, "Finncials": 0.05},  # typo
                self.b_returns,
            )
        self.assertIn("Financials", str(ctx.exception))

    def test_off_benchmark_sector_reconciles(self):
        """A sector held with zero benchmark weight still reconciles to active return."""
        p_weights = {"Tech": 0.40, "Financials": 0.40, "Crypto": 0.20}
        p_returns = {"Tech": 0.10, "Financials": 0.05, "Crypto": 0.30}

        results = self.engine.compute_brinson_attribution(
            p_weights, self.b_weights, p_returns, self.b_returns
        )

        portfolio_return = 0.40 * 0.10 + 0.40 * 0.05 + 0.20 * 0.30
        self.assertAlmostEqual(
            sum(r.total_effect for r in results),
            portfolio_return - self.total_benchmark_return,
            places=12,
        )
        crypto = next(r for r in results if r.sector == "Crypto")
        self.assertEqual(crypto.selection_effect, 0.0)

    def test_brinson_fachler_differs_from_hood_beebower_on_sign(self):
        """
        Under BF, overweighting a sector that rose but underperformed the benchmark
        is a negative allocation effect. Under BHB it would be positive because the
        sector return is positive. This asserts the BF convention is implemented.
        """
        p_weights = {"Laggard": 0.60, "Leader": 0.40}
        b_weights = {"Laggard": 0.40, "Leader": 0.60}
        b_returns = {"Laggard": 0.01, "Leader": 0.09}  # benchmark total = 0.058
        p_returns = dict(b_returns)  # no selection skill, isolate allocation

        results = self.engine.compute_brinson_attribution(
            p_weights, b_weights, p_returns, b_returns
        )
        laggard = next(r for r in results if r.sector == "Laggard")

        # BF:  (0.60-0.40) * (0.01 - 0.058) = -0.0096
        # BHB: (0.60-0.40) * 0.01           = +0.0020
        self.assertAlmostEqual(laggard.allocation_effect, -0.0096, places=12)
        self.assertLess(laggard.allocation_effect, 0.0)

    def test_empty_weights_raise(self):
        with self.assertRaises(AttributionError):
            self.engine.compute_brinson_attribution({}, self.b_weights, {}, self.b_returns)

    def test_non_finite_weight_raises(self):
        bad = {"Tech": float("nan"), "Financials": 0.50}
        with self.assertRaises(AttributionError):
            self.engine.compute_brinson_attribution(
                bad, self.b_weights, self.p_returns, self.b_returns
            )

    def test_non_finite_sector_return_raises(self):
        bad = {"Tech": float("inf"), "Financials": 0.05}
        with self.assertRaises(AttributionError):
            self.engine.compute_brinson_attribution(
                self.p_weights, self.b_weights, bad, self.b_returns
            )

    def test_reduced_precision_inputs_still_reconcile(self):
        """
        Inputs are normalised to float64 before any arithmetic. Passing float32
        weights straight through would leave the reconciliation identity holding
        only to ~1e-7 and trip the internal assertion.

        Simulated here by rounding to float32 precision via `struct`, so the test
        does not require numpy.
        """
        import struct

        def as_float32(x):
            return struct.unpack("f", struct.pack("f", x))[0]

        p_weights = {k: as_float32(v) for k, v in self.p_weights.items()}
        p_returns = {k: as_float32(v) for k, v in self.p_returns.items()}

        results = self.engine.compute_brinson_attribution(
            p_weights, self.b_weights, p_returns, self.b_returns
        )

        expected_active = (
            sum(p_weights[k] * p_returns[k] for k in p_weights) - self.total_benchmark_return
        )
        self.assertAlmostEqual(sum(r.total_effect for r in results), expected_active, places=12)
        self.assertIsInstance(results[0].total_effect, float)

    def test_boolean_weight_rejected(self):
        """`float(True) == 1.0` would turn a boolean typo into a full weight."""
        with self.assertRaises(AttributionError):
            self.engine.compute_brinson_attribution(
                {"Tech": True, "Financials": 0.0}, self.b_weights, self.p_returns, self.b_returns
            )

    def test_results_are_sorted_deterministically(self):
        results = self.engine.compute_brinson_attribution(
            self.p_weights, self.b_weights, self.p_returns, self.b_returns
        )
        self.assertEqual([r.sector for r in results], ["Financials", "Tech"])
        self.assertIsInstance(results[0], BrinsonSectorResult)


# ---------------------------------------------------------------------- #
# Correlation, caveats and the leveraged-clone case
# (folded in from benchmark-relative-performance-attribution)
# ---------------------------------------------------------------------- #
class TestCorrelationAndCaveats(unittest.TestCase):

    def setUp(self):
        self.engine = PerformanceAttributionEngine(risk_free_rate=0.04, annualization_factor=252)

    def test_unfunded_leveraged_clone_has_alpha_equal_to_the_risk_free_rate(self):
        """A 2x unfunded clone shows alpha = +Rf, not 0.

        Jensen's alpha charges the risk-free rate against one unit of capital while
        the portfolio earns twice the benchmark's excess return, so the "alpha" is
        the un-charged borrowing cost of the second unit. Reading it as skill is the
        classic leverage-as-alpha error.
        """
        portfolio = [2.0 * r for r in BENCHMARK_2Y]
        summary = self.engine.evaluate_alpha_beta(portfolio, BENCHMARK_2Y)

        self.assertAlmostEqual(summary.beta, 2.0, places=12)
        self.assertAlmostEqual(summary.correlation_to_benchmark, 1.0, places=12)
        self.assertAlmostEqual(summary.alpha_annualized, 0.04, places=12)
        self.assertNotAlmostEqual(summary.alpha_annualized, 0.0, places=3)

    def test_affine_clone_alpha_matches_the_closed_form(self):
        # portfolio = 1.5*benchmark + 0.0002 per day, Rf = 4%:
        # alpha = 252*0.0002 + (1 - 1.5)*(-Rf) = 0.0504 + 0.5*0.04 = 0.0704.
        portfolio = [1.5 * r + 0.0002 for r in BENCHMARK_2Y]
        summary = self.engine.evaluate_alpha_beta(portfolio, BENCHMARK_2Y)

        self.assertAlmostEqual(summary.beta, 1.5, places=12)
        self.assertAlmostEqual(summary.alpha_annualized, 0.0704, places=10)

    def test_correlation_is_nan_not_zero_for_a_constant_portfolio(self):
        flat = [0.0005] * len(BENCHMARK_2Y)
        summary = self.engine.evaluate_alpha_beta(flat, BENCHMARK_2Y)

        self.assertTrue(math.isnan(summary.correlation_to_benchmark))
        self.assertTrue(any("undefined" in w for w in summary.warnings))

    def test_low_volatility_benchmark_is_not_misclassified_as_constant(self):
        """Regression: a `variance > 1e-8` degeneracy guard trips on a real benchmark.

        A cash or short-duration index runs a daily sigma of ~1bp, i.e. a variance
        of ~1e-8. A guard placed at that magnitude reports beta = 0 next to a
        correlation of 1.0. The tolerance must sit at the floating-point noise floor.
        """
        cash_like = [0.0001 * ((-1) ** i) for i in range(504)]
        portfolio = [2.0 * r for r in cash_like]
        summary = self.engine.evaluate_alpha_beta(portfolio, cash_like)

        self.assertAlmostEqual(summary.beta, 2.0, places=10)
        self.assertAlmostEqual(summary.correlation_to_benchmark, 1.0, places=10)

    def test_sub_one_year_sample_is_flagged_in_warnings(self):
        short_benchmark = BENCHMARK_2Y[:60]
        portfolio = [1.1 * r for r in short_benchmark]
        summary = self.engine.evaluate_alpha_beta(portfolio, short_benchmark)

        self.assertTrue(any("year(s)" in w for w in summary.warnings))
        self.assertEqual(summary.observations, 60)

    def test_insignificant_t_stat_is_flagged_in_warnings(self):
        # Active return of +/-20bp a day around a 0.1bp mean: over two years
        # t = sqrt(504) * 1e-5 / 2e-3 ~ 0.11, far inside +/-1.96.
        portfolio = [r + (0.002 if i % 2 == 0 else -0.002) + 0.00001
                     for i, r in enumerate(BENCHMARK_2Y)]
        summary = self.engine.evaluate_alpha_beta(portfolio, BENCHMARK_2Y)

        self.assertLess(abs(summary.information_ratio_t_stat), 1.96)
        self.assertTrue(any("not distinguishable from zero" in w for w in summary.warnings))

    def test_unbounded_information_ratio_is_flagged_rather_than_reported_as_zero(self):
        portfolio = [r + 0.001 for r in BENCHMARK_2Y]
        summary = self.engine.evaluate_alpha_beta(portfolio, BENCHMARK_2Y)

        self.assertEqual(summary.information_ratio, math.inf)
        self.assertTrue(any("unbounded" in w for w in summary.warnings))

    def test_a_clean_long_sample_carries_no_warnings(self):
        portfolio = [1.2 * r + 0.05 / 252 for r in BENCHMARK_2Y]
        summary = self.engine.evaluate_alpha_beta(portfolio, BENCHMARK_2Y)
        self.assertEqual(summary.warnings, [])


# ---------------------------------------------------------------------- #
# Multi-strategy comparison
# (folded in from benchmark-relative-performance-attribution)
# ---------------------------------------------------------------------- #
class TestMultiStrategyComparison(unittest.TestCase):

    def setUp(self):
        self.engine = PerformanceAttributionEngine(risk_free_rate=0.0, annualization_factor=252)
        self.benchmark = BENCHMARK_2Y
        # Three affine clones with closed-form beta and alpha at Rf = 0:
        #   alpha = 252 * intercept, beta = slope.
        self.strategies = {
            "Momentum": [1.2 * r + 0.05 / 252 for r in self.benchmark],
            "StatArb": [0.8 * r + 0.02 / 252 for r in self.benchmark],
            "Carry": [1.0 * r - 0.01 / 252 for r in self.benchmark],
        }

    def test_rows_carry_the_closed_form_statistics_for_each_strategy(self):
        rows = {r.strategy: r for r in
                self.engine.compare_strategies(self.strategies, self.benchmark)}

        self.assertEqual(set(rows), {"Momentum", "StatArb", "Carry"})
        self.assertIsInstance(rows["Momentum"], StrategyComparisonRow)
        for name, beta, alpha in (
            ("Momentum", 1.2, 0.05), ("StatArb", 0.8, 0.02), ("Carry", 1.0, -0.01),
        ):
            with self.subTest(strategy=name):
                self.assertAlmostEqual(rows[name].beta, beta, places=10)
                self.assertAlmostEqual(rows[name].alpha_annualized, alpha, places=10)
                self.assertAlmostEqual(rows[name].correlation_to_benchmark, 1.0, places=10)
                self.assertEqual(rows[name].observations, len(self.benchmark))

    def test_rows_match_the_single_strategy_path_exactly(self):
        """The comparison must not be a second implementation of the same maths."""
        rows = self.engine.compare_strategies(self.strategies, self.benchmark)
        for row in rows:
            with self.subTest(strategy=row.strategy):
                summary = self.engine.evaluate_alpha_beta(
                    self.strategies[row.strategy], self.benchmark
                )
                self.assertEqual(row.alpha_annualized, summary.alpha_annualized)
                self.assertEqual(row.beta, summary.beta)
                self.assertEqual(row.information_ratio, summary.information_ratio)
                self.assertEqual(row.tracking_error_annualized,
                                 summary.tracking_error_annualized)
                self.assertEqual(row.warnings, summary.warnings)

    def test_rows_are_sorted_by_information_ratio_descending(self):
        rows = self.engine.compare_strategies(self.strategies, self.benchmark)
        ratios = [r.information_ratio for r in rows]
        self.assertEqual(ratios, sorted(ratios, reverse=True))
        # Momentum has the largest positive alpha per unit of active risk here.
        self.assertEqual(rows[0].strategy, "Momentum")
        self.assertEqual(rows[-1].strategy, "Carry")

    def test_insertion_order_is_preserved_when_sorting_is_disabled(self):
        rows = self.engine.compare_strategies(
            self.strategies, self.benchmark, sort_by_information_ratio=False
        )
        self.assertEqual([r.strategy for r in rows], list(self.strategies))

    def test_undefined_information_ratios_sort_last_with_a_name_tie_break(self):
        strategies = {
            "Zulu": [r for r in self.benchmark],          # IR = 0/0 -> 0.0
            "Alpha": [r for r in self.benchmark],
            "Real": [1.1 * r + 0.03 / 252 for r in self.benchmark],
        }
        rows = self.engine.compare_strategies(strategies, self.benchmark)
        self.assertEqual(rows[0].strategy, "Real")
        # Exact replicas tie at IR = 0.0 and are ordered by name.
        self.assertEqual([r.strategy for r in rows[1:]], ["Alpha", "Zulu"])

    def test_a_failing_strategy_raises_with_its_name_rather_than_being_dropped(self):
        strategies = dict(self.strategies)
        strategies["Broken"] = self.benchmark[:-1]  # length mismatch
        with self.assertRaises(AttributionError) as ctx:
            self.engine.compare_strategies(strategies, self.benchmark)
        self.assertIn("Broken", str(ctx.exception))

    def test_empty_mapping_and_bad_names_raise(self):
        with self.assertRaises(AttributionError):
            self.engine.compare_strategies({}, self.benchmark)
        with self.assertRaises(AttributionError):
            self.engine.compare_strategies({"  ": self.benchmark}, self.benchmark)
        with self.assertRaises(AttributionError):
            self.engine.compare_strategies({7: self.benchmark}, self.benchmark)

    def test_a_nan_in_one_strategy_raises_for_the_whole_table(self):
        strategies = dict(self.strategies)
        strategies["Bad"] = [float("nan")] * len(self.benchmark)
        with self.assertRaises(AttributionError):
            self.engine.compare_strategies(strategies, self.benchmark)

    def test_rendered_table_has_one_line_per_strategy_and_a_caveat_column(self):
        rows = self.engine.compare_strategies(self.strategies, self.benchmark)
        table = self.engine.render_comparison_table(rows)
        lines = table.splitlines()

        self.assertEqual(len(lines), len(self.strategies) + 2)  # header + rule + rows
        self.assertIn("Strategy", lines[0])
        self.assertIn("Caveats", lines[0])
        for row in rows:
            self.assertTrue(any(line.startswith(row.strategy) for line in lines[2:]))
        # No "-0.00%" artefacts from float noise on a zero-alpha row.
        self.assertNotIn("-0.00%", table)

    def test_comparison_against_a_simple_alternative_benchmark(self):
        """The capital-allocation question: does the complex book beat 60/40?

        Benchmarking the multi-strategy portfolio against the simple alternative it
        is supposed to replace is the same arithmetic pointed at a different series.
        """
        sixty_forty = [0.6 * r + 0.4 * 0.0001 for r in self.benchmark]
        book = [sum(s[i] for s in self.strategies.values()) / 3.0
                for i in range(len(self.benchmark))]

        rows = self.engine.compare_strategies({"MultiStrategyBook": book}, sixty_forty)
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0].strategy, "MultiStrategyBook")
        self.assertTrue(math.isfinite(rows[0].beta))
        self.assertTrue(math.isfinite(rows[0].information_ratio))


if __name__ == "__main__":
    unittest.main()
