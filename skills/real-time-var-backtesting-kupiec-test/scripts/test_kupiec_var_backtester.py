"""
Unit tests for the Kupiec POF backtester.

Expected values are taken from published sources, not recomputed with the module's own
formula:

- BCBS, "Supervisory framework for the use of 'backtesting' in conjunction with the
  internal models approach to market risk capital requirements", January 1996
  (bcbs22), Table 2 -- cumulative binomial probabilities and zone boundaries.
- Basel Framework MAR32.9 Table 1 -- backtesting-dependent multipliers.
- Campbell, "A Review of Backtesting and Backtesting Procedures", Federal Reserve
  FEDS 2005-21, Sec. 3.1 -- LR_POF = 0.76 at (T=250, x=4) and 12.95 at (T=250, x=10).
- The chi-square(1) 5% critical value 3.841459, whose survival probability is 0.05 by
  definition.
"""
import math
import unittest

from kupiec_var_backtester import (
    CHI2_1DF_CRITICAL_VALUE_5PCT,
    KupiecVaRBacktester,
    basel_zone_boundaries,
    binomial_cdf,
    chi_square_1df_survival,
    kupiec_pof_statistic,
)


class TestChiSquareSurvival(unittest.TestCase):
    """The p-value transform is where the previous revision was materially wrong."""

    def test_five_percent_critical_value_returns_exactly_five_percent(self):
        self.assertAlmostEqual(
            chi_square_1df_survival(CHI2_1DF_CRITICAL_VALUE_5PCT), 0.05, places=12
        )

    def test_zero_statistic_gives_unit_p_value(self):
        self.assertEqual(chi_square_1df_survival(0.0), 1.0)

    def test_one_percent_critical_value(self):
        # chi-square(1) 1% critical value, 6.634897.
        self.assertAlmostEqual(chi_square_1df_survival(6.634896601021213), 0.01, places=12)

    def test_regression_not_the_chi_square_two_df_formula(self):
        """
        The old implementation used exp(-s/2), the chi-square(2) survival function. At
        the 5% critical value that returns 0.1465 instead of 0.05 -- a p-value roughly
        3x too large, which reported a rejected model as merely "borderline".
        """
        wrong = math.exp(-CHI2_1DF_CRITICAL_VALUE_5PCT / 2.0)
        self.assertAlmostEqual(wrong, 0.1465, places=4)
        self.assertNotAlmostEqual(
            chi_square_1df_survival(CHI2_1DF_CRITICAL_VALUE_5PCT), wrong, places=3
        )

    def test_negative_statistic_rejected(self):
        with self.assertRaises(ValueError):
            chi_square_1df_survival(-0.1)


class TestBinomialCdf(unittest.TestCase):
    """Reproduce BCBS bcbs22 Table 2 cumulative probabilities (250 obs, 99% coverage)."""

    # Published percentages, exceptions 0 through 10.
    BCBS_TABLE_2 = [8.11, 28.58, 54.32, 75.81, 89.22, 95.88, 98.63, 99.60, 99.89,
                    99.97, 99.99]

    def test_matches_published_table(self):
        for exceptions, published_pct in enumerate(self.BCBS_TABLE_2):
            with self.subTest(exceptions=exceptions):
                computed_pct = binomial_cdf(exceptions, 250, 0.01) * 100.0
                self.assertAlmostEqual(computed_pct, published_pct, places=2)

    def test_full_range_sums_to_one(self):
        self.assertAlmostEqual(binomial_cdf(250, 250, 0.01), 1.0, places=12)

    def test_negative_k_is_zero_probability(self):
        self.assertEqual(binomial_cdf(-1, 250, 0.01), 0.0)

    def test_large_sample_does_not_overflow(self):
        # A naive math.comb(50000, k) * p**k formulation overflows or underflows here.
        value = binomial_cdf(500, 50000, 0.01)
        self.assertTrue(0.0 < value < 1.0)
        self.assertTrue(math.isfinite(value))

    def test_invalid_probability_rejected(self):
        with self.assertRaises(ValueError):
            binomial_cdf(5, 250, 1.5)


class TestKupiecStatistic(unittest.TestCase):
    """Values published in Campbell, FEDS 2005-21, Sec. 3.1."""

    def test_four_exceptions_in_250_matches_published_0_76(self):
        # The paper prints 0.76; compare within that published rounding precision.
        self.assertAlmostEqual(kupiec_pof_statistic(250, 4, 0.01), 0.76, delta=0.01)

    def test_ten_exceptions_in_250_matches_published_12_95(self):
        # The paper prints 12.95; compare within that published rounding precision.
        self.assertAlmostEqual(kupiec_pof_statistic(250, 10, 0.01), 12.95, delta=0.01)

    def test_perfect_calibration_gives_zero_statistic(self):
        # x/T == p exactly, so restricted and unrestricted likelihoods coincide.
        self.assertAlmostEqual(kupiec_pof_statistic(1000, 10, 0.01), 0.0, places=10)

    def test_binary_float_expected_rate_still_reads_as_aligned(self):
        """
        Regression: 1.0 - 0.99 == 0.010000000000000009, so an exact `observed == p`
        comparison labelled a perfectly calibrated 10-in-1000 model as overstating risk.
        """
        res = KupiecVaRBacktester(confidence_level=0.99).run_test(1000, 10)
        self.assertEqual(res.breach_direction, "aligned")

    def test_zero_exceptions_uses_analytic_limit(self):
        # -2 * T * ln(1 - p) = -2 * 250 * ln(0.99).
        self.assertAlmostEqual(
            kupiec_pof_statistic(250, 0, 0.01), -2.0 * 250 * math.log(0.99), places=10
        )

    def test_all_observations_are_exceptions_uses_analytic_limit(self):
        self.assertAlmostEqual(
            kupiec_pof_statistic(250, 250, 0.01), -2.0 * 250 * math.log(0.01), places=10
        )

    def test_statistic_never_negative(self):
        for exceptions in range(0, 60):
            with self.subTest(exceptions=exceptions):
                self.assertGreaterEqual(kupiec_pof_statistic(250, exceptions, 0.01), 0.0)

    def test_no_underflow_at_small_p_and_large_x(self):
        """
        The naive product form computes p**x = 0.01**40 ~ 1e-80 and (x/T)**x, both of
        which lose precision; the log-space form stays exact and finite.
        """
        value = kupiec_pof_statistic(1000, 40, 0.01)
        self.assertTrue(math.isfinite(value))
        self.assertGreater(value, 3.841459)


class TestBaselZoneBoundaries(unittest.TestCase):
    def test_reproduces_published_250_day_boundaries(self):
        # bcbs22 Table 2: green 0-4, yellow/amber 5-9, red 10 or more.
        self.assertEqual(basel_zone_boundaries(250, 0.01), (5, 10))

    def test_boundaries_are_not_linear_in_sample_size(self):
        """
        Guards against the linear-rescaling shortcut (x * 250 / T). At T = 1000 that
        shortcut would put the amber boundary at 20 exceptions; the BCBS binomial rule
        puts it at 15.
        """
        amber_start, red_start = basel_zone_boundaries(1000, 0.01)
        self.assertEqual((amber_start, red_start), (15, 24))
        self.assertNotEqual(amber_start, 5 * 4)

    def test_degenerate_window_never_puts_a_zone_at_zero_exceptions(self):
        """
        Regression: at T = 1, P(X <= 0) = 0.99 clears the 95% rule, so the raw boundary
        computation made zero breaches "amber". The zones penalise excess breaches; a
        clean window must never be labelled amber.
        """
        for total in (1, 2, 5, 20):
            with self.subTest(total_observations=total):
                amber_start, red_start = basel_zone_boundaries(total, 0.01)
                self.assertGreaterEqual(amber_start, 1)
                self.assertGreaterEqual(red_start, amber_start)
                zone = KupiecVaRBacktester(0.99).run_test(total, 0).basel_zone
                self.assertEqual(zone, "green")

    def test_guard_does_not_disturb_the_published_basis(self):
        self.assertEqual(basel_zone_boundaries(250, 0.01), (5, 10))
        self.assertEqual(basel_zone_boundaries(250, 0.05), (18, 27))


class TestKupiecVaRBacktester(unittest.TestCase):
    def setUp(self):
        self.tester = KupiecVaRBacktester(confidence_level=0.99)

    # --- Kupiec test outcomes -------------------------------------------------

    def test_perfectly_calibrated_model_accepted(self):
        res = self.tester.run_test(1000, 10)
        self.assertFalse(res.is_rejected)
        self.assertEqual(res.exceptions, 10)
        self.assertAlmostEqual(res.stat, 0.0, places=10)
        self.assertAlmostEqual(res.p_value, 1.0, places=6)
        self.assertEqual(res.breach_direction, "aligned")

    def test_grossly_undercalibrated_model_rejected(self):
        res = self.tester.run_test(1000, 25)
        self.assertTrue(res.is_rejected)
        self.assertLess(res.p_value, 0.05)
        self.assertEqual(res.breach_direction, "under_estimating_risk")
        self.assertEqual(res.basel_zone, "red")

    def test_statistic_is_populated_not_left_at_zero(self):
        """
        Regression: the previous SciPy code path never set ``stat``, so every result
        reported a Kupiec likelihood ratio of exactly 0.0 regardless of the breach count.
        """
        res = self.tester.run_test(250, 10)
        self.assertGreater(res.stat, 12.0)
        self.assertAlmostEqual(res.stat, 12.95, delta=0.01)

    def test_p_value_is_consistent_with_rejection_flag(self):
        """
        Regression: the old fallback rejected on LR > 3.841459 while reporting
        exp(-LR/2) as the p-value, so a rejected model could carry a reported p-value
        of 0.064 -- above the 5% level it was supposedly rejected at.
        """
        res = self.tester.run_test(250, 7)
        self.assertTrue(res.is_rejected)
        self.assertLess(res.p_value, 0.05)
        self.assertGreater(res.stat, CHI2_1DF_CRITICAL_VALUE_5PCT)

    def test_two_sided_rejection_on_too_few_breaches(self):
        """
        Zero breaches in 250 days rejects the POF null (the model is far too
        conservative) but is still Basel green, since the supervisory zones only
        penalise excess breaches. Both facts must be reported.
        """
        res = self.tester.run_test(250, 0)
        self.assertTrue(res.is_rejected)
        self.assertEqual(res.breach_direction, "over_estimating_risk")
        self.assertEqual(res.basel_zone, "green")
        self.assertIn("too FEW breaches", res.notes)

    def test_kupiec_and_basel_may_disagree_at_six_exceptions(self):
        """
        T = 250, x = 6: Kupiec does not reject (p = 0.0594 > 0.05) but Basel is already
        amber. Pinning this prevents a future change from collapsing the two verdicts
        into one.
        """
        res = self.tester.run_test(250, 6)
        self.assertFalse(res.is_rejected)
        self.assertAlmostEqual(res.p_value, 0.0594, places=4)
        self.assertEqual(res.basel_zone, "amber")

    # --- Basel zone classification --------------------------------------------

    def test_published_250_day_zone_and_multiplier_table(self):
        # MAR32.9 Table 1.
        expected = {
            0: ("green", 1.50), 1: ("green", 1.50), 2: ("green", 1.50),
            3: ("green", 1.50), 4: ("green", 1.50),
            5: ("amber", 1.70), 6: ("amber", 1.76), 7: ("amber", 1.83),
            8: ("amber", 1.88), 9: ("amber", 1.92),
            10: ("red", 2.00), 15: ("red", 2.00),
        }
        for exceptions, (zone, multiplier) in expected.items():
            with self.subTest(exceptions=exceptions):
                res = self.tester.run_test(250, exceptions)
                self.assertEqual(res.basel_zone, zone)
                self.assertAlmostEqual(res.basel_backtesting_multiplier, multiplier)

    def test_multiplier_withheld_off_the_published_basis(self):
        """
        BCBS generalises the zone boundaries to other sample sizes but publishes no
        multiplier steps for them, so the multiplier must be None rather than guessed.
        """
        res = self.tester.run_test(500, 12)
        self.assertIsNone(res.basel_backtesting_multiplier)
        self.assertEqual(res.basel_zone, "amber")
        self.assertIn("not applicable", res.notes)

    def test_multiplier_withheld_at_non_99_percent_coverage(self):
        res = KupiecVaRBacktester(confidence_level=0.975).run_test(250, 12)
        self.assertIsNone(res.basel_backtesting_multiplier)

    def test_cumulative_probability_matches_published_value(self):
        res = self.tester.run_test(250, 5)
        self.assertAlmostEqual(res.basel_cumulative_probability * 100.0, 95.88, places=2)

    # --- Reported context fields ----------------------------------------------

    def test_expected_exceptions_and_rates(self):
        res = self.tester.run_test(750, 15)
        self.assertAlmostEqual(res.expected_exception_rate, 0.01)
        self.assertAlmostEqual(res.expected_exceptions, 7.5)
        self.assertAlmostEqual(res.observed_exception_rate, 0.02)
        self.assertEqual(res.total_observations, 750)

    def test_below_basel_minimum_is_flagged(self):
        with self.assertLogs("kupiec_var_backtester", level="WARNING") as captured:
            res = self.tester.run_test(120, 2)
        self.assertFalse(res.meets_basel_minimum_observations)
        self.assertIn("below the Basel", res.notes)
        self.assertIn("below the Basel minimum", captured.output[0])

    def test_at_basel_minimum_is_not_flagged(self):
        res = self.tester.run_test(250, 2)
        self.assertTrue(res.meets_basel_minimum_observations)

    def test_ninety_five_percent_var_uses_five_percent_expected_rate(self):
        res = KupiecVaRBacktester(confidence_level=0.95).run_test(250, 13)
        self.assertAlmostEqual(res.expected_exception_rate, 0.05)
        self.assertAlmostEqual(res.expected_exceptions, 12.5)
        self.assertFalse(res.is_rejected)

    # --- Input validation ------------------------------------------------------

    def test_empty_window_raises_instead_of_reporting_a_valid_model(self):
        """
        Regression: T <= 0 previously returned KupiecResult(1.0, False, 0, 0.0), so a
        feed outage that produced no observations was reported as a validated VaR model.
        """
        for bad in (0, -1, -250):
            with self.subTest(total_observations=bad):
                with self.assertRaises(ValueError):
                    self.tester.run_test(bad, 0)

    def test_negative_exceptions_rejected(self):
        with self.assertRaises(ValueError):
            self.tester.run_test(250, -1)

    def test_exceptions_exceeding_observations_rejected(self):
        with self.assertRaises(ValueError):
            self.tester.run_test(250, 251)

    def test_non_integer_inputs_rejected(self):
        with self.assertRaises(TypeError):
            self.tester.run_test(250.0, 5)
        with self.assertRaises(TypeError):
            self.tester.run_test(250, 5.0)
        with self.assertRaises(TypeError):
            self.tester.run_test(True, 0)

    def test_invalid_confidence_level_rejected(self):
        for bad in (0.0, 1.0, 1.5, -0.1):
            with self.subTest(confidence_level=bad):
                with self.assertRaises(ValueError):
                    KupiecVaRBacktester(confidence_level=bad)

    def test_invalid_significance_level_rejected(self):
        for bad in (0.0, 1.0, 2.0):
            with self.subTest(alpha=bad):
                with self.assertRaises(ValueError):
                    KupiecVaRBacktester(0.99, alpha=bad)

    # --- Determinism -----------------------------------------------------------

    def test_repeated_runs_are_identical(self):
        first = self.tester.run_test(250, 6)
        second = self.tester.run_test(250, 6)
        self.assertEqual(first, second)


if __name__ == "__main__":
    unittest.main()
