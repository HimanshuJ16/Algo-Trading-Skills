"""
Unit tests for strategy-specific-vs-shared-risk-budget-allocation.

Expected values are derived by hand from the Euler definitions rather than by
re-running the module's own expressions. For the base 50/50 book with

    Sigma = [[1e-4, 2e-5],
             [2e-5, 4e-4]]     (daily)

the arithmetic is small enough to do in closed form:

    w        = [0.5, 0.5]
    Sigma w  = [6.0e-5, 2.1e-4]
    w'Sigma w = 0.5*6.0e-5 + 0.5*2.1e-4 = 1.35e-4
    c_1      = w_1 (Sigma w)_1 / w'Sigma w = 3.0e-5 / 1.35e-4 = 2/9 = 22.222%
    c_2      = 1.05e-4 / 1.35e-4 = 7/9 = 77.778%

so the component shares are exact rationals, independent of this implementation.

The solver regression test is the important one: the previous implementation
returned ``budget / actual`` as the recommended capital factor, which does not
bring a strategy to its budget because component risk share is not linear in the
capital weight. On the 70/30 book below it returned 0.4264 for a 40% budget and
applying it left the strategy at 77.6%.
"""
import logging
import math
import unittest

import numpy as np

from strategy_specific_vs_shared_risk_budget_allocation import (
    Config,
    Engine,
    PortfolioRiskBudgetAllocationReport,
    StrategyRiskBreakdown,
    StrategyRiskBudgetSpec,
    StrategySpecificVsSharedRiskBudgetEngine,
)

MODULE_LOGGER = "strategy_specific_vs_shared_risk_budget_allocation"

# Keep logging's "last resort" stderr handler from printing expected breach
# warnings during the run; assertLogs still installs its own handler.
logging.getLogger(MODULE_LOGGER).addHandler(logging.NullHandler())

TRADING_DAYS = 252
ANNUALIZE = math.sqrt(TRADING_DAYS)
Z95 = 1.645

# Base book: two strategies, equal capital, mildly positively correlated.
BASE_COV = np.array(
    [
        [0.0001, 0.00002],
        [0.00002, 0.0004],
    ]
)

# Concentrated book used for the adjustment-factor tests.
CONCENTRATED_COV = np.array(
    [
        [0.0004, 0.00002],
        [0.00002, 0.0001],
    ]
)


def base_specs():
    return [
        StrategyRiskBudgetSpec("STAT_ARB", 500_000.0, 15.0, 40.0),
        StrategyRiskBudgetSpec("TREND", 500_000.0, 25.0, 70.0),
    ]


class TestEngineLegacy(unittest.TestCase):
    """The legacy Config/Engine pair is part of the public surface."""

    def test_init(self):
        engine = Engine(Config())
        self.assertTrue(engine.config.enabled)

    def test_run(self):
        engine = Engine(Config())
        self.assertTrue(engine.run())


class TestEulerDecomposition(unittest.TestCase):

    def setUp(self):
        self.engine = StrategySpecificVsSharedRiskBudgetEngine(base_specs())

    def test_component_shares_match_hand_derived_values(self):
        report = self.engine.evaluate_risk_budgets(BASE_COV, ["STAT_ARB", "TREND"])

        # 2/9 and 7/9 exactly -- see module docstring.
        self.assertAlmostEqual(
            report.strategy_breakdown["STAT_ARB"].component_risk_pct, 22.22, places=2
        )
        self.assertAlmostEqual(
            report.strategy_breakdown["TREND"].component_risk_pct, 77.78, places=2
        )
        self.assertTrue(report.is_euler_decomposition_valid)

    def test_component_shares_sum_to_one_hundred_percent(self):
        report = self.engine.evaluate_risk_budgets(BASE_COV, ["STAT_ARB", "TREND"])
        total = sum(b.component_risk_pct for b in report.strategy_breakdown.values())
        self.assertAlmostEqual(total, 100.0, places=2)

    def test_risk_contributions_sum_to_portfolio_volatility(self):
        """Euler's theorem: sum_i w_i * MCR_i == sigma_p, with no residual."""
        report = self.engine.evaluate_risk_budgets(BASE_COV, ["STAT_ARB", "TREND"])
        daily_sigma_p = math.sqrt(0.000135)
        contributions = sum(
            b.capital_weight * b.marginal_contribution_to_risk
            for b in report.strategy_breakdown.values()
        )
        self.assertAlmostEqual(contributions, daily_sigma_p, places=6)

    def test_marginal_contributions_match_closed_form(self):
        report = self.engine.evaluate_risk_budgets(BASE_COV, ["STAT_ARB", "TREND"])
        daily_sigma_p = math.sqrt(0.000135)
        self.assertAlmostEqual(
            report.strategy_breakdown["STAT_ARB"].marginal_contribution_to_risk,
            6.0e-5 / daily_sigma_p,
            places=6,
        )
        self.assertAlmostEqual(
            report.strategy_breakdown["TREND"].marginal_contribution_to_risk,
            2.1e-4 / daily_sigma_p,
            places=6,
        )

    def test_portfolio_volatility_and_var(self):
        report = self.engine.evaluate_risk_budgets(BASE_COV, ["STAT_ARB", "TREND"])
        expected_vol_pct = math.sqrt(0.000135) * ANNUALIZE * 100.0
        self.assertAlmostEqual(
            report.total_portfolio_volatility_pct, round(expected_vol_pct, 2), places=2
        )
        expected_var = 1_000_000.0 * math.sqrt(0.000135) * ANNUALIZE * Z95
        self.assertAlmostEqual(
            report.total_portfolio_var_95_usd, round(expected_var, 2), places=2
        )
        self.assertEqual(report.total_portfolio_capital_usd, 1_000_000.0)

    def test_standalone_volatilities_come_from_the_diagonal_only(self):
        report = self.engine.evaluate_risk_budgets(BASE_COV, ["STAT_ARB", "TREND"])
        self.assertAlmostEqual(
            report.strategy_breakdown["STAT_ARB"].standalone_volatility_pct,
            round(math.sqrt(0.0001) * ANNUALIZE * 100.0, 2),
            places=2,
        )
        self.assertAlmostEqual(
            report.strategy_breakdown["TREND"].standalone_volatility_pct,
            round(math.sqrt(0.0004) * ANNUALIZE * 100.0, 2),
            places=2,
        )

    def test_results_are_invariant_to_strategy_ordering(self):
        forward = self.engine.evaluate_risk_budgets(BASE_COV, ["STAT_ARB", "TREND"])
        reversed_cov = BASE_COV[::-1, ::-1]
        backward = self.engine.evaluate_risk_budgets(reversed_cov, ["TREND", "STAT_ARB"])
        for sid in ("STAT_ARB", "TREND"):
            self.assertAlmostEqual(
                forward.strategy_breakdown[sid].component_risk_pct,
                backward.strategy_breakdown[sid].component_risk_pct,
                places=6,
            )


class TestDualTierBreachDetection(unittest.TestCase):

    def test_standalone_breach_is_flagged(self):
        engine = StrategySpecificVsSharedRiskBudgetEngine(base_specs())
        # STAT_ARB at 0.0009 daily variance -> 47.6% annualized vs a 15% limit.
        cov = np.array([[0.0009, 0.0001], [0.0001, 0.0001]])
        report = engine.evaluate_risk_budgets(cov, ["STAT_ARB", "TREND"])

        breakdown = report.strategy_breakdown["STAT_ARB"]
        self.assertIn("STAT_ARB", report.breached_strategies)
        self.assertTrue(breakdown.standalone_limit_breached)
        self.assertLess(breakdown.recommended_capital_adjustment_factor, 1.0)
        self.assertAlmostEqual(
            breakdown.standalone_delever_factor,
            15.0 / (math.sqrt(0.0009) * ANNUALIZE * 100.0),
            places=5,
        )

    def test_strategy_exactly_on_its_limit_is_compliant(self):
        """The comparison is strict '>': sitting on the limit is not a breach."""
        exact_vol = math.sqrt(0.0001) * ANNUALIZE * 100.0
        specs = [
            StrategyRiskBudgetSpec("STAT_ARB", 500_000.0, exact_vol, 40.0),
            StrategyRiskBudgetSpec("TREND", 500_000.0, 100.0, 100.0),
        ]
        engine = StrategySpecificVsSharedRiskBudgetEngine(specs)
        report = engine.evaluate_risk_budgets(BASE_COV, ["STAT_ARB", "TREND"])
        self.assertFalse(
            report.strategy_breakdown["STAT_ARB"].standalone_limit_breached
        )

    def test_a_high_volatility_diversifier_breaches_standalone_but_not_shared(self):
        """
        The documented pitfall: a hedge can be the loudest standalone offender while
        contributing negative portfolio risk. Both flags must be reported separately
        so the caller does not cut the hedge on the standalone number alone.
        """
        cov = np.array([[0.0004, -0.00018], [-0.00018, 0.0001]])
        specs = [
            StrategyRiskBudgetSpec("HEDGE", 300_000.0, 15.0, 60.0),
            StrategyRiskBudgetSpec("CORE", 700_000.0, 100.0, 100.0),
        ]
        report = StrategySpecificVsSharedRiskBudgetEngine(specs).evaluate_risk_budgets(
            cov, ["HEDGE", "CORE"]
        )
        hedge = report.strategy_breakdown["HEDGE"]
        self.assertTrue(hedge.standalone_limit_breached)
        self.assertLess(hedge.component_risk_pct, 0.0)
        self.assertFalse(hedge.shared_budget_breached)


class TestSharedBudgetCapitalFactor(unittest.TestCase):
    """
    Regression tests for the capital scaling factor.

    The naive ``budget / actual`` ratio is wrong because the component risk share
    is not linear in the capital weight. These tests assert the property that
    matters -- applying the returned factor actually clears the breach.
    """

    def concentrated_engine(self, capital_a=700_000.0, budget_a=40.0):
        specs = [
            StrategyRiskBudgetSpec("DOMINANT", capital_a, 100.0, budget_a),
            StrategyRiskBudgetSpec("SMALL", 300_000.0, 100.0, 100.0),
        ]
        return StrategySpecificVsSharedRiskBudgetEngine(specs)

    def test_dominant_strategy_breaches_shared_budget(self):
        report = self.concentrated_engine().evaluate_risk_budgets(
            CONCENTRATED_COV, ["DOMINANT", "SMALL"]
        )
        breakdown = report.strategy_breakdown["DOMINANT"]
        self.assertTrue(breakdown.shared_budget_breached)
        self.assertAlmostEqual(breakdown.component_risk_pct, 93.81, places=2)

    def test_naive_ratio_would_not_clear_the_breach(self):
        """
        Guards the regression directly: budget/actual = 40/93.81 = 0.4264, and
        applying it leaves the strategy at ~77.6%, far above its 40% budget. The
        solved factor must therefore be materially smaller than the naive ratio.
        """
        report = self.concentrated_engine().evaluate_risk_budgets(
            CONCENTRATED_COV, ["DOMINANT", "SMALL"]
        )
        breakdown = report.strategy_breakdown["DOMINANT"]
        naive_ratio = 40.0 / breakdown.component_risk_pct
        self.assertAlmostEqual(naive_ratio, 0.4264, places=4)
        self.assertLess(breakdown.shared_budget_capital_factor, naive_ratio)

        naive_report = self.concentrated_engine(
            capital_a=700_000.0 * naive_ratio
        ).evaluate_risk_budgets(CONCENTRATED_COV, ["DOMINANT", "SMALL"])
        self.assertGreater(
            naive_report.strategy_breakdown["DOMINANT"].component_risk_pct, 70.0
        )

    def test_applying_the_solved_factor_clears_the_breach(self):
        report = self.concentrated_engine().evaluate_risk_budgets(
            CONCENTRATED_COV, ["DOMINANT", "SMALL"]
        )
        factor = report.strategy_breakdown["DOMINANT"].shared_budget_capital_factor

        rerun = self.concentrated_engine(
            capital_a=700_000.0 * factor
        ).evaluate_risk_budgets(CONCENTRATED_COV, ["DOMINANT", "SMALL"])
        after = rerun.strategy_breakdown["DOMINANT"]
        self.assertLessEqual(after.component_risk_pct, 40.0)
        self.assertFalse(after.shared_budget_breached)
        self.assertEqual(rerun.breached_strategies, [])

    def test_recommended_factor_also_clears_the_breach(self):
        """The 4-dp public factor is floored, not rounded, so it stays inside budget."""
        report = self.concentrated_engine().evaluate_risk_budgets(
            CONCENTRATED_COV, ["DOMINANT", "SMALL"]
        )
        factor = report.strategy_breakdown[
            "DOMINANT"
        ].recommended_capital_adjustment_factor

        rerun = self.concentrated_engine(
            capital_a=700_000.0 * factor
        ).evaluate_risk_budgets(CONCENTRATED_COV, ["DOMINANT", "SMALL"])
        self.assertEqual(rerun.breached_strategies, [])

    def test_compliant_strategy_gets_a_unit_factor(self):
        report = self.concentrated_engine(budget_a=100.0).evaluate_risk_budgets(
            CONCENTRATED_COV, ["DOMINANT", "SMALL"]
        )
        breakdown = report.strategy_breakdown["DOMINANT"]
        self.assertFalse(breakdown.shared_budget_breached)
        self.assertEqual(breakdown.shared_budget_capital_factor, 1.0)
        self.assertEqual(breakdown.recommended_capital_adjustment_factor, 1.0)

    def test_budget_set_exactly_at_the_current_share_needs_no_reduction(self):
        """
        The breach flag and the solver compute the share by slightly different
        float paths, so a strategy sitting exactly on its budget must not produce
        a broken bisection bracket.
        """
        report = self.concentrated_engine(budget_a=93.81).evaluate_risk_budgets(
            CONCENTRATED_COV, ["DOMINANT", "SMALL"]
        )
        breakdown = report.strategy_breakdown["DOMINANT"]
        self.assertGreater(breakdown.shared_budget_capital_factor, 0.0)
        self.assertLessEqual(breakdown.shared_budget_capital_factor, 1.0)
        self.assertFalse(breakdown.shared_budget_infeasible_by_scaling)

    def test_hedge_with_non_monotone_share_still_clears_its_budget(self):
        """
        Component share is not monotone in the capital weight when the strategy
        hedges the rest of the book, so the solver must rely on a sign change
        rather than a monotone search.
        """
        cov = np.array([[0.0004, -0.00018], [-0.00018, 0.0001]])
        specs = [
            StrategyRiskBudgetSpec("A", 500_000.0, 100.0, 60.0),
            StrategyRiskBudgetSpec("B", 500_000.0, 100.0, 100.0),
        ]
        report = StrategySpecificVsSharedRiskBudgetEngine(specs).evaluate_risk_budgets(
            cov, ["A", "B"]
        )
        factor = report.strategy_breakdown["A"].shared_budget_capital_factor
        self.assertTrue(report.strategy_breakdown["A"].shared_budget_breached)
        self.assertLess(factor, 1.0)

        rerun_specs = [
            StrategyRiskBudgetSpec("A", 500_000.0 * factor, 100.0, 60.0),
            StrategyRiskBudgetSpec("B", 500_000.0, 100.0, 100.0),
        ]
        rerun = StrategySpecificVsSharedRiskBudgetEngine(
            rerun_specs
        ).evaluate_risk_budgets(cov, ["A", "B"])
        self.assertLessEqual(rerun.strategy_breakdown["A"].component_risk_pct, 60.0)

    def test_single_strategy_budget_is_unreachable_by_scaling(self):
        """
        A one-strategy book carries 100% of its own risk at every capital level, so
        no scaling satisfies a sub-100% budget. That must be flagged, not answered
        with a factor that does nothing.
        """
        engine = StrategySpecificVsSharedRiskBudgetEngine(
            [StrategyRiskBudgetSpec("SOLO", 1_000_000.0, 100.0, 40.0)]
        )
        report = engine.evaluate_risk_budgets(np.array([[0.0001]]), ["SOLO"])
        breakdown = report.strategy_breakdown["SOLO"]
        self.assertAlmostEqual(breakdown.component_risk_pct, 100.0, places=6)
        self.assertTrue(breakdown.shared_budget_infeasible_by_scaling)
        self.assertEqual(breakdown.recommended_capital_adjustment_factor, 0.0)
        self.assertFalse(report.budgets_feasible)


class TestBudgetFeasibility(unittest.TestCase):

    def test_budgets_summing_below_one_hundred_are_flagged_infeasible(self):
        specs = [
            StrategyRiskBudgetSpec("A", 500_000.0, 100.0, 30.0),
            StrategyRiskBudgetSpec("B", 500_000.0, 100.0, 30.0),
        ]
        report = StrategySpecificVsSharedRiskBudgetEngine(specs).evaluate_risk_budgets(
            BASE_COV, ["A", "B"]
        )
        self.assertFalse(report.budgets_feasible)

    def test_budgets_summing_to_one_hundred_are_feasible(self):
        specs = [
            StrategyRiskBudgetSpec("A", 500_000.0, 100.0, 50.0),
            StrategyRiskBudgetSpec("B", 500_000.0, 100.0, 50.0),
        ]
        report = StrategySpecificVsSharedRiskBudgetEngine(specs).evaluate_risk_budgets(
            BASE_COV, ["A", "B"]
        )
        self.assertTrue(report.budgets_feasible)


class TestVarHorizon(unittest.TestCase):

    def test_horizon_is_reported_and_scales_by_square_root_of_time(self):
        one_day = StrategySpecificVsSharedRiskBudgetEngine(
            base_specs(), var_horizon_days=1
        ).evaluate_risk_budgets(BASE_COV, ["STAT_ARB", "TREND"])
        annual = StrategySpecificVsSharedRiskBudgetEngine(
            base_specs(), var_horizon_days=252
        ).evaluate_risk_budgets(BASE_COV, ["STAT_ARB", "TREND"])

        self.assertEqual(one_day.var_horizon_days, 1)
        self.assertEqual(annual.var_horizon_days, 252)
        self.assertAlmostEqual(
            annual.total_portfolio_var_95_usd / one_day.total_portfolio_var_95_usd,
            ANNUALIZE,
            places=4,
        )

    def test_one_day_var_matches_closed_form(self):
        report = StrategySpecificVsSharedRiskBudgetEngine(
            base_specs(), var_horizon_days=1
        ).evaluate_risk_budgets(BASE_COV, ["STAT_ARB", "TREND"])
        expected = 1_000_000.0 * math.sqrt(0.000135) * Z95
        self.assertAlmostEqual(
            report.total_portfolio_var_95_usd, round(expected, 2), places=2
        )

    def test_standalone_volatility_is_invariant_to_capital(self):
        """
        Documents why a standalone breach is a gate rather than a capital problem:
        halving the strategy's capital leaves sigma_i unchanged, so the breach
        survives any reallocation this engine can recommend.
        """
        full = StrategySpecificVsSharedRiskBudgetEngine(
            base_specs()
        ).evaluate_risk_budgets(BASE_COV, ["STAT_ARB", "TREND"])
        halved_specs = [
            StrategyRiskBudgetSpec("STAT_ARB", 250_000.0, 15.0, 40.0),
            StrategyRiskBudgetSpec("TREND", 500_000.0, 25.0, 70.0),
        ]
        halved = StrategySpecificVsSharedRiskBudgetEngine(
            halved_specs
        ).evaluate_risk_budgets(BASE_COV, ["STAT_ARB", "TREND"])
        self.assertEqual(
            full.strategy_breakdown["TREND"].standalone_volatility_pct,
            halved.strategy_breakdown["TREND"].standalone_volatility_pct,
        )


class TestCovarianceValidation(unittest.TestCase):
    """A bad covariance matrix must raise, never be repaired into a plausible number."""

    def setUp(self):
        self.engine = StrategySpecificVsSharedRiskBudgetEngine(base_specs())

    def assert_rejected(self, cov):
        with self.assertRaises(ValueError):
            self.engine.evaluate_risk_budgets(cov, ["STAT_ARB", "TREND"])

    def test_wrong_shape_rejected(self):
        self.assert_rejected(np.array([[0.0001, 0.0, 0.0], [0.0, 0.0001, 0.0]]))

    def test_one_dimensional_rejected(self):
        self.assert_rejected(np.array([0.0001, 0.0004]))

    def test_nan_rejected(self):
        """NaN would propagate silently: 'NaN > limit' is False, so breaches vanish."""
        self.assert_rejected(np.array([[np.nan, 0.0], [0.0, 0.0004]]))

    def test_infinity_rejected(self):
        self.assert_rejected(np.array([[np.inf, 0.0], [0.0, 0.0004]]))

    def test_asymmetric_rejected(self):
        self.assert_rejected(np.array([[0.0001, 0.00005], [0.00001, 0.0004]]))

    def test_negative_variance_rejected(self):
        """sqrt of a negative diagonal is NaN, which silently passes every limit."""
        self.assert_rejected(np.array([[-0.0001, 0.0], [0.0, 0.0004]]))

    def test_indefinite_matrix_rejected(self):
        """An implied correlation above 1 must not become a plausible volatility."""
        self.assert_rejected(np.array([[0.0001, 0.01], [0.01, 0.0001]]))

    def test_perfectly_correlated_singular_matrix_rejected(self):
        """The final eigenvalue is ~1e-20 rather than exactly 0; a relative test is needed."""
        self.assert_rejected(np.array([[0.0001, 0.0002], [0.0002, 0.0004]]))

    def test_zero_variance_strategy_rejected(self):
        self.assert_rejected(np.array([[0.0, 0.0], [0.0, 0.0004]]))

    def test_non_numeric_rejected(self):
        self.assert_rejected([["a", "b"], ["c", "d"]])

    def test_all_zero_matrix_rejected(self):
        self.assert_rejected(np.zeros((2, 2)))

    def test_overflowing_total_capital_rejected(self):
        """
        Each spec is individually finite, but the sum can overflow to infinity,
        which would silently produce zero weights and a zero portfolio variance.
        """
        specs = [
            StrategyRiskBudgetSpec("A", 1e308, 100.0, 60.0),
            StrategyRiskBudgetSpec("B", 1e308, 100.0, 60.0),
        ]
        engine = StrategySpecificVsSharedRiskBudgetEngine(specs)
        # The overflow is the point of the test; silence numpy's warning about it.
        with np.errstate(over="ignore"), self.assertRaises(ValueError):
            engine.evaluate_risk_budgets(BASE_COV, ["A", "B"])

    def test_nested_list_accepted(self):
        report = self.engine.evaluate_risk_budgets(
            [[0.0001, 0.00002], [0.00002, 0.0004]], ["STAT_ARB", "TREND"]
        )
        self.assertTrue(report.is_euler_decomposition_valid)


class TestIdentifierValidation(unittest.TestCase):

    def setUp(self):
        self.engine = StrategySpecificVsSharedRiskBudgetEngine(base_specs())

    def test_omitting_a_registered_strategy_is_rejected(self):
        """Silently dropping it would under-report total portfolio risk."""
        with self.assertRaises(ValueError):
            self.engine.evaluate_risk_budgets(np.array([[0.0001]]), ["STAT_ARB"])

    def test_duplicate_ids_rejected(self):
        """A duplicate would double-count that strategy's capital."""
        with self.assertRaises(ValueError):
            self.engine.evaluate_risk_budgets(BASE_COV, ["STAT_ARB", "STAT_ARB"])

    def test_unknown_id_rejected(self):
        with self.assertRaises(ValueError):
            self.engine.evaluate_risk_budgets(BASE_COV, ["STAT_ARB", "GHOST"])

    def test_empty_order_rejected(self):
        with self.assertRaises(ValueError):
            self.engine.evaluate_risk_budgets(BASE_COV, [])


class TestSpecAndEngineValidation(unittest.TestCase):

    def test_non_positive_capital_rejected(self):
        for capital in (0.0, -1.0):
            with self.assertRaises(ValueError):
                StrategyRiskBudgetSpec("A", capital, 15.0, 40.0)

    def test_non_finite_capital_rejected(self):
        for capital in (float("nan"), float("inf")):
            with self.assertRaises(ValueError):
                StrategyRiskBudgetSpec("A", capital, 15.0, 40.0)

    def test_non_positive_limits_rejected(self):
        with self.assertRaises(ValueError):
            StrategyRiskBudgetSpec("A", 1_000.0, 0.0, 40.0)
        with self.assertRaises(ValueError):
            StrategyRiskBudgetSpec("A", 1_000.0, 15.0, -5.0)

    def test_empty_strategy_id_rejected(self):
        for bad_id in ("", "   "):
            with self.assertRaises(ValueError):
                StrategyRiskBudgetSpec(bad_id, 1_000.0, 15.0, 40.0)

    def test_empty_spec_list_rejected(self):
        with self.assertRaises(ValueError):
            StrategySpecificVsSharedRiskBudgetEngine([])

    def test_duplicate_specs_rejected(self):
        specs = [
            StrategyRiskBudgetSpec("A", 500_000.0, 15.0, 40.0),
            StrategyRiskBudgetSpec("A", 500_000.0, 25.0, 70.0),
        ]
        with self.assertRaises(ValueError):
            StrategySpecificVsSharedRiskBudgetEngine(specs)

    def test_invalid_z_score_rejected(self):
        for z in (0.0, -1.645, float("nan")):
            with self.assertRaises(ValueError):
                StrategySpecificVsSharedRiskBudgetEngine(base_specs(), confidence_level_z=z)

    def test_invalid_calendar_parameters_rejected(self):
        with self.assertRaises(ValueError):
            StrategySpecificVsSharedRiskBudgetEngine(
                base_specs(), trading_days_per_year=0
            )
        with self.assertRaises(ValueError):
            StrategySpecificVsSharedRiskBudgetEngine(base_specs(), var_horizon_days=0)

    def test_pre_annualized_covariance_escape_hatch(self):
        """
        With an already-annualized Sigma the caller must pass
        trading_days_per_year=1 and var_horizon_days=1, or every figure is
        annualized twice.
        """
        annual_cov = BASE_COV * TRADING_DAYS
        report = StrategySpecificVsSharedRiskBudgetEngine(
            base_specs(), trading_days_per_year=1, var_horizon_days=1
        ).evaluate_risk_budgets(annual_cov, ["STAT_ARB", "TREND"])
        daily_based = StrategySpecificVsSharedRiskBudgetEngine(
            base_specs()
        ).evaluate_risk_budgets(BASE_COV, ["STAT_ARB", "TREND"])
        self.assertAlmostEqual(
            report.total_portfolio_volatility_pct,
            daily_based.total_portfolio_volatility_pct,
            places=2,
        )

    def test_custom_trading_calendar_changes_annualization(self):
        report = StrategySpecificVsSharedRiskBudgetEngine(
            base_specs(), trading_days_per_year=365
        ).evaluate_risk_budgets(BASE_COV, ["STAT_ARB", "TREND"])
        self.assertAlmostEqual(
            report.total_portfolio_volatility_pct,
            round(math.sqrt(0.000135) * math.sqrt(365) * 100.0, 2),
            places=2,
        )


class TestReportShape(unittest.TestCase):

    def test_report_types(self):
        report = StrategySpecificVsSharedRiskBudgetEngine(
            base_specs()
        ).evaluate_risk_budgets(BASE_COV, ["STAT_ARB", "TREND"])
        self.assertIsInstance(report, PortfolioRiskBudgetAllocationReport)
        self.assertEqual(len(report.strategy_breakdown), 2)
        for breakdown in report.strategy_breakdown.values():
            self.assertIsInstance(breakdown, StrategyRiskBreakdown)
        self.assertIn("EULER RISK ALLOCATION", report.audit_notes)
        self.assertEqual(report.var_confidence_z, Z95)

    def test_breach_is_logged_at_warning_level(self):
        specs = [
            StrategyRiskBudgetSpec("STAT_ARB", 500_000.0, 5.0, 50.0),
            StrategyRiskBudgetSpec("TREND", 500_000.0, 100.0, 100.0),
        ]
        engine = StrategySpecificVsSharedRiskBudgetEngine(specs)
        with self.assertLogs(MODULE_LOGGER, level="WARNING") as captured:
            engine.evaluate_risk_budgets(BASE_COV, ["STAT_ARB", "TREND"])
        self.assertTrue(any("STAT_ARB" in line for line in captured.output))

    def test_clean_book_logs_at_info_level_only(self):
        specs = [
            StrategyRiskBudgetSpec("STAT_ARB", 500_000.0, 100.0, 100.0),
            StrategyRiskBudgetSpec("TREND", 500_000.0, 100.0, 100.0),
        ]
        engine = StrategySpecificVsSharedRiskBudgetEngine(specs)
        with self.assertLogs(MODULE_LOGGER, level="INFO") as captured:
            report = engine.evaluate_risk_budgets(BASE_COV, ["STAT_ARB", "TREND"])
        self.assertEqual(report.breached_strategies, [])
        self.assertTrue(all(line.startswith("INFO:") for line in captured.output))


if __name__ == "__main__":
    unittest.main()
