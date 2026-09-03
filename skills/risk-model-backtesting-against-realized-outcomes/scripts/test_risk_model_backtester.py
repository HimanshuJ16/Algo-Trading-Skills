"""
Tests for risk-model-backtesting-against-realized-outcomes.

Expected values are derived independently of the implementation wherever possible:

- Binomial cumulative probabilities are checked against the eleven published rows of
  BCBS bcbs22 Table 2, and the point probabilities against bcbs22 Table 1.
- Basel zone boundaries are checked against the published (5, 10) at T = 250 and against
  boundaries recomputed from the published cumulative-probability rule at other T.
- Chi-square survival functions are checked against published critical values.
- Kupiec statistics are checked against values recomputed from the closed-form expression
  by hand in the test, not by calling the implementation twice.
- Christoffersen independence is checked against a hand-computed 2x2 contingency table.
"""
import logging
import math
import unittest

from risk_model_backtester import (
    BCBS22_SCALING_FACTOR_INCREASE,
    CHI2_1DF_CRITICAL_VALUE_5PCT,
    CHI2_2DF_CRITICAL_VALUE_5PCT,
    MAR32_BACKTESTING_MULTIPLIER,
    MINIMUM_OBSERVATIONS,
    PNL_BASIS_ACTUAL,
    PNL_BASIS_HYPOTHETICAL,
    SEC_APPENDIX_E_MULTIPLICATION_FACTOR,
    BaselZone,
    DailyRiskObservation,
    Result,
    RiskModelBacktestReport,
    RiskModelBacktesterEngine,
    acerbi_szekely_z2,
    basel_zone_boundaries,
    binomial_cdf,
    chi_square_1df_survival,
    chi_square_2df_survival,
    christoffersen_independence_statistic,
    kupiec_pof_statistic,
)

# Silence the engine's audit logging during tests; the notes are asserted directly.
logging.getLogger("risk_model_backtester").addHandler(logging.NullHandler())
logging.getLogger("risk_model_backtester").propagate = False


def business_days(count, start_ordinal=739252):
    """`count` consecutive valid ISO dates, skipping weekends."""
    import datetime

    out = []
    day = datetime.date.fromordinal(start_ordinal)
    while len(out) < count:
        if day.weekday() < 5:
            out.append(day.isoformat())
        day += datetime.timedelta(days=1)
    return out


def make_series(n, exception_indices, var=10_000.0, loss=-15_000.0, gain=500.0,
                confidence_level=0.99, es=None):
    """A clean n-day window with exceptions on the given zero-based indices."""
    dates = business_days(n)
    return [
        DailyRiskObservation(
            date_iso=dates[i],
            realized_pnl_usd=loss if i in exception_indices else gain,
            forecast_var_usd=var,
            confidence_level=confidence_level,
            forecast_es_usd=es,
        )
        for i in range(n)
    ]


class TestLegacyApi(unittest.TestCase):
    """an earlier public surface must keep working."""

    def setUp(self):
        self.engine = RiskModelBacktesterEngine()

    def test_execute_true(self):
        self.assertTrue(self.engine.execute(True).success)

    def test_execute_false(self):
        self.assertFalse(self.engine.execute(False).success)

    def test_result_is_still_constructible(self):
        self.assertEqual(Result(True, "Success").message, "Success")

    def test_three_positional_observation_fields_still_work(self):
        obs = DailyRiskObservation("2026-01-05", -1.0, 10_000.0)
        self.assertEqual(obs.confidence_level, 0.99)
        self.assertIsNone(obs.hypothetical_pnl_usd)
        self.assertIsNone(obs.forecast_es_usd)


class TestBinomialCdfAgainstBcbs22(unittest.TestCase):
    """
    Independent check: BCBS bcbs22 Table 2 publishes the cumulative probability of k or
    fewer exceptions in 250 observations at 99% coverage, to two decimals.
    """

    PUBLISHED_CUMULATIVE_PCT = [
        8.11, 28.58, 54.32, 75.81, 89.22, 95.88, 98.63, 99.60, 99.89, 99.97, 99.99,
    ]
    # bcbs22 Table 1, "Model is accurate / Coverage = 99% / exact" column.
    PUBLISHED_EXACT_PCT = [8.1, 20.5, 25.7, 21.5, 13.4, 6.7, 2.7, 1.0, 0.3, 0.1]

    def test_cumulative_probabilities_match_table_2(self):
        for k, published in enumerate(self.PUBLISHED_CUMULATIVE_PCT):
            with self.subTest(k=k):
                self.assertAlmostEqual(
                    binomial_cdf(k, 250, 0.01) * 100.0, published, places=2)

    def test_point_probabilities_match_table_1(self):
        for k, published in enumerate(self.PUBLISHED_EXACT_PCT):
            with self.subTest(k=k):
                exact = binomial_cdf(k, 250, 0.01) - binomial_cdf(k - 1, 250, 0.01)
                self.assertAlmostEqual(exact * 100.0, published, places=1)

    def test_degenerate_arguments(self):
        self.assertEqual(binomial_cdf(-1, 10, 0.5), 0.0)
        self.assertEqual(binomial_cdf(10, 10, 0.5), 1.0)
        self.assertEqual(binomial_cdf(0, 10, 0.0), 1.0)
        self.assertEqual(binomial_cdf(0, 10, 1.0), 0.0)
        with self.assertRaises(ValueError):
            binomial_cdf(1, -1, 0.5)
        with self.assertRaises(ValueError):
            binomial_cdf(1, 10, 1.5)

    def test_no_overflow_on_large_windows(self):
        # A naive nCk * p^k * q^(n-k) overflows here; log-space accumulation must not.
        self.assertTrue(0.0 <= binomial_cdf(500, 100_000, 0.01) <= 1.0)


class TestChiSquareSurvival(unittest.TestCase):
    def test_one_df_matches_published_critical_value(self):
        self.assertAlmostEqual(
            chi_square_1df_survival(CHI2_1DF_CRITICAL_VALUE_5PCT), 0.05, places=12)

    def test_two_df_matches_published_critical_value(self):
        self.assertAlmostEqual(
            chi_square_2df_survival(CHI2_2DF_CRITICAL_VALUE_5PCT), 0.05, places=12)

    def test_one_df_is_not_the_two_df_function(self):
        """
        Regression guard. exp(-s/2) is the chi2(2) survival function; using it for the
        chi2(1) test inflates p-values roughly threefold at the decision boundary and lets
        miscalibrated models pass.
        """
        s = CHI2_1DF_CRITICAL_VALUE_5PCT
        self.assertAlmostEqual(chi_square_2df_survival(s), 0.1465, places=4)
        self.assertNotAlmostEqual(chi_square_1df_survival(s), math.exp(-s / 2.0), places=3)

    def test_zero_statistic_is_certain(self):
        self.assertEqual(chi_square_1df_survival(0.0), 1.0)
        self.assertEqual(chi_square_2df_survival(0.0), 1.0)

    def test_negative_statistic_raises(self):
        with self.assertRaises(ValueError):
            chi_square_1df_survival(-0.1)
        with self.assertRaises(ValueError):
            chi_square_2df_survival(-0.1)


class TestKupiecStatistic(unittest.TestCase):
    @staticmethod
    def _reference(t, x, p):
        """Closed form evaluated directly, independent of the implementation's branches."""
        pi = x / t
        numerator = ((1.0 - p) ** (t - x)) * (p ** x)
        denominator = ((1.0 - pi) ** (t - x)) * (pi ** x) if 0 < x < t else 1.0
        return -2.0 * math.log(numerator / denominator)

    def test_matches_closed_form_at_representative_counts(self):
        for x in (1, 2, 4, 6, 10, 12, 25):
            with self.subTest(x=x):
                self.assertAlmostEqual(
                    kupiec_pof_statistic(250, x, 0.01),
                    self._reference(250, x, 0.01), places=9)

    def test_zero_exceptions_is_the_analytic_limit(self):
        # x = 0: LR reduces to -2 * T * ln(1-p).
        self.assertAlmostEqual(
            kupiec_pof_statistic(250, 0, 0.01),
            -2.0 * 250 * math.log(0.99), places=12)

    def test_all_exceptions_is_the_analytic_limit(self):
        self.assertAlmostEqual(
            kupiec_pof_statistic(250, 250, 0.01),
            -2.0 * 250 * math.log(0.01), places=12)

    def test_statistic_is_zero_at_perfect_calibration(self):
        # 10 exceptions in 1000 days at p = 0.01 is exactly the null rate.
        self.assertAlmostEqual(kupiec_pof_statistic(1000, 10, 0.01), 0.0, places=9)

    def test_is_two_sided_too_few_breaches_also_rejects(self):
        """Zero exceptions in 250 days rejects the null: the model is over-conservative."""
        stat = kupiec_pof_statistic(250, 0, 0.01)
        self.assertGreater(stat, CHI2_1DF_CRITICAL_VALUE_5PCT)
        self.assertLess(chi_square_1df_survival(stat), 0.05)

    def test_no_underflow_at_many_exceptions(self):
        # p ** x underflows to 0.0 at x = 200, p = 0.01; log space must not.
        self.assertTrue(math.isfinite(kupiec_pof_statistic(1000, 200, 0.01)))


class TestChristoffersenIndependence(unittest.TestCase):
    def test_perfectly_alternating_sequence_is_not_clustered(self):
        # 0,1,0,1,... : after a 0 a breach always follows, after a 1 it never does. This is
        # maximal *negative* dependence and the statistic must be large, not zero.
        hits = [i % 2 for i in range(100)]
        self.assertGreater(christoffersen_independence_statistic(hits),
                           CHI2_1DF_CRITICAL_VALUE_5PCT)

    def test_hand_computed_contingency_table(self):
        """
        Sequence: 0 1 1 0 0 0 0 0 (8 days, 7 transitions).
        Transitions: 0->1, 1->1, 1->0, 0->0, 0->0, 0->0, 0->0
        n00 = 4, n01 = 1, n10 = 1, n11 = 1.
        pi = 2/7, pi01 = 1/5, pi11 = 1/2.
        """
        hits = [0, 1, 1, 0, 0, 0, 0, 0]
        pi, pi01, pi11 = 2 / 7, 1 / 5, 1 / 2
        n00, n01, n10, n11 = 4, 1, 1, 1
        restricted = (n00 + n10) * math.log(1 - pi) + (n01 + n11) * math.log(pi)
        unrestricted = (
            n00 * math.log(1 - pi01) + n01 * math.log(pi01)
            + n10 * math.log(1 - pi11) + n11 * math.log(pi11)
        )
        expected = -2.0 * (restricted - unrestricted)
        self.assertAlmostEqual(
            christoffersen_independence_statistic(hits), expected, places=9)

    def test_no_breaches_returns_zero(self):
        self.assertEqual(christoffersen_independence_statistic([0] * 50), 0.0)

    def test_all_breaches_returns_zero(self):
        self.assertEqual(christoffersen_independence_statistic([1] * 50), 0.0)

    def test_too_short_returns_zero(self):
        self.assertEqual(christoffersen_independence_statistic([1]), 0.0)
        self.assertEqual(christoffersen_independence_statistic([]), 0.0)

    def test_single_leading_breach_is_identified_but_gives_no_evidence(self):
        # One breach on day 0 only: no day t-1 is ever in state 1, so the n1x row of the
        # contingency table is empty and pi11 is unestimable.
        self.assertEqual(christoffersen_independence_statistic([1] + [0] * 49), 0.0)

    def test_clustered_beats_spread_at_identical_count(self):
        """
        The point of the test: Kupiec cannot distinguish these, the Markov test must.
        Both sequences have exactly 10 breaches in 250 days.
        """
        clustered = [1] * 10 + [0] * 240
        spread = [1 if i % 25 == 0 else 0 for i in range(250)]
        self.assertEqual(sum(clustered), 10)
        self.assertEqual(sum(spread), 10)
        self.assertGreater(christoffersen_independence_statistic(clustered),
                           christoffersen_independence_statistic(spread))
        self.assertGreater(christoffersen_independence_statistic(clustered),
                           CHI2_1DF_CRITICAL_VALUE_5PCT)


class TestBaselZoneBoundaries(unittest.TestCase):
    def test_published_250_day_boundaries(self):
        """bcbs22 Table 2: green 0-4, yellow 5-9, red 10 or more."""
        self.assertEqual(basel_zone_boundaries(250, 0.01), (5, 10))

    def test_boundaries_follow_the_published_cumulative_probability_rule(self):
        """
        Recompute the rule directly from binomial_cdf, independently of the function's own
        loop, at sample sizes BCBS does not tabulate.
        """
        for t in (125, 500, 750, 1000):
            with self.subTest(t=t):
                yellow = min(k for k in range(t + 1) if binomial_cdf(k, t, 0.01) >= 0.95)
                red = min(k for k in range(t + 1) if binomial_cdf(k, t, 0.01) >= 0.9999)
                self.assertEqual(basel_zone_boundaries(t, 0.01),
                                 (max(1, yellow), max(max(1, yellow), red)))

    def test_boundaries_are_not_the_linear_rescaling(self):
        """
        Regression guard for an earlier defect. Linear rescaling of the exception count
        to a 250-day equivalent implies (20, 40) at T = 1000; the binomial rule gives
        (15, 24). Rescaling would report a model with 30 exceptions in 1000 days as green.
        """
        self.assertEqual(basel_zone_boundaries(1000, 0.01), (15, 24))
        self.assertNotEqual(basel_zone_boundaries(1000, 0.01), (20, 40))
        self.assertEqual(basel_zone_boundaries(500, 0.01), (9, 15))

    def test_zone_never_begins_below_one_exception(self):
        # At T = 5, P(X <= 0) = 0.951 already clears the 95% rule; a "zero breaches,
        # yellow" verdict would be an artifact of the sample size.
        yellow, red = basel_zone_boundaries(5, 0.01)
        self.assertGreaterEqual(yellow, 1)
        self.assertGreaterEqual(red, yellow)


class TestBacktestZoneAssignment(unittest.TestCase):
    def setUp(self):
        self.engine = RiskModelBacktesterEngine(confidence_level=0.99)

    def test_green_zone(self):
        report = self.engine.backtest_var_model(make_series(250, {50, 150}))
        self.assertEqual(report.total_observations, 250)
        self.assertEqual(report.actual_exceptions, 2)
        self.assertEqual(report.basel_zone, BaselZone.GREEN)
        self.assertTrue(report.is_model_accepted)
        self.assertEqual(len(report.exceptions), 2)
        self.assertEqual(report.exceptions[0].breach_amount_usd, 5000.0)
        self.assertLess(report.kupiec_lr_stat, CHI2_1DF_CRITICAL_VALUE_5PCT)

    def test_yellow_zone(self):
        report = self.engine.backtest_var_model(make_series(250, set(range(10, 80, 10))))
        self.assertEqual(report.actual_exceptions, 7)
        self.assertEqual(report.basel_zone, BaselZone.YELLOW)
        self.assertTrue(report.is_model_accepted)

    def test_red_zone(self):
        report = self.engine.backtest_var_model(make_series(250, set(range(10, 130, 10))))
        self.assertEqual(report.actual_exceptions, 12)
        self.assertEqual(report.basel_zone, BaselZone.RED)
        self.assertFalse(report.is_model_accepted)
        self.assertGreater(report.kupiec_lr_stat, CHI2_1DF_CRITICAL_VALUE_5PCT)

    def test_exact_zone_boundaries_at_250(self):
        for count, zone in ((4, BaselZone.GREEN), (5, BaselZone.YELLOW),
                            (9, BaselZone.YELLOW), (10, BaselZone.RED)):
            with self.subTest(count=count):
                report = self.engine.backtest_var_model(
                    make_series(250, set(range(0, count * 5, 5))))
                self.assertEqual(report.actual_exceptions, count)
                self.assertEqual(report.basel_zone, zone)

    def test_short_window_is_not_falsely_reddened(self):
        """
        Regression: an earlier linear rescaling turned 1 exception in 25 days into
        int(round(1 * 250/25)) = 10 -> RED and "model rejected", while Kupiec's own p-value
        was 0.26. The binomial rule puts the red boundary for T = 25 well above 1.
        """
        report = self.engine.backtest_var_model(make_series(25, {3}))
        self.assertEqual(report.actual_exceptions, 1)
        self.assertNotEqual(report.basel_zone, BaselZone.RED)
        self.assertTrue(report.is_model_accepted)
        self.assertGreater(report.kupiec_p_value, 0.05)

    def test_long_window_is_not_falsely_greened(self):
        """
        Regression, and the unsafe direction: 30 exceptions in 1000 days is 3% against a
        1% claim. Linear rescaling gives int(round(30 * 250/1000)) = 8 -> YELLOW/"accepted".
        The published binomial rule puts the red zone at 24 or more.
        """
        report = self.engine.backtest_var_model(
            make_series(1000, set(range(0, 900, 30))))
        self.assertEqual(report.actual_exceptions, 30)
        self.assertEqual(report.basel_red_zone_starts_at, 24)
        self.assertEqual(report.basel_zone, BaselZone.RED)
        self.assertFalse(report.is_model_accepted)

    def test_sub_reference_window_is_flagged(self):
        report = self.engine.backtest_var_model(make_series(60, {5}))
        self.assertFalse(report.meets_basel_reference_sample_size)
        self.assertIn("below the Basel reference window", report.audit_notes)

    def test_reference_window_is_not_flagged(self):
        report = self.engine.backtest_var_model(make_series(250, {5}))
        self.assertTrue(report.meets_basel_reference_sample_size)
        self.assertNotIn("below the Basel reference window", report.audit_notes)


class TestCapitalMultipliers(unittest.TestCase):
    """
    The three published tables are distinct and must not be conflated: bcbs22 Table 2
    publishes *increments* on a base of 3, MAR32.9 Table 1 publishes *total* multipliers on
    a different base, and SEC Appendix E Table 1 publishes total factors on a base of 3.
    """

    def setUp(self):
        self.engine = RiskModelBacktesterEngine(confidence_level=0.99)

    def test_published_tables_at_every_tabulated_count(self):
        for count in range(0, 11):
            with self.subTest(count=count):
                report = self.engine.backtest_var_model(
                    make_series(250, set(range(0, count * 5, 5))))
                self.assertEqual(report.actual_exceptions, count)
                self.assertAlmostEqual(
                    report.bcbs22_scaling_factor_increase,
                    BCBS22_SCALING_FACTOR_INCREASE.get(count, 1.00))
                self.assertAlmostEqual(
                    report.mar32_backtesting_multiplier,
                    MAR32_BACKTESTING_MULTIPLIER.get(count, 2.00))
                self.assertAlmostEqual(
                    report.sec_appendix_e_multiplication_factor,
                    SEC_APPENDIX_E_MULTIPLICATION_FACTOR.get(count, 4.00))

    def test_sec_table_equals_three_plus_the_bcbs22_increment(self):
        for count in range(0, 10):
            self.assertAlmostEqual(
                SEC_APPENDIX_E_MULTIPLICATION_FACTOR[count],
                3.0 + BCBS22_SCALING_FACTOR_INCREASE[count], places=10)

    def test_mar32_is_not_three_plus_the_increment(self):
        """Guard against someone "harmonising" the two tables. They have different bases."""
        self.assertNotAlmostEqual(MAR32_BACKTESTING_MULTIPLIER[6], 3.0 + 0.50, places=6)

    def test_multipliers_withheld_off_the_published_basis(self):
        report = self.engine.backtest_var_model(make_series(500, {10, 20}))
        self.assertIsNone(report.bcbs22_scaling_factor_increase)
        self.assertIsNone(report.mar32_backtesting_multiplier)
        self.assertIsNone(report.sec_appendix_e_multiplication_factor)
        self.assertIn("not applicable", report.audit_notes)

    def test_multipliers_withheld_at_non_99_percent_coverage(self):
        engine = RiskModelBacktesterEngine(confidence_level=0.975)
        report = engine.backtest_var_model(
            make_series(250, {10, 20}, confidence_level=0.975))
        self.assertIsNone(report.mar32_backtesting_multiplier)

    def test_red_zone_note_does_not_claim_disqualification(self):
        report = self.engine.backtest_var_model(make_series(250, set(range(0, 60, 5))))
        self.assertEqual(report.basel_zone, BaselZone.RED)
        self.assertIn("supervisory determination", report.audit_notes)
        self.assertNotIn("Disqualified", report.audit_notes)


class TestMissingDataCountsAsException(unittest.TestCase):
    """
    MAR32.5(2) and MAR32.18(2): "In the event either the P&L or the daily VaR measure is
    not available or impossible to compute, it will count as an outlier."
    """

    def setUp(self):
        self.engine = RiskModelBacktesterEngine(confidence_level=0.99)

    def test_nan_pnl_is_an_exception(self):
        observations = make_series(250, set())
        observations[0].realized_pnl_usd = float("nan")
        report = self.engine.backtest_var_model(observations)
        self.assertEqual(report.actual_exceptions, 1)
        self.assertEqual(report.missing_data_outliers, 1)
        self.assertTrue(report.exceptions[0].is_missing_data_outlier)
        self.assertIsNone(report.exceptions[0].breach_amount_usd)
        self.assertIn("MAR32.5(2)", report.audit_notes)

    def test_infinite_pnl_is_an_exception(self):
        observations = make_series(250, set())
        observations[3].realized_pnl_usd = float("-inf")
        self.assertEqual(
            self.engine.backtest_var_model(observations).actual_exceptions, 1)

    def test_nan_var_is_an_exception(self):
        observations = make_series(250, set())
        observations[7].forecast_var_usd = float("nan")
        report = self.engine.backtest_var_model(observations)
        self.assertEqual(report.actual_exceptions, 1)
        self.assertEqual(report.missing_data_outliers, 1)

    def test_a_broken_feed_can_reach_the_red_zone(self):
        """A whole month of missing P&L must not report as a validated model."""
        observations = make_series(250, set())
        for i in range(20):
            observations[i].realized_pnl_usd = float("nan")
        report = self.engine.backtest_var_model(observations)
        self.assertEqual(report.actual_exceptions, 20)
        self.assertEqual(report.basel_zone, BaselZone.RED)
        self.assertFalse(report.is_model_accepted)


class TestActualVsHypotheticalPnl(unittest.TestCase):
    """MAR32.5(1): the overall count is the greater of the two."""

    def setUp(self):
        self.engine = RiskModelBacktesterEngine(confidence_level=0.99)

    def test_greater_count_governs_when_hypothetical_is_worse(self):
        observations = make_series(250, {10, 20})
        for i in (10, 20, 30, 40, 50, 60):
            observations[i].hypothetical_pnl_usd = -15_000.0
        for i in range(250):
            if observations[i].hypothetical_pnl_usd is None:
                observations[i].hypothetical_pnl_usd = 400.0
        report = self.engine.backtest_var_model(observations)
        self.assertEqual(report.actual_pnl_exceptions, 2)
        self.assertEqual(report.hypothetical_pnl_exceptions, 6)
        self.assertEqual(report.actual_exceptions, 6)
        self.assertEqual(report.governing_pnl_basis, PNL_BASIS_HYPOTHETICAL)
        self.assertEqual(report.basel_zone, BaselZone.YELLOW)
        self.assertTrue(all(e.pnl_basis == PNL_BASIS_HYPOTHETICAL
                            for e in report.exceptions))

    def test_actual_governs_when_it_is_worse(self):
        observations = make_series(250, {10, 20, 30, 40, 50, 60})
        for obs in observations:
            obs.hypothetical_pnl_usd = 400.0
        report = self.engine.backtest_var_model(observations)
        self.assertEqual(report.hypothetical_pnl_exceptions, 0)
        self.assertEqual(report.actual_exceptions, 6)
        self.assertEqual(report.governing_pnl_basis, PNL_BASIS_ACTUAL)

    def test_tie_keeps_the_actual_basis(self):
        observations = make_series(250, {10, 20})
        for i, obs in enumerate(observations):
            obs.hypothetical_pnl_usd = -15_000.0 if i in (100, 200) else 400.0
        report = self.engine.backtest_var_model(observations)
        self.assertEqual(report.hypothetical_pnl_exceptions, 2)
        self.assertEqual(report.governing_pnl_basis, PNL_BASIS_ACTUAL)

    def test_partial_hypothetical_series_is_ignored_not_mixed(self):
        observations = make_series(250, {10})
        observations[50].hypothetical_pnl_usd = -99_000.0
        report = self.engine.backtest_var_model(observations)
        self.assertIsNone(report.hypothetical_pnl_exceptions)
        self.assertEqual(report.actual_exceptions, 1)
        self.assertEqual(report.governing_pnl_basis, PNL_BASIS_ACTUAL)


class TestExpectedShortfallDiagnostic(unittest.TestCase):
    def setUp(self):
        self.engine = RiskModelBacktesterEngine(confidence_level=0.99)

    def test_z2_is_one_when_there_are_no_breaches(self):
        # Every indicator is zero, so every term is exactly 1.
        self.assertAlmostEqual(
            acerbi_szekely_z2([100.0] * 10, [1000.0] * 10, [1500.0] * 10, 0.01),
            1.0, places=12)

    def test_z2_hand_computed_on_a_single_breach(self):
        """
        N = 100, one day with x = -5000, v = 1000, e = 1500, alpha = 0.01.
        Breach term: -5000 / (0.01 * 1500) + 1 = -333.3333... + 1 = -332.3333...
        The other 99 terms are 1 each, so Z2 = (99 - 332.33333...) / 100.
        """
        pnl = [-5000.0] + [100.0] * 99
        expected = (99.0 + (-5000.0 / (0.01 * 1500.0) + 1.0)) / 100.0
        self.assertAlmostEqual(
            acerbi_szekely_z2(pnl, [1000.0] * 100, [1500.0] * 100, 0.01),
            expected, places=9)
        self.assertLess(expected, 0.0)

    def test_negative_z2_flags_underestimation_in_the_report(self):
        observations = make_series(250, set(range(0, 100, 10)),
                                   var=10_000.0, loss=-90_000.0, es=13_000.0)
        report = self.engine.backtest_var_model(observations)
        self.assertIsNotNone(report.es_acerbi_szekely_z2)
        self.assertLess(report.es_acerbi_szekely_z2, 0.0)
        self.assertTrue(report.es_underestimated)
        self.assertIn("UNDERESTIMATE", report.audit_notes)

    def test_well_calibrated_es_is_not_flagged(self):
        # Two breaches that only just exceed VaR, against a comfortable ES forecast.
        observations = make_series(250, {50, 150}, var=10_000.0, loss=-10_500.0,
                                   es=13_000.0)
        report = self.engine.backtest_var_model(observations)
        self.assertGreater(report.es_acerbi_szekely_z2, 0.0)
        self.assertFalse(report.es_underestimated)

    def test_no_p_value_is_claimed(self):
        observations = make_series(250, {50}, es=13_000.0)
        report = self.engine.backtest_var_model(observations)
        self.assertIn("No p-value", report.audit_notes)

    def test_absent_when_es_not_supplied(self):
        report = self.engine.backtest_var_model(make_series(250, {50}))
        self.assertIsNone(report.es_acerbi_szekely_z2)
        self.assertIsNone(report.es_underestimated)

    def test_skipped_on_a_partial_es_series(self):
        observations = make_series(250, {50})
        observations[0].forecast_es_usd = 13_000.0
        report = self.engine.backtest_var_model(observations)
        self.assertIsNone(report.es_acerbi_szekely_z2)

    def test_skipped_when_the_window_has_missing_data(self):
        observations = make_series(250, set(), es=13_000.0)
        observations[0].realized_pnl_usd = float("nan")
        report = self.engine.backtest_var_model(observations)
        self.assertIsNone(report.es_acerbi_szekely_z2)
        self.assertEqual(report.actual_exceptions, 1)

    def test_non_positive_es_rejected(self):
        with self.assertRaises(ValueError):
            acerbi_szekely_z2([1.0], [1.0], [0.0], 0.01)
        with self.assertRaises(ValueError):
            self.engine.backtest_var_model(make_series(250, {50}, es=-1.0))

    def test_helper_argument_validation(self):
        with self.assertRaises(ValueError):
            acerbi_szekely_z2([1.0, 2.0], [1.0], [1.0], 0.01)
        with self.assertRaises(ValueError):
            acerbi_szekely_z2([], [], [], 0.01)
        with self.assertRaises(ValueError):
            acerbi_szekely_z2([1.0], [1.0], [1.0], 0.0)

    def test_es_below_var_warns_but_still_computes(self):
        observations = make_series(250, {50}, var=10_000.0, es=5_000.0)
        with self.assertLogs("risk_model_backtester", level="WARNING") as captured:
            logging.getLogger("risk_model_backtester").propagate = True
            try:
                report = self.engine.backtest_var_model(observations)
            finally:
                logging.getLogger("risk_model_backtester").propagate = False
        self.assertIsNotNone(report.es_acerbi_szekely_z2)
        self.assertTrue(any("below forecast VaR" in m for m in captured.output))


class TestClusteringDetection(unittest.TestCase):
    def setUp(self):
        self.engine = RiskModelBacktesterEngine(confidence_level=0.99)

    def test_clustered_breaches_are_flagged(self):
        report = self.engine.backtest_var_model(make_series(250, set(range(40, 48))))
        self.assertEqual(report.actual_exceptions, 8)
        self.assertTrue(report.exceptions_are_clustered)
        self.assertLess(report.christoffersen_ind_p_value, 0.05)
        self.assertIn("clustered", report.audit_notes)

    def test_spread_breaches_are_not_flagged(self):
        report = self.engine.backtest_var_model(make_series(250, set(range(0, 240, 30))))
        self.assertEqual(report.actual_exceptions, 8)
        self.assertFalse(report.exceptions_are_clustered)

    def test_kupiec_cannot_distinguish_what_independence_does(self):
        """
        Same count, same Kupiec statistic, opposite independence verdicts. This is the
        capability the frontmatter advertises and a naive engine did not have.
        """
        clustered = self.engine.backtest_var_model(make_series(250, set(range(40, 48))))
        spread = self.engine.backtest_var_model(make_series(250, set(range(0, 240, 30))))
        self.assertEqual(clustered.actual_exceptions, spread.actual_exceptions)
        self.assertAlmostEqual(clustered.kupiec_lr_stat, spread.kupiec_lr_stat, places=12)
        self.assertEqual(clustered.basel_zone, spread.basel_zone)
        self.assertGreater(clustered.christoffersen_ind_lr_stat,
                           spread.christoffersen_ind_lr_stat)
        self.assertTrue(clustered.exceptions_are_clustered)
        self.assertFalse(spread.exceptions_are_clustered)

    def test_conditional_coverage_is_the_sum_of_its_components(self):
        report = self.engine.backtest_var_model(make_series(250, set(range(40, 48))))
        self.assertAlmostEqual(
            report.christoffersen_cc_lr_stat,
            report.kupiec_lr_stat + report.christoffersen_ind_lr_stat, places=12)
        self.assertAlmostEqual(
            report.christoffersen_cc_p_value,
            math.exp(-report.christoffersen_cc_lr_stat / 2.0), places=12)


class TestInputValidation(unittest.TestCase):
    def setUp(self):
        self.engine = RiskModelBacktesterEngine(confidence_level=0.99)

    def test_short_window_raises(self):
        with self.assertRaises(ValueError) as ctx:
            self.engine.backtest_var_model(make_series(MINIMUM_OBSERVATIONS - 1, set()))
        self.assertIn(str(MINIMUM_OBSERVATIONS), str(ctx.exception))

    def test_minimum_window_is_accepted(self):
        report = self.engine.backtest_var_model(make_series(MINIMUM_OBSERVATIONS, set()))
        self.assertEqual(report.total_observations, MINIMUM_OBSERVATIONS)

    def test_empty_window_raises_rather_than_passing(self):
        with self.assertRaises(ValueError):
            self.engine.backtest_var_model([])

    def test_non_sequence_raises(self):
        with self.assertRaises(TypeError):
            self.engine.backtest_var_model(None)
        with self.assertRaises(TypeError):
            self.engine.backtest_var_model("2026-01-01")

    def test_wrong_element_type_raises(self):
        observations = make_series(25, set())
        observations[4] = {"date_iso": "2026-01-05"}
        with self.assertRaises(TypeError):
            self.engine.backtest_var_model(observations)

    def test_malformed_date_raises(self):
        observations = make_series(25, set())
        observations[2].date_iso = "2026-01-002"
        with self.assertRaises(ValueError) as ctx:
            self.engine.backtest_var_model(observations)
        self.assertIn("ISO 8601", str(ctx.exception))

    def test_duplicate_date_raises(self):
        observations = make_series(25, set())
        observations[5].date_iso = observations[4].date_iso
        with self.assertRaises(ValueError) as ctx:
            self.engine.backtest_var_model(observations)
        self.assertIn("strictly increasing", str(ctx.exception))

    def test_out_of_order_dates_raise(self):
        observations = make_series(25, set())
        observations[3], observations[9] = observations[9], observations[3]
        with self.assertRaises(ValueError):
            self.engine.backtest_var_model(observations)

    def test_weekend_and_holiday_gaps_are_allowed(self):
        observations = make_series(25, set())
        del observations[10]
        report = self.engine.backtest_var_model(observations)
        self.assertEqual(report.total_observations, 24)

    def test_confidence_level_mismatch_raises(self):
        observations = make_series(25, set())
        observations[6].confidence_level = 0.95
        with self.assertRaises(ValueError) as ctx:
            self.engine.backtest_var_model(observations)
        self.assertIn("does not match", str(ctx.exception))

    def test_non_positive_var_raises(self):
        for bad in (0.0, -10_000.0):
            with self.subTest(bad=bad):
                observations = make_series(25, set())
                observations[1].forecast_var_usd = bad
                with self.assertRaises(ValueError):
                    self.engine.backtest_var_model(observations)

    def test_non_numeric_fields_raise_typeerror_not_a_silent_outlier(self):
        """
        A wrong type is a caller bug and must surface as one. It must not be swallowed by
        the MAR32.5(2) missing-data path, which exists for genuinely unavailable values.
        """
        for attribute, bad in (
            ("forecast_var_usd", "10000"),
            ("realized_pnl_usd", "oops"),
            ("realized_pnl_usd", True),
            ("hypothetical_pnl_usd", "x"),
            ("forecast_es_usd", "x"),
        ):
            with self.subTest(attribute=attribute, bad=bad):
                observations = make_series(25, set())
                setattr(observations[1], attribute, bad)
                with self.assertRaises(TypeError) as ctx:
                    self.engine.backtest_var_model(observations)
                self.assertIn(attribute, str(ctx.exception))

    def test_none_is_allowed_only_on_the_optional_fields(self):
        observations = make_series(25, set())
        self.assertIsNone(observations[0].hypothetical_pnl_usd)
        self.assertIsNone(observations[0].forecast_es_usd)
        self.engine.backtest_var_model(observations)  # must not raise
        observations[1].realized_pnl_usd = None
        with self.assertRaises(TypeError):
            self.engine.backtest_var_model(observations)

    def test_tuple_input_is_accepted(self):
        report = self.engine.backtest_var_model(tuple(make_series(25, {2})))
        self.assertEqual(report.actual_exceptions, 1)

    def test_engine_confidence_level_bounds(self):
        for bad in (0.0, 1.0, -0.5, 1.5, float("nan")):
            with self.subTest(bad=bad):
                with self.assertRaises(ValueError):
                    RiskModelBacktesterEngine(confidence_level=bad)

    def test_engine_confidence_level_type(self):
        with self.assertRaises(TypeError):
            RiskModelBacktesterEngine(confidence_level="0.99")

    def test_significance_level_bounds(self):
        for bad in (0.0, 1.0, float("nan")):
            with self.subTest(bad=bad):
                with self.assertRaises(ValueError):
                    RiskModelBacktesterEngine(significance_level=bad)


class TestReportIntegrity(unittest.TestCase):
    def setUp(self):
        self.engine = RiskModelBacktesterEngine(confidence_level=0.99)

    def test_report_is_the_documented_type(self):
        report = self.engine.backtest_var_model(make_series(250, {10}))
        self.assertIsInstance(report, RiskModelBacktestReport)

    def test_statistics_are_not_rounded_away(self):
        """
        Regression: a naive engine rounded the p-value to 4 decimals, printing a
        decisive rejection as "p-val = 0.0" in the audit trail.
        """
        report = self.engine.backtest_var_model(make_series(250, set(range(0, 100, 5))))
        self.assertEqual(report.actual_exceptions, 20)
        self.assertGreater(report.kupiec_p_value, 0.0)
        self.assertLess(report.kupiec_p_value, 1e-6)
        self.assertNotIn("p=0.0 ", report.audit_notes)

    def test_expected_exceptions_is_n_times_p(self):
        report = self.engine.backtest_var_model(make_series(250, {10}))
        self.assertAlmostEqual(report.expected_exceptions, 2.5, places=9)
        self.assertAlmostEqual(report.exception_rate_pct, 0.4, places=9)

    def test_cumulative_probability_matches_the_binomial(self):
        report = self.engine.backtest_var_model(make_series(250, {10, 20, 30}))
        self.assertAlmostEqual(report.basel_cumulative_probability,
                               binomial_cdf(3, 250, 0.01), places=12)

    def test_breach_amount_is_the_excess_over_var(self):
        observations = make_series(250, {5}, var=10_000.0, loss=-12_345.67)
        report = self.engine.backtest_var_model(observations)
        self.assertAlmostEqual(report.exceptions[0].breach_amount_usd, 2345.67, places=2)

    def test_exception_rule_is_strict(self):
        """A loss exactly equal to VaR is covered by the forecast, not an exception."""
        observations = make_series(250, set(), gain=-10_000.0, var=10_000.0)
        self.assertEqual(
            self.engine.backtest_var_model(observations).actual_exceptions, 0)

    def test_one_cent_past_var_is_an_exception(self):
        observations = make_series(250, set(), gain=-10_000.01, var=10_000.0)
        self.assertEqual(
            self.engine.backtest_var_model(observations).actual_exceptions, 250)

    def test_deterministic(self):
        observations = make_series(250, {10, 60, 61, 62})
        first = self.engine.backtest_var_model(observations)
        second = self.engine.backtest_var_model(observations)
        self.assertEqual(first.audit_notes, second.audit_notes)
        self.assertEqual(first.kupiec_lr_stat, second.kupiec_lr_stat)

    def test_input_is_not_mutated(self):
        observations = make_series(250, {10})
        before = [(o.date_iso, o.realized_pnl_usd, o.forecast_var_usd)
                  for o in observations]
        self.engine.backtest_var_model(observations)
        after = [(o.date_iso, o.realized_pnl_usd, o.forecast_var_usd)
                 for o in observations]
        self.assertEqual(before, after)

    def test_non_99_percent_coverage_is_supported(self):
        """MAR32.18 requires desk-level backtesting at 97.5% as well as 99%."""
        engine = RiskModelBacktesterEngine(confidence_level=0.975)
        observations = make_series(250, set(range(0, 60, 10)), confidence_level=0.975)
        report = engine.backtest_var_model(observations)
        self.assertAlmostEqual(report.expected_exceptions, 6.25, places=9)
        self.assertEqual(report.actual_exceptions, 6)
        self.assertEqual(report.basel_zone, BaselZone.GREEN)


if __name__ == "__main__":
    unittest.main()
