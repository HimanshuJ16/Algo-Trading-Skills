import math
import unittest

import numpy as np

from tearsheet_generator import StandardizedTearsheetGenerator, TearsheetError


class TestStandardizedTearsheetGenerator(unittest.TestCase):
    def setUp(self):
        self.generator = StandardizedTearsheetGenerator(risk_free_rate=0.0, periods_per_year=252)

    # ----------------------------------------------------------------- baseline

    def test_generate_tearsheet(self):
        returns = np.array([0.01, 0.02, -0.01, -0.02, 0.03, 0.01, -0.01])
        report = self.generator.generate(returns)

        self.assertIn("Total Return", report)
        self.assertIn("Sharpe Ratio", report)
        self.assertIn("Max Drawdown", report)
        self.assertIn("Hit Rate", report)
        self.assertIn("Profit Factor", report)

        self.assertAlmostEqual(report["Hit Rate"], 4/7)
        self.assertTrue(report["Max Drawdown"] <= 0)

    def test_empty_returns(self):
        report = self.generator.generate(np.array([]))
        self.assertEqual(report, {})

    def test_accepts_a_plain_list(self):
        report = self.generator.generate([0.01, -0.01, 0.02])
        self.assertIn("Sharpe Ratio", report)

    # ------------------------------------------------- drawdown (regression, critical)

    def test_drawdown_from_inception_is_captured(self):
        """Regression: the equity curve must be seeded at 1.0.

        Without the seed the running maximum starts at the first period's close, so a
        decline beginning on period one is invisible. This series previously reported a
        maximum drawdown of 0.0 despite losing half the account on the first period.
        """
        report = self.generator.generate(np.array([-0.50, 0.10, 0.05]))
        self.assertAlmostEqual(report["Max Drawdown"], -0.50)

    def test_monotonic_decline_drawdown_equals_total_loss(self):
        """Three -10% periods compound to 0.9^3 = 0.729, so the drawdown is -27.1%.

        The unseeded curve previously reported -19%.
        """
        report = self.generator.generate(np.array([-0.10, -0.10, -0.10]))
        self.assertAlmostEqual(report["Max Drawdown"], 0.9 ** 3 - 1.0)
        self.assertAlmostEqual(report["Max Drawdown"], report["Total Return"])

    def test_drawdown_duration_and_recovery_hand_computed(self):
        """Equity 1.00 -> 1.25 -> 1.00 -> 1.25 (exact binary fractions).

        Peak at index 1, trough at index 2, so duration is 1 period. Equity regains the
        1.25 peak one period after the trough, so recovery is 1 period.
        """
        report = self.generator.generate(np.array([0.25, -0.20, 0.25]))
        self.assertAlmostEqual(report["Max Drawdown"], -0.20)
        self.assertEqual(report["Max Drawdown Duration"], 1)
        self.assertEqual(report["Max Drawdown Recovery Periods"], 1)

    def test_unrecovered_drawdown_reports_no_recovery(self):
        report = self.generator.generate(np.array([0.25, -0.20, 0.01]))
        self.assertIsNone(report["Max Drawdown Recovery Periods"])

    def test_drawdown_can_never_be_worse_than_total_loss(self):
        report = self.generator.generate(np.array([-1.0, 0.0, 0.0]))
        self.assertAlmostEqual(report["Max Drawdown"], -1.0)
        self.assertAlmostEqual(report["Annualized Return"], -1.0)

    def test_no_drawdown_reports_zero(self):
        report = self.generator.generate(np.array([0.01] * 10))
        self.assertEqual(report["Max Drawdown"], 0.0)
        self.assertEqual(report["Max Drawdown Duration"], 0)

    # ------------------------------------------------------ ratios, verified by hand

    def test_sharpe_matches_closed_form(self):
        """Returns [a, 0, a, 0] with ppy=4 and rf=0 give a Sharpe of exactly sqrt(3).

        Mean is a/2, so the annualized numerator is 2a. The ddof=1 deviations are
        +/-a/2, giving sum of squares 4*(a/2)^2 = a^2, divided by 3, square-rooted, then
        multiplied by sqrt(4): the denominator is 2a/sqrt(3). The ratio is sqrt(3)
        independent of a.
        """
        gen = StandardizedTearsheetGenerator(risk_free_rate=0.0, periods_per_year=4)
        report = gen.generate(np.array([0.02, 0.00, 0.02, 0.00]))
        self.assertAlmostEqual(report["Sharpe Ratio"], math.sqrt(3.0), places=10)

    def test_sortino_divides_downside_deviation_by_all_periods(self):
        """Returns [0.02, 0, 0.02, -0.02], ppy=4, rf=0 give a Sortino of exactly 1.0.

        Mean 0.005 annualizes to 0.02. Target downside deviation is
        sqrt( (0+0+0+0.02^2) / 4 ) = 0.01, annualized to 0.02 -- so the ratio is 1.0.
        Dividing by the count of below-target periods (1) instead of all 4 would give
        0.5, so this test discriminates the two conventions.
        """
        gen = StandardizedTearsheetGenerator(risk_free_rate=0.0, periods_per_year=4)
        report = gen.generate(np.array([0.02, 0.00, 0.02, -0.02]))
        self.assertAlmostEqual(report["Sortino Ratio"], 1.0, places=10)

    def test_calmar_default_convention_excludes_the_risk_free_rate(self):
        """Equity 1.00 -> 1.25 -> 1.00 -> 1.25 with ppy=3: CAGR 0.25, maxDD -0.20.

        Default Calmar is 0.25 / 0.20 = 1.25, and must not move when rf changes.
        """
        returns = np.array([0.25, -0.20, 0.25])
        for rf in (0.0, 0.05):
            gen = StandardizedTearsheetGenerator(risk_free_rate=rf, periods_per_year=3)
            report = gen.generate(returns)
            self.assertAlmostEqual(report["Annualized Return"], 0.25)
            self.assertAlmostEqual(report["Calmar Ratio"], 1.25, places=10)
            self.assertEqual(report["Calmar Convention"], "CAGR / |maxDD|")

    def test_calmar_excess_variant_is_opt_in_and_labelled(self):
        """(0.25 - 0.05) / 0.20 = 1.0 exactly."""
        gen = StandardizedTearsheetGenerator(
            risk_free_rate=0.05, periods_per_year=3, calmar_uses_excess_return=True
        )
        report = gen.generate(np.array([0.25, -0.20, 0.25]))
        self.assertAlmostEqual(report["Calmar Ratio"], 1.0, places=10)
        self.assertEqual(report["Calmar Convention"], "(CAGR - rf) / |maxDD|")

    def test_risk_free_rate_reduces_sharpe_by_the_annual_rate(self):
        """Subtracting rf shifts only the numerator, by exactly rf / annualized vol."""
        returns = np.array([0.02, 0.00, 0.02, 0.00])
        base = StandardizedTearsheetGenerator(risk_free_rate=0.0, periods_per_year=4).generate(returns)
        with_rf = StandardizedTearsheetGenerator(risk_free_rate=0.01, periods_per_year=4).generate(returns)
        self.assertAlmostEqual(base["Annualized Volatility"], with_rf["Annualized Volatility"])
        self.assertAlmostEqual(
            base["Sharpe Ratio"] - with_rf["Sharpe Ratio"], 0.01 / base["Annualized Volatility"]
        )

    # ------------------------------------------- degenerate denominators (regression)

    def test_zero_volatility_gives_infinite_not_zero_sharpe(self):
        """Regression: a flawless curve used to report Sharpe 0.0 and Calmar 0.0."""
        report = self.generator.generate(np.array([0.01] * 10))
        self.assertEqual(report["Sharpe Ratio"], math.inf)
        self.assertEqual(report["Sortino Ratio"], math.inf)
        self.assertEqual(report["Calmar Ratio"], math.inf)

    def test_constant_losses_give_negative_infinite_sharpe(self):
        report = self.generator.generate(np.array([-0.01] * 10))
        self.assertEqual(report["Sharpe Ratio"], -math.inf)
        self.assertLess(report["Calmar Ratio"], 0)

    def test_all_zero_returns_give_nan_not_infinite_profit_factor(self):
        """Regression: gross profit 0 over gross loss 0 used to report inf."""
        report = self.generator.generate(np.zeros(10))
        self.assertTrue(math.isnan(report["Profit Factor"]))
        self.assertTrue(math.isnan(report["Sharpe Ratio"]))

    def test_profit_factor_is_infinite_only_with_real_profits_and_no_losses(self):
        report = self.generator.generate(np.array([0.01, 0.02, 0.0]))
        self.assertEqual(report["Profit Factor"], math.inf)

    def test_profit_factor_hand_computed(self):
        """Gross profit 0.06 over gross loss 0.02 is exactly 3.0."""
        report = self.generator.generate(np.array([0.04, 0.02, -0.02]))
        self.assertAlmostEqual(report["Profit Factor"], 3.0, places=10)

    # --------------------------------------------------------------- reporting hygiene

    def test_hit_rate_counts_flat_periods_as_non_wins(self):
        report = self.generator.generate(np.array([0.01, 0.0, 0.0, 0.0]))
        self.assertAlmostEqual(report["Hit Rate"], 0.25)

    def test_short_series_is_flagged_as_extrapolated(self):
        short = self.generator.generate(np.array([0.01, 0.02, -0.01]))
        self.assertTrue(short["Annualization Extrapolated"])
        self.assertEqual(short["Periods"], 3)

        full_year = self.generator.generate(np.full(252, 0.001))
        self.assertFalse(full_year["Annualization Extrapolated"])

    # --------------------------------------------------------------- input validation

    def test_non_finite_returns_are_rejected(self):
        """Regression: NaN used to yield a half-nan, half-plausible report."""
        with self.assertRaises(TearsheetError):
            self.generator.generate(np.array([0.01, np.nan, 0.02]))
        with self.assertRaises(TearsheetError):
            self.generator.generate(np.array([0.01, np.inf, 0.02]))

    def test_return_below_minus_one_is_rejected(self):
        """Regression: a -150% return drove equity negative and reported maxDD -1.51."""
        with self.assertRaises(TearsheetError):
            self.generator.generate(np.array([0.05, -1.5, 0.02]))

    def test_single_observation_is_rejected(self):
        """Regression: one +5% period was annualized to over 20,000,000%."""
        with self.assertRaises(TearsheetError):
            self.generator.generate(np.array([0.05]))

    def test_wrong_shape_is_rejected(self):
        with self.assertRaises(TearsheetError):
            self.generator.generate(np.array([[0.01, 0.02], [0.03, 0.04]]))
        with self.assertRaises(TearsheetError):
            self.generator.generate(0.01)

    def test_non_numeric_returns_are_rejected(self):
        with self.assertRaises(TearsheetError):
            self.generator.generate(["a", "b"])

    def test_invalid_configuration_is_rejected(self):
        with self.assertRaises(TearsheetError):
            StandardizedTearsheetGenerator(periods_per_year=0)
        with self.assertRaises(TearsheetError):
            StandardizedTearsheetGenerator(periods_per_year=-252)
        with self.assertRaises(TearsheetError):
            StandardizedTearsheetGenerator(periods_per_year=252.5)
        with self.assertRaises(TearsheetError):
            StandardizedTearsheetGenerator(risk_free_rate=float("nan"))

    def test_generate_does_not_mutate_input(self):
        returns = np.array([0.01, -0.02, 0.03])
        snapshot = returns.copy()
        self.generator.generate(returns)
        np.testing.assert_array_equal(returns, snapshot)


if __name__ == '__main__':
    unittest.main()
