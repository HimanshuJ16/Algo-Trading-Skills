"""
Unit tests for risk-adjusted-performance-attribution-per-strategy.

Expected values are derived independently of the implementation -- by hand from the
published metric definitions, or from a closed-form construction -- rather than by
re-running the engine's own formula.
"""
import math
import unittest

from risk_adjusted_attribution import (
    RiskAdjustedPerformanceAttributionEngine,
    StrategyReturns,
    TRADING_DAYS_PER_YEAR,
)


class TestMetricCorrectness(unittest.TestCase):
    """Per-strategy metrics against hand-derived values."""

    def setUp(self):
        self.engine = RiskAdjustedPerformanceAttributionEngine(risk_free_rate_annual=0.05)

    def test_annualized_return_is_geometric(self):
        # 252 days of exactly +0.2% compounds to 1.002**252 - 1.
        strat = StrategyReturns("GEO", [0.002] * TRADING_DAYS_PER_YEAR)
        attr = self.engine.compute_strategy_attribution([strat]).strategy_attributions[0]
        self.assertAlmostEqual(attr.annualized_return, 1.002 ** 252 - 1.0, places=6)

    def test_total_return_compounds_and_is_not_the_arithmetic_sum(self):
        # -50% then +100% leaves the investor exactly flat: 0.5 * 2.0 = 1.0.
        # The arithmetic sum of the returns is +0.5, which nobody ever earned.
        strat = StrategyReturns("ROUNDTRIP", [-0.5, 1.0])
        attr = self.engine.compute_strategy_attribution([strat]).strategy_attributions[0]
        self.assertAlmostEqual(attr.total_return, 0.0, places=9)
        self.assertNotAlmostEqual(attr.total_return, sum([-0.5, 1.0]), places=3)

    def test_max_drawdown_hand_computed(self):
        # Equity: 1.10, 0.88, 0.924. Peak 1.10, trough 0.88 -> (1.10-0.88)/1.10 = 0.20.
        strat = StrategyReturns("DD", [0.10, -0.20, 0.05])
        attr = self.engine.compute_strategy_attribution([strat]).strategy_attributions[0]
        self.assertAlmostEqual(attr.max_drawdown, 0.20, places=6)

    def test_annualized_volatility_matches_closed_form(self):
        # Alternating +d/-d has sample mean 0 and sample variance n*d^2/(n-1).
        d, n = 0.01, 100
        strat = StrategyReturns("ALT", [d, -d] * (n // 2))
        attr = self.engine.compute_strategy_attribution([strat]).strategy_attributions[0]
        expected = math.sqrt(n * d * d / (n - 1)) * math.sqrt(TRADING_DAYS_PER_YEAR)
        self.assertAlmostEqual(attr.annualized_volatility, expected, places=6)

    def test_downside_deviation_averages_over_all_observations(self):
        """
        Sortino's denominator divides the summed squared shortfalls by the TOTAL number
        of observations, not by the count of losing periods (CFA Institute / Kidd 2012).

        With MAR = 0 and returns [0.10, 0.10, 0.10, -0.10]: one shortfall of -0.10,
        so sqrt(0.01 / 4) = 0.05 annualized by sqrt(252). Dividing by the single losing
        period instead would give 0.10 -- exactly double.
        """
        engine = RiskAdjustedPerformanceAttributionEngine(risk_free_rate_annual=0.0)
        dd = engine._downside_deviation([0.10, 0.10, 0.10, -0.10], 0.0)
        self.assertAlmostEqual(dd, 0.05 * math.sqrt(TRADING_DAYS_PER_YEAR), places=9)
        self.assertNotAlmostEqual(dd, 0.10 * math.sqrt(TRADING_DAYS_PER_YEAR), places=3)

    def test_risk_free_rate_deannualized_geometrically(self):
        # (1 + rf_daily)**252 must return exactly to the annual rate.
        daily = self.engine._daily_risk_free(0.05)
        self.assertAlmostEqual((1.0 + daily) ** TRADING_DAYS_PER_YEAR, 1.05, places=9)
        # The arithmetic shortcut rf/252 overstates the daily rate.
        self.assertLess(daily, 0.05 / TRADING_DAYS_PER_YEAR)

    def test_per_strategy_risk_free_override_is_honoured(self):
        """A rate set on the strategy must actually reach the ratios."""
        returns = [0.001] * 200 + [-0.001] * 52
        low = StrategyReturns("LOW_RF", returns, risk_free_rate_annual=0.00)
        high = StrategyReturns("HIGH_RF", returns, risk_free_rate_annual=0.20)
        rep = self.engine.compute_strategy_attribution([low, high], weights=[0.5, 0.5])
        low_attr, high_attr = rep.strategy_attributions
        # Same returns, different hurdle -> the higher hurdle must score strictly lower.
        self.assertLess(high_attr.sharpe_ratio, low_attr.sharpe_ratio)
        self.assertLess(high_attr.sortino_ratio, low_attr.sortino_ratio)


class TestUndefinedRatiosAreNotZero(unittest.TestCase):
    """
    Regression: an undefined ratio must be None, never 0.0.

    Reporting 0.0 inverted the ranking -- a strategy with a strong return and no
    drawdown scored below a mediocre one, which is exactly backwards for a tool used
    to allocate capital and retire strategies.
    """

    def setUp(self):
        self.engine = RiskAdjustedPerformanceAttributionEngine(risk_free_rate_annual=0.05)

    def test_flawless_strategy_reports_none_not_zero(self):
        strat = StrategyReturns("PERFECT", [0.002] * TRADING_DAYS_PER_YEAR)
        attr = self.engine.compute_strategy_attribution([strat]).strategy_attributions[0]

        self.assertGreater(attr.annualized_return, 0.60)   # ~65% annualized
        self.assertEqual(attr.max_drawdown, 0.0)
        self.assertIsNone(attr.sharpe_ratio)               # zero volatility
        self.assertIsNone(attr.sortino_ratio)              # never below the MAR
        self.assertIsNone(attr.calmar_ratio)               # no drawdown
        self.assertEqual(len(attr.undefined_ratios), 3)

    def test_flawless_strategy_does_not_rank_below_a_mediocre_one(self):
        """The ranking inversion itself, stated as a test."""
        perfect = StrategyReturns("PERFECT", [0.002] * 252)
        choppy = StrategyReturns("CHOPPY", [0.02, -0.019] * 126)
        rep = self.engine.compute_strategy_attribution([perfect, choppy], weights=[0.5, 0.5])
        p, c = rep.strategy_attributions

        self.assertGreater(p.annualized_return, c.annualized_return)
        self.assertIsNotNone(c.calmar_ratio)
        # The old sentinel made this comparison 0.0 > 4.27 -> False. None is not a
        # score, so a consumer must handle it explicitly instead of silently losing.
        self.assertIsNone(p.calmar_ratio)
        self.assertNotEqual(p.calmar_ratio, 0.0)

    def test_drawdown_strategy_still_produces_defined_ratios(self):
        returns = [0.01] * 20 + [-0.05] * 5 + [0.01] * 20
        strat = StrategyReturns("DD_TEST", returns)
        attr = self.engine.compute_strategy_attribution([strat]).strategy_attributions[0]
        self.assertGreater(attr.max_drawdown, 0.10)
        self.assertIsNotNone(attr.sharpe_ratio)
        self.assertIsNotNone(attr.sortino_ratio)
        self.assertIsNotNone(attr.calmar_ratio)
        self.assertEqual(attr.undefined_ratios, ())


class TestEulerRiskDecomposition(unittest.TestCase):
    """
    Risk contributions must follow the Euler decomposition
    CR_i / sigma_p = w_i * (Sigma w)_i / sigma_p^2, not a correlation-blind share of
    weighted volatilities.
    """

    def setUp(self):
        self.engine = RiskAdjustedPerformanceAttributionEngine(risk_free_rate_annual=0.05)
        self.base = [0.02, -0.015, 0.018, -0.012, 0.01] * 50

    def test_hedge_receives_negative_risk_contribution(self):
        """
        Hand derivation. Let B = -0.5 * A, w = [0.5, 0.5], v = Var(A). Then
        Cov = [[v, -0.5v], [-0.5v, 0.25v]] and

            sigma_p^2 = 0.25v - 0.25v + 0.0625v = 0.0625v
            (Sigma w)_A = 0.5v - 0.25v  =  0.25v  -> CR_A = 0.5*0.25v/0.0625v = +200%
            (Sigma w)_B = -0.25v + 0.125v = -0.125v -> CR_B = 0.5*-0.125v/0.0625v = -100%

        The naive weighted-volatility share instead reports +66.67% / +33.33%, hiding
        the hedge entirely and overstating the portfolio's true risk concentration.
        """
        long_leg = StrategyReturns("LONG", self.base)
        hedge = StrategyReturns("HEDGE", [-0.5 * r for r in self.base])
        rep = self.engine.compute_strategy_attribution([long_leg, hedge], weights=[0.5, 0.5])
        a, b = rep.strategy_attributions

        self.assertAlmostEqual(a.risk_contribution_pct, 200.0, places=2)
        self.assertAlmostEqual(b.risk_contribution_pct, -100.0, places=2)
        # The retained naive measure is the old (wrong-for-attribution) number.
        self.assertAlmostEqual(a.standalone_volatility_share_pct, 66.67, places=2)
        self.assertAlmostEqual(b.standalone_volatility_share_pct, 33.33, places=2)

    def test_contributions_sum_to_one_hundred_percent(self):
        """Euler's theorem: the component contributions must exhaust portfolio risk."""
        strategies = [
            StrategyReturns("A", [0.010, -0.008, 0.012, -0.004, 0.006] * 50),
            StrategyReturns("B", [-0.003, 0.011, 0.002, -0.009, 0.005] * 50),
            StrategyReturns("C", [0.007, 0.001, -0.013, 0.004, -0.002] * 50),
        ]
        rep = self.engine.compute_strategy_attribution(strategies, weights=[0.2, 0.3, 0.5])
        total = sum(a.risk_contribution_pct for a in rep.strategy_attributions)
        self.assertAlmostEqual(total, 100.0, places=1)

    def test_identical_strategies_split_risk_equally(self):
        """Two identical, equally weighted strategies must each contribute half."""
        s1 = StrategyReturns("CLONE_A", self.base)
        s2 = StrategyReturns("CLONE_B", list(self.base))
        rep = self.engine.compute_strategy_attribution([s1, s2], weights=[0.5, 0.5])
        for attr in rep.strategy_attributions:
            self.assertAlmostEqual(attr.risk_contribution_pct, 50.0, places=2)

    def test_higher_volatility_strategy_contributes_more_risk(self):
        high = StrategyReturns("HIGH_VOL", [0.02, -0.015, 0.018, -0.012, 0.01] * 50)
        low = StrategyReturns("LOW_VOL", [0.001, 0.0005, 0.0012, 0.0008, 0.0006] * 50)
        rep = self.engine.compute_strategy_attribution([high, low], weights=[0.5, 0.5])
        h, l = rep.strategy_attributions
        self.assertGreater(h.annualized_volatility, l.annualized_volatility)
        self.assertGreater(h.risk_contribution_pct, l.risk_contribution_pct)

    def test_perfect_hedge_makes_decomposition_undefined(self):
        """Zero portfolio volatility: contributions are 0/0, so None, not 50/50."""
        s1 = StrategyReturns("LONG", self.base)
        s2 = StrategyReturns("PERFECT_HEDGE", [-r for r in self.base])
        rep = self.engine.compute_strategy_attribution([s1, s2], weights=[0.5, 0.5])

        self.assertFalse(rep.risk_decomposition_available)
        self.assertAlmostEqual(rep.total_portfolio_volatility, 0.0, places=9)
        for attr in rep.strategy_attributions:
            self.assertIsNone(attr.risk_contribution_pct)
        self.assertIn("undefined", rep.audit_notes.lower())


class TestInputValidation(unittest.TestCase):
    """Malformed input must raise a specific error, not crash deep in a stdlib call."""

    def setUp(self):
        self.engine = RiskAdjustedPerformanceAttributionEngine(risk_free_rate_annual=0.05)

    def test_empty_strategy_list_raises(self):
        with self.assertRaises(ValueError):
            self.engine.compute_strategy_attribution([])

    def test_single_observation_raises(self):
        with self.assertRaises(ValueError):
            self.engine.compute_strategy_attribution([StrategyReturns("X", [0.01])])

    def test_nan_return_raises(self):
        strat = StrategyReturns("NAN", [0.01, float("nan"), 0.01])
        with self.assertRaises(ValueError) as ctx:
            self.engine.compute_strategy_attribution([strat])
        self.assertIn("non-finite", str(ctx.exception))

    def test_infinite_return_raises(self):
        strat = StrategyReturns("INF", [0.01, float("inf"), 0.01])
        with self.assertRaises(ValueError):
            self.engine.compute_strategy_attribution([strat])

    def test_total_loss_return_raises(self):
        """r = -1.0 wipes equity to zero; below that drives it negative."""
        with self.assertRaises(ValueError):
            self.engine.compute_strategy_attribution([StrategyReturns("WIPE", [0.01, -1.0])])

    def test_return_below_minus_one_raises_instead_of_complex_number(self):
        """
        Regression: compounding a negative equity to an annualized figure raised a
        negative base to a fractional power, producing a complex number and a
        TypeError from round(); it also yielded a max drawdown above 100%.
        """
        strat = StrategyReturns("IMPOSSIBLE", [0.01] * 9 + [-1.5])
        with self.assertRaises(ValueError) as ctx:
            self.engine.compute_strategy_attribution([strat])
        self.assertIn("-1.0", str(ctx.exception))

    def test_non_numeric_return_raises_type_error(self):
        with self.assertRaises(TypeError):
            self.engine.compute_strategy_attribution([StrategyReturns("BAD", [0.01, "0.02"])])

    def test_ragged_series_raises_instead_of_truncating(self):
        """
        Regression: unequal lengths were silently truncated to the shortest series for
        the portfolio while per-strategy metrics used the full series, so one report
        mixed two different horizons.
        """
        long_s = StrategyReturns("LONG", [0.001] * 252)
        short_s = StrategyReturns("SHORT", [0.001] * 10)
        with self.assertRaises(ValueError) as ctx:
            self.engine.compute_strategy_attribution([long_s, short_s])
        self.assertIn("same number of return observations", str(ctx.exception))

    def test_weights_length_mismatch_raises(self):
        strat = StrategyReturns("A", [0.01] * 10)
        with self.assertRaises(ValueError):
            self.engine.compute_strategy_attribution([strat], weights=[0.5, 0.5])

    def test_weights_not_summing_to_one_raises(self):
        strategies = [StrategyReturns("A", [0.01] * 10), StrategyReturns("B", [0.02] * 10)]
        with self.assertRaises(ValueError) as ctx:
            self.engine.compute_strategy_attribution(strategies, weights=[0.5, 0.9])
        self.assertIn("sum to 1.0", str(ctx.exception))

    def test_invalid_engine_risk_free_rate_raises(self):
        with self.assertRaises(ValueError):
            RiskAdjustedPerformanceAttributionEngine(risk_free_rate_annual=-1.5)
        with self.assertRaises(ValueError):
            RiskAdjustedPerformanceAttributionEngine(risk_free_rate_annual=float("nan"))


class TestReportSemantics(unittest.TestCase):

    def setUp(self):
        self.engine = RiskAdjustedPerformanceAttributionEngine(risk_free_rate_annual=0.05)

    def test_equal_weight_default_and_status(self):
        strategies = [
            StrategyReturns("A", [0.003, -0.001, 0.002] * 84),
            StrategyReturns("B", [0.001, 0.002, -0.002] * 84),
        ]
        rep = self.engine.compute_strategy_attribution(strategies)
        self.assertEqual(rep.status, "ATTRIBUTION_COMPLETE")
        self.assertEqual(rep.observations, 252)
        self.assertEqual(len(rep.strategy_attributions), 2)
        self.assertFalse(rep.insufficient_history_warning)

    def test_short_sample_is_flagged_not_silently_annualized(self):
        strat = StrategyReturns("SHORT", [0.01, -0.005, 0.008, 0.002, -0.001])
        rep = self.engine.compute_strategy_attribution([strat])
        self.assertTrue(rep.insufficient_history_warning)
        self.assertEqual(rep.observations, 5)
        self.assertIn("WARNING", rep.audit_notes)

    def test_portfolio_return_reflects_weights(self):
        """A 100%/0% weighting must reproduce the funded strategy's own return."""
        a = StrategyReturns("A", [0.004, -0.001, 0.003] * 84)
        b = StrategyReturns("B", [-0.010, 0.020, -0.015] * 84)
        rep = self.engine.compute_strategy_attribution([a, b], weights=[1.0, 0.0])
        self.assertAlmostEqual(
            rep.total_portfolio_return,
            rep.strategy_attributions[0].annualized_return,
            places=6,
        )

    def test_diversification_reduces_portfolio_volatility(self):
        """Blending two offsetting strategies must not exceed the higher standalone vol."""
        a = StrategyReturns("A", [0.02, -0.015, 0.018, -0.012, 0.01] * 50)
        b = StrategyReturns("B", [-0.018, 0.014, -0.016, 0.011, -0.009] * 50)
        rep = self.engine.compute_strategy_attribution([a, b], weights=[0.5, 0.5])
        standalone = max(x.annualized_volatility for x in rep.strategy_attributions)
        self.assertLess(rep.total_portfolio_volatility, standalone)


if __name__ == "__main__":
    unittest.main()
