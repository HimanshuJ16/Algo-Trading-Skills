"""Unit tests for strategy-performance-attribution-vs-market-beta.

Expected values are derived independently of the engine: by closed-form algebra on a
hand-worked four-observation dataset, and by cross-checking against
`scipy.stats.linregress` (a separate OLS implementation) and against the textbook
sandwich formula computed directly with numpy. Tests that merely re-evaluate the
engine's own expression would verify nothing.
"""
import unittest

import numpy as np
import pandas as pd
from scipy import stats

from strategy_performance_attribution_vs_market_beta import (
    StrategyPerformanceAttributionVsMarketBeta,
    StrategyPerformanceAttributionVsMarketBetaConfig,
    StrategyPerformanceAttributionEngine,
    PerformanceAttributionReport,
    FactorAttributionBreakdown,
)


def _synthetic_capm(n=504, beta=1.15, alpha_annual=0.06, noise=0.0025, seed=7):
    """Market series and a strategy built as beta*MKT + alpha + iid noise."""
    rng = np.random.default_rng(seed)
    mkt = rng.normal(0.0004, 0.011, n)
    strat = beta * mkt + alpha_annual / 252.0 + rng.normal(0.0, noise, n)
    return pd.Series(mkt), pd.Series(strat)


class TestPerformanceAttributionLegacy(unittest.TestCase):
    """The legacy shim is part of the published API and must keep working."""

    def test_execute_true(self):
        config = StrategyPerformanceAttributionVsMarketBetaConfig(enabled=True)
        engine = StrategyPerformanceAttributionVsMarketBeta(config)
        self.assertTrue(engine.execute())

    def test_execute_false(self):
        config = StrategyPerformanceAttributionVsMarketBetaConfig(enabled=False)
        engine = StrategyPerformanceAttributionVsMarketBeta(config)
        self.assertFalse(engine.execute())


class TestClosedFormRegression(unittest.TestCase):
    """Every expected value below is computed by hand in the docstring, not by code.

    With a zero risk-free rate, mkt = [0.01, 0.02, 0.03, 0.04] and
    strat = [0.02, 0.03, 0.06, 0.07]:

        x_bar = 0.025, y_bar = 0.045
        Sxy   = 0.0009, Sxx = 0.0005  ->  beta  = 1.8
        alpha = 0.045 - 1.8 * 0.025   =  0.0 exactly
        residuals = [0.002, -0.006, 0.006, -0.002], SSres = 8.0e-5, SStot = 0.0017
        R^2       = 1 - 8.0e-5/0.0017 = 0.95294117...
        adj R^2   = 1 - 0.04705882 * 3/2 = 0.92941176...
        sigma^2   = 8.0e-5 / 2 = 4.0e-5
        Var(alpha)= sigma^2 (1/n + x_bar^2/Sxx) = 4e-5 * 1.5 = 6.0e-5
        SE(alpha) = 0.007745966..., t_alpha = 0.0
        Var(beta) = sigma^2 / Sxx = 0.08, SE(beta) = 0.2828427..., t_beta = 6.363961...
        t crit at 5% with 2 df = 4.302653
    """

    def setUp(self):
        self.report = StrategyPerformanceAttributionEngine().analyze_attribution(
            strategy_id="HAND_WORKED",
            strategy_returns=pd.Series([0.02, 0.03, 0.06, 0.07]),
            market_returns=pd.Series([0.01, 0.02, 0.03, 0.04]),
            risk_free_rate_annual_pct=0.0,
        )

    def test_beta_and_alpha_match_closed_form(self):
        self.assertAlmostEqual(self.report.market_beta, 1.8, places=10)
        self.assertAlmostEqual(self.report.annualized_jensens_alpha_pct, 0.0, places=10)

    def test_r_squared_and_adjusted_r_squared_match_closed_form(self):
        self.assertAlmostEqual(self.report.r_squared, 0.9529, places=4)
        self.assertAlmostEqual(self.report.adjusted_r_squared, 0.9294, places=4)

    def test_standard_errors_and_t_statistics_match_closed_form(self):
        self.assertAlmostEqual(self.report.alpha_standard_error, 0.007745966692, places=10)
        self.assertAlmostEqual(self.report.alpha_t_statistic, 0.0, places=10)
        market = self.report.factor_breakdown[0]
        self.assertAlmostEqual(market.standard_error, 0.2828427125, places=9)
        self.assertAlmostEqual(market.t_statistic, 6.3640, places=4)

    def test_significance_uses_exact_student_t_not_1_96(self):
        # With 2 residual degrees of freedom the 5% two-sided threshold is 4.302653,
        # not the large-sample 1.96. A t of 6.364 clears it; 1.96 would have been a
        # far weaker (and wrong) hurdle.
        self.assertAlmostEqual(self.report.t_critical_value, 4.302653, places=4)
        self.assertEqual(self.report.degrees_of_freedom, 2)
        self.assertTrue(self.report.factor_breakdown[0].is_statistically_significant)
        self.assertFalse(self.report.is_true_alpha_significant)

    def test_factor_contribution_and_totals_match_closed_form(self):
        # mean(mkt) * 252 * 100 = 0.025 * 25200 = 630.0
        market = self.report.factor_breakdown[0]
        self.assertAlmostEqual(market.factor_annual_return_pct, 630.0, places=6)
        self.assertAlmostEqual(market.return_contribution_pct, 1134.0, places=6)
        self.assertAlmostEqual(self.report.total_realized_annual_return_pct, 1134.0, places=6)


class TestIndependentCrossValidation(unittest.TestCase):
    """Cross-check the whole OLS path against scipy.stats.linregress."""

    def setUp(self):
        self.engine = StrategyPerformanceAttributionEngine()
        self.mkt, self.strat = _synthetic_capm()
        self.rf_annual_pct = 3.0
        self.rf_period = (1.0 + self.rf_annual_pct / 100.0) ** (1.0 / 252.0) - 1.0
        self.report = self.engine.analyze_attribution(
            strategy_id="CROSS_CHECK",
            strategy_returns=self.strat,
            market_returns=self.mkt,
            risk_free_rate_annual_pct=self.rf_annual_pct,
        )
        self.reference = stats.linregress(
            self.mkt.to_numpy() - self.rf_period,
            self.strat.to_numpy() - self.rf_period,
        )

    def test_beta_matches_scipy(self):
        self.assertAlmostEqual(self.report.market_beta, self.reference.slope, delta=5e-5)

    def test_alpha_matches_scipy(self):
        # annualized_jensens_alpha_pct is reported rounded to 2 dp (1 bp).
        self.assertAlmostEqual(
            self.report.annualized_jensens_alpha_pct,
            self.reference.intercept * 252.0 * 100.0,
            delta=5e-3)

    def test_r_squared_matches_scipy(self):
        self.assertAlmostEqual(self.report.r_squared, self.reference.rvalue ** 2, delta=5e-5)

    def test_alpha_standard_error_matches_scipy(self):
        self.assertAlmostEqual(
            self.report.alpha_standard_error, self.reference.intercept_stderr, places=12)

    def test_market_beta_standard_error_and_p_value_match_scipy(self):
        market = self.report.factor_breakdown[0]
        self.assertAlmostEqual(market.standard_error, self.reference.stderr, places=12)
        self.assertAlmostEqual(market.p_value, self.reference.pvalue, places=10)

    def test_recovers_the_planted_beta_and_alpha(self):
        # Series was generated as 1.15 * MKT + 6% annual alpha.
        self.assertAlmostEqual(self.report.market_beta, 1.15, delta=0.05)
        self.assertAlmostEqual(self.report.annualized_jensens_alpha_pct, 6.0, delta=1.5)


class TestStandardErrorFloorRegression(unittest.TestCase):
    """Regression test for the removed 1e-8 variance floor.

    The previous implementation clamped every coefficient variance at 1e-8, i.e. every
    standard error at 1e-4. Daily alpha standard errors are of that same order, so a
    low-noise strategy had its alpha t-statistic silently deflated -- here from 4.54 to
    2.32, close enough to the 1.96 hurdle to flip the verdict on a slightly weaker
    strategy. The expected value comes from scipy, not from the engine.
    """

    def test_small_standard_error_is_not_clamped(self):
        rng = np.random.default_rng(11)
        mkt = rng.normal(0.0004, 0.010, 252)
        strat = 1.0 * mkt + 0.05 / 252.0 + rng.normal(0.0, 0.0008, 252)
        rf_period = 1.02 ** (1.0 / 252.0) - 1.0

        report = StrategyPerformanceAttributionEngine().analyze_attribution(
            strategy_id="LOW_NOISE", strategy_returns=pd.Series(strat),
            market_returns=pd.Series(mkt), risk_free_rate_annual_pct=2.0)
        reference = stats.linregress(mkt - rf_period, strat - rf_period)

        self.assertLess(reference.intercept_stderr, 1e-4,
                        "test fixture must sit below the old 1e-4 standard-error floor")
        self.assertAlmostEqual(
            report.alpha_standard_error, reference.intercept_stderr, places=12)
        self.assertAlmostEqual(
            report.alpha_t_statistic,
            round(reference.intercept / reference.intercept_stderr, 2), places=2)
        self.assertGreater(abs(report.alpha_t_statistic), 4.0)


class TestAlignmentRegressions(unittest.TestCase):
    """Regression tests for the stale-sample defects in factor and total statistics."""

    def setUp(self):
        rng = np.random.default_rng(3)
        n = 300
        mkt = rng.normal(0.0005, 0.01, n)
        mkt[:150] = 0.05          # a wildly different regime in the rows that get dropped
        strat = 1.0 * mkt + 0.05 / 252.0 + rng.normal(0.0, 0.001, n)
        smb = pd.Series(rng.normal(0.0, 0.005, n))
        smb.iloc[:150] = np.nan   # forces the first 150 rows out of the regression

        self.mkt = pd.Series(mkt)
        self.strat = pd.Series(strat)
        self.smb = smb
        self.report = StrategyPerformanceAttributionEngine().analyze_attribution(
            strategy_id="RAGGED", strategy_returns=self.strat,
            market_returns=self.mkt, smb_returns=self.smb,
            risk_free_rate_annual_pct=0.0)

    def test_observation_counts_are_reported(self):
        self.assertEqual(self.report.observations, 150)
        self.assertEqual(self.report.dropped_observations, 150)

    def test_market_factor_return_uses_the_aligned_sample_only(self):
        # Previously this was the mean of all 300 rows including the dropped regime.
        aligned_mean_pct = float(self.mkt.iloc[150:].mean()) * 252.0 * 100.0
        full_mean_pct = float(self.mkt.mean()) * 252.0 * 100.0
        self.assertAlmostEqual(
            self.report.factor_breakdown[0].factor_annual_return_pct,
            aligned_mean_pct, places=4)
        self.assertNotAlmostEqual(aligned_mean_pct, full_mean_pct, places=1)

    def test_total_return_uses_the_aligned_sample_only(self):
        aligned_total_pct = float(self.strat.iloc[150:].mean()) * 252.0 * 100.0
        self.assertAlmostEqual(
            self.report.total_realized_annual_return_pct, round(aligned_total_pct, 2),
            places=2)

    def test_smb_factor_return_uses_the_aligned_sample_only(self):
        aligned_smb_pct = float(self.smb.iloc[150:].mean()) * 252.0 * 100.0
        smb_row = [f for f in self.report.factor_breakdown if f.factor_name == "Size (SMB)"][0]
        self.assertAlmostEqual(smb_row.factor_annual_return_pct, aligned_smb_pct, places=4)


class TestAdditiveDecompositionIdentity(unittest.TestCase):
    """rf + alpha + sum(beta_i * factor_i) must reconstruct the realized total return."""

    def _assert_identity_closes(self, report):
        # unexplained_residual_pct is computed from unrounded quantities, so it is the
        # exact closure check; the reconstruction from the published (rounded) fields
        # can only agree to the coarsest of their rounding steps, 0.01 percentage points.
        self.assertAlmostEqual(report.unexplained_residual_pct, 0.0, places=8)
        rebuilt = (report.risk_free_contribution_pct
                   + report.annualized_jensens_alpha_pct
                   + sum(f.return_contribution_pct for f in report.factor_breakdown))
        self.assertAlmostEqual(
            report.total_realized_annual_return_pct, rebuilt, delta=0.02)

    def test_identity_closes_for_capm(self):
        mkt, strat = _synthetic_capm()
        self._assert_identity_closes(
            StrategyPerformanceAttributionEngine().analyze_attribution(
                strategy_id="CAPM", strategy_returns=strat, market_returns=mkt,
                risk_free_rate_annual_pct=4.0))

    def test_identity_closes_for_fama_french_three_factor(self):
        rng = np.random.default_rng(23)
        n = 504
        mkt = rng.normal(0.0004, 0.011, n)
        smb = rng.normal(0.0002, 0.005, n)
        hml = rng.normal(-0.0001, 0.005, n)
        strat = 1.0 * mkt + 0.5 * smb - 0.3 * hml + 0.04 / 252.0 + rng.normal(0.0, 0.001, n)
        self._assert_identity_closes(
            StrategyPerformanceAttributionEngine().analyze_attribution(
                strategy_id="FF3", strategy_returns=pd.Series(strat),
                market_returns=pd.Series(mkt), smb_returns=pd.Series(smb),
                hml_returns=pd.Series(hml), risk_free_rate_annual_pct=4.0))


class TestFamaFrenchConventions(unittest.TestCase):
    """SMB/HML enter raw; the market leg's excess convention is explicit."""

    def setUp(self):
        rng = np.random.default_rng(23)
        n = 504
        self.mkt = pd.Series(rng.normal(0.0004, 0.011, n))
        self.smb = pd.Series(rng.normal(0.0002, 0.005, n))
        self.hml = pd.Series(rng.normal(-0.0001, 0.005, n))
        self.strat = pd.Series(
            1.0 * self.mkt + 0.5 * self.smb - 0.3 * self.hml
            + 0.04 / 252.0 + rng.normal(0.0, 0.001, n))

    def _run(self, **kwargs):
        return StrategyPerformanceAttributionEngine().analyze_attribution(
            strategy_id="FF3", strategy_returns=self.strat, market_returns=self.mkt,
            smb_returns=self.smb, hml_returns=self.hml, **kwargs)

    def test_recovers_planted_size_and_value_loadings(self):
        report = self._run(risk_free_rate_annual_pct=2.0)
        names = [f.factor_name for f in report.factor_breakdown]
        self.assertEqual(names, ["Market (MKT)", "Size (SMB)", "Value (HML)"])
        loadings = {f.factor_name: f.beta_exposure for f in report.factor_breakdown}
        self.assertAlmostEqual(loadings["Size (SMB)"], 0.5, delta=0.05)
        self.assertAlmostEqual(loadings["Value (HML)"], -0.3, delta=0.05)

    def test_smb_and_hml_loadings_are_invariant_to_the_risk_free_rate(self):
        # SMB and HML are zero-investment spreads: the risk-free rate must never be
        # subtracted from them, so changing it cannot move their loadings.
        low = {f.factor_name: f.beta_exposure for f in self._run(
            risk_free_rate_annual_pct=0.0).factor_breakdown}
        high = {f.factor_name: f.beta_exposure for f in self._run(
            risk_free_rate_annual_pct=8.0).factor_breakdown}
        self.assertAlmostEqual(low["Size (SMB)"], high["Size (SMB)"], places=10)
        self.assertAlmostEqual(low["Value (HML)"], high["Value (HML)"], places=10)

    def test_excess_market_flag_avoids_double_subtracting_the_risk_free_rate(self):
        # alpha(excess=False) - alpha(excess=True) = beta * rf_period, exactly.
        rf_annual_pct = 5.0
        rf_period = (1.0 + rf_annual_pct / 100.0) ** (1.0 / 252.0) - 1.0
        total = self._run(risk_free_rate_annual_pct=rf_annual_pct)
        excess = self._run(risk_free_rate_annual_pct=rf_annual_pct,
                           market_returns_are_excess=True)

        self.assertAlmostEqual(total.market_beta, excess.market_beta, places=10)
        self.assertTrue(excess.market_factor_is_excess)
        self.assertFalse(total.market_factor_is_excess)
        expected_gap = total.market_beta * rf_period * 252.0 * 100.0
        self.assertAlmostEqual(
            total.annualized_jensens_alpha_pct - excess.annualized_jensens_alpha_pct,
            expected_gap, places=2)


class TestNeweyWestStandardErrors(unittest.TestCase):

    def setUp(self):
        self.engine = StrategyPerformanceAttributionEngine()
        self.mkt, self.strat = _synthetic_capm(n=504)

    def test_default_lag_follows_the_newey_west_1994_rule(self):
        report = self.engine.analyze_attribution(
            strategy_id="HAC", strategy_returns=self.strat, market_returns=self.mkt,
            standard_errors="hac")
        # floor(4 * (504/100) ** (2/9)) = floor(5.66...) = 5
        self.assertEqual(report.hac_lags, 5)
        self.assertEqual(report.standard_error_type, "hac")

    def test_coefficients_are_unchanged_by_the_covariance_estimator(self):
        ols = self.engine.analyze_attribution(
            strategy_id="A", strategy_returns=self.strat, market_returns=self.mkt)
        hac = self.engine.analyze_attribution(
            strategy_id="A", strategy_returns=self.strat, market_returns=self.mkt,
            standard_errors="hac")
        self.assertAlmostEqual(ols.market_beta, hac.market_beta, places=10)
        self.assertAlmostEqual(ols.annualized_jensens_alpha_pct,
                               hac.annualized_jensens_alpha_pct, places=10)
        self.assertIsNone(ols.hac_lags)

    def test_zero_lag_hac_equals_the_textbook_hc1_sandwich(self):
        """With L = 0 the Bartlett kernel collapses to White's estimator.

        Expected values are built here from the sandwich formula directly:
            cov = (X'X)^-1 (sum_t u_t^2 x_t x_t') (X'X)^-1 * n/(n-k)
        """
        rf_period = 1.02 ** (1.0 / 252.0) - 1.0
        y = self.strat.to_numpy() - rf_period
        design = np.column_stack([np.ones(len(y)), self.mkt.to_numpy() - rf_period])
        coefficients, *_ = np.linalg.lstsq(design, y, rcond=None)
        residuals = y - design @ coefficients
        scaled = design * residuals[:, None]
        xtx_inv = np.linalg.inv(design.T @ design)
        n_obs, n_cols = design.shape
        cov = xtx_inv @ (scaled.T @ scaled) @ xtx_inv * (n_obs / (n_obs - n_cols))
        expected_se = np.sqrt(np.diag(cov))

        report = self.engine.analyze_attribution(
            strategy_id="HC1", strategy_returns=self.strat, market_returns=self.mkt,
            risk_free_rate_annual_pct=2.0, standard_errors="hac", hac_lags=0)
        self.assertEqual(report.hac_lags, 0)
        self.assertAlmostEqual(report.alpha_standard_error, expected_se[0], places=12)
        self.assertAlmostEqual(
            report.factor_breakdown[0].standard_error, expected_se[1], places=12)

    def test_positively_autocorrelated_residuals_widen_the_hac_standard_error(self):
        # An AR(1) residual violates the iid assumption behind the OLS standard error;
        # Newey-West should report a wider interval for alpha.
        rng = np.random.default_rng(5)
        n = 504
        mkt = rng.normal(0.0004, 0.011, n)
        shocks = rng.normal(0.0, 0.002, n)
        residual = np.zeros(n)
        for i in range(1, n):
            residual[i] = 0.6 * residual[i - 1] + shocks[i]
        strat = 1.0 * mkt + 0.05 / 252.0 + residual

        ols = self.engine.analyze_attribution(
            strategy_id="AR1", strategy_returns=pd.Series(strat),
            market_returns=pd.Series(mkt))
        hac = self.engine.analyze_attribution(
            strategy_id="AR1", strategy_returns=pd.Series(strat),
            market_returns=pd.Series(mkt), standard_errors="hac")
        self.assertGreater(hac.alpha_standard_error, ols.alpha_standard_error)
        self.assertLess(abs(hac.alpha_t_statistic), abs(ols.alpha_t_statistic))

    def test_hac_requires_a_chronologically_sorted_index(self):
        shuffled_index = np.arange(len(self.mkt))[::-1]
        mkt = pd.Series(self.mkt.to_numpy(), index=shuffled_index)
        strat = pd.Series(self.strat.to_numpy(), index=shuffled_index)
        with self.assertRaises(ValueError) as ctx:
            self.engine.analyze_attribution(
                strategy_id="UNSORTED", strategy_returns=strat, market_returns=mkt,
                standard_errors="hac")
        self.assertIn("sorted", str(ctx.exception))

    def test_ols_coefficients_are_invariant_to_row_order(self):
        shuffled_index = np.arange(len(self.mkt))[::-1]
        shuffled = self.engine.analyze_attribution(
            strategy_id="SHUFFLED",
            strategy_returns=pd.Series(self.strat.to_numpy(), index=shuffled_index),
            market_returns=pd.Series(self.mkt.to_numpy(), index=shuffled_index))
        ordered = self.engine.analyze_attribution(
            strategy_id="ORDERED", strategy_returns=self.strat, market_returns=self.mkt)
        self.assertAlmostEqual(shuffled.market_beta, ordered.market_beta, places=10)


class TestUndefinedMetrics(unittest.TestCase):
    """Undefined results are None with a stated reason, never a plausible-looking 0.0."""

    def test_perfect_fit_leaves_t_statistics_undefined(self):
        rng = np.random.default_rng(31)
        mkt = pd.Series(rng.normal(0.0004, 0.011, 300))
        rf_period = 1.02 ** (1.0 / 252.0) - 1.0
        strat = pd.Series(1.5 * (mkt.to_numpy() - rf_period) + rf_period)

        report = StrategyPerformanceAttributionEngine().analyze_attribution(
            strategy_id="NOISELESS", strategy_returns=strat, market_returns=mkt,
            risk_free_rate_annual_pct=2.0)

        self.assertAlmostEqual(report.market_beta, 1.5, places=6)
        self.assertAlmostEqual(report.r_squared, 1.0, places=10)
        self.assertIsNone(report.alpha_t_statistic)
        self.assertIsNone(report.alpha_p_value)
        self.assertIsNone(report.t_critical_value)
        self.assertFalse(report.is_true_alpha_significant)
        self.assertTrue(any("t-statistic" in note for note in report.undefined_metrics))

    def test_constant_return_series_does_not_fabricate_an_r_squared(self):
        """A constant excess-return series makes R-squared 0/0.

        Guarding only on `ss_tot > 0.0` left the floating-point summation residue
        (~1e-38) in the denominator and produced an R-squared of -119.55.
        """
        report = StrategyPerformanceAttributionEngine().analyze_attribution(
            strategy_id="CONSTANT",
            strategy_returns=pd.Series([0.001] * 300),
            market_returns=pd.Series(np.random.default_rng(1).normal(0.0004, 0.011, 300)))

        self.assertEqual(report.r_squared, 0.0)
        self.assertIsNone(report.adjusted_r_squared)
        self.assertTrue(any("r_squared" in note for note in report.undefined_metrics))
        self.assertAlmostEqual(report.total_realized_annual_return_pct, 25.2, places=6)

    def test_negative_total_return_makes_the_alpha_share_undefined(self):
        rng = np.random.default_rng(41)
        mkt = rng.normal(-0.0008, 0.011, 300)
        strat = 1.0 * mkt + 0.01 / 252.0 + rng.normal(0.0, 0.002, 300)
        report = StrategyPerformanceAttributionEngine().analyze_attribution(
            strategy_id="LOSING", strategy_returns=pd.Series(strat),
            market_returns=pd.Series(mkt))

        self.assertLess(report.total_realized_annual_return_pct, 0.0)
        self.assertIsNone(report.alpha_percentage_of_total_return)
        self.assertTrue(any("alpha_percentage_of_total_return" in note
                            for note in report.undefined_metrics))

    def test_positive_total_return_reports_the_alpha_share(self):
        """Hand-worked: the closed-form dataset shifted up by 0.01 per period.

        mean(strat) = 0.055, beta = 1.8 (unchanged), alpha = 0.055 - 1.8*0.025 = 0.01.
        Annualized: alpha = 252.0%, total = 1386.0%, share = 252/1386 = 18.1818...%
        """
        report = StrategyPerformanceAttributionEngine().analyze_attribution(
            strategy_id="WINNING",
            strategy_returns=pd.Series([0.03, 0.04, 0.07, 0.08]),
            market_returns=pd.Series([0.01, 0.02, 0.03, 0.04]),
            risk_free_rate_annual_pct=0.0)
        self.assertAlmostEqual(report.annualized_jensens_alpha_pct, 252.0, places=6)
        self.assertAlmostEqual(report.total_realized_annual_return_pct, 1386.0, places=6)
        self.assertAlmostEqual(report.alpha_percentage_of_total_return, 18.18, places=2)
        self.assertEqual(report.undefined_metrics, ())


class TestSampleSizeHandling(unittest.TestCase):

    def test_short_sample_is_flagged_not_silently_annualized(self):
        mkt, strat = _synthetic_capm(n=60)
        report = StrategyPerformanceAttributionEngine().analyze_attribution(
            strategy_id="SHORT", strategy_returns=strat, market_returns=mkt)
        self.assertTrue(report.insufficient_history_warning)
        self.assertIn("WARNING", report.audit_notes)

    def test_full_year_is_not_flagged(self):
        mkt, strat = _synthetic_capm(n=252)
        report = StrategyPerformanceAttributionEngine().analyze_attribution(
            strategy_id="FULL_YEAR", strategy_returns=strat, market_returns=mkt)
        self.assertFalse(report.insufficient_history_warning)

    def test_monthly_frequency_uses_the_correct_t_threshold(self):
        # 60 monthly observations, 2 regressors -> 58 df. The two-sided 5% Student-t
        # critical value is 2.0017 (standard t-table), not the large-sample 1.96.
        rng = np.random.default_rng(13)
        mkt = pd.Series(rng.normal(0.007, 0.04, 60))
        strat = pd.Series(1.1 * mkt.to_numpy() + 0.004 + rng.normal(0.0, 0.01, 60))
        report = StrategyPerformanceAttributionEngine(
            periods_per_year=12).analyze_attribution(
                strategy_id="MONTHLY", strategy_returns=strat, market_returns=mkt)
        self.assertEqual(report.degrees_of_freedom, 58)
        self.assertAlmostEqual(report.t_critical_value, 2.0017, places=4)
        self.assertGreater(report.t_critical_value, 1.96)

    def test_significance_level_is_configurable(self):
        mkt, strat = _synthetic_capm(n=504)
        strict = StrategyPerformanceAttributionEngine(
            significance_level=0.01).analyze_attribution(
                strategy_id="STRICT", strategy_returns=strat, market_returns=mkt)
        self.assertEqual(strict.significance_level, 0.01)
        self.assertAlmostEqual(strict.t_critical_value, 2.5857, places=3)


class TestInputValidation(unittest.TestCase):

    def setUp(self):
        self.engine = StrategyPerformanceAttributionEngine()
        self.mkt, self.strat = _synthetic_capm(n=60)

    def _expect_value_error(self, fragment, **kwargs):
        params = {"strategy_id": "V", "strategy_returns": self.strat,
                  "market_returns": self.mkt}
        params.update(kwargs)
        with self.assertRaises(ValueError) as ctx:
            self.engine.analyze_attribution(**params)
        self.assertIn(fragment, str(ctx.exception))

    def test_empty_series_is_rejected(self):
        empty = pd.Series([], dtype=float)
        self._expect_value_error("aligned observation", strategy_returns=empty,
                                 market_returns=empty)

    def test_too_few_observations_for_the_regressor_count_is_rejected(self):
        self._expect_value_error(
            "aligned observation",
            strategy_returns=pd.Series([0.01, 0.02, 0.03]),
            market_returns=pd.Series([0.01, 0.02, 0.03]),
            smb_returns=pd.Series([0.001, 0.002, 0.003]),
            hml_returns=pd.Series([0.001, 0.002, 0.003]))

    def test_non_overlapping_indices_are_rejected(self):
        self._expect_value_error(
            "aligned observation",
            strategy_returns=pd.Series([0.01] * 30, index=range(0, 30)),
            market_returns=pd.Series([0.01] * 30, index=range(100, 130)))

    def test_collinear_factors_are_rejected(self):
        self._expect_value_error("rank deficient", smb_returns=self.mkt.copy())

    def test_infinite_values_are_rejected(self):
        poisoned = self.strat.copy()
        poisoned.iloc[3] = np.inf
        self._expect_value_error("infinite", strategy_returns=poisoned)

    def test_non_numeric_values_are_rejected(self):
        poisoned = self.strat.astype(object)
        poisoned.iloc[3] = "n/a"
        self._expect_value_error("non-numeric", strategy_returns=poisoned)

    def test_duplicate_index_labels_are_rejected(self):
        duplicated = pd.Series(self.strat.to_numpy(), index=[0] * len(self.strat))
        self._expect_value_error("duplicate index", strategy_returns=duplicated)

    def test_non_series_input_is_rejected(self):
        with self.assertRaises(TypeError):
            self.engine.analyze_attribution(
                strategy_id="V", strategy_returns=self.strat.to_numpy(),
                market_returns=self.mkt)

    def test_unknown_standard_error_type_is_rejected(self):
        self._expect_value_error("standard_errors must be", standard_errors="robust")

    def test_hac_lags_without_hac_is_rejected(self):
        self._expect_value_error("only meaningful", hac_lags=3)

    def test_negative_hac_lags_is_rejected(self):
        self._expect_value_error("non-negative", standard_errors="hac", hac_lags=-1)

    def test_hac_lags_beyond_the_sample_is_rejected(self):
        self._expect_value_error(
            "must be smaller than", standard_errors="hac", hac_lags=len(self.strat))

    def test_impossible_risk_free_rate_is_rejected(self):
        self._expect_value_error("must exceed -100", risk_free_rate_annual_pct=-120.0)
        self._expect_value_error("must be finite", risk_free_rate_annual_pct=float("nan"))

    def test_invalid_engine_configuration_is_rejected(self):
        for bad in (0, -1, 2.5, True):
            with self.assertRaises(ValueError):
                StrategyPerformanceAttributionEngine(periods_per_year=bad)
        for bad in (0.0, 1.0, -0.1, 1.5):
            with self.assertRaises(ValueError):
                StrategyPerformanceAttributionEngine(significance_level=bad)

    def test_missing_observations_are_dropped_not_treated_as_zero(self):
        strat = self.strat.copy()
        strat.iloc[5] = np.nan
        report = self.engine.analyze_attribution(
            strategy_id="NAN", strategy_returns=strat, market_returns=self.mkt)
        self.assertEqual(report.observations, len(self.strat) - 1)
        self.assertEqual(report.dropped_observations, 1)


class TestReportShape(unittest.TestCase):
    """The report is consumed by agents; its contract must stay stable and typed."""

    def test_report_and_breakdown_types(self):
        mkt, strat = _synthetic_capm(n=300)
        report = StrategyPerformanceAttributionEngine().analyze_attribution(
            strategy_id="SHAPE", strategy_returns=strat, market_returns=mkt,
            smb_returns=pd.Series(np.linspace(-0.004, 0.004, 300)))
        self.assertIsInstance(report, PerformanceAttributionReport)
        self.assertEqual(len(report.factor_breakdown), 2)
        for row in report.factor_breakdown:
            self.assertIsInstance(row, FactorAttributionBreakdown)
            self.assertIsInstance(row.beta_exposure, float)
            self.assertIsInstance(row.is_statistically_significant, bool)
        self.assertIsInstance(report.is_true_alpha_significant, bool)
        self.assertIsInstance(report.undefined_metrics, tuple)
        self.assertEqual(report.strategy_id, "SHAPE")
        self.assertIn("SHAPE", report.audit_notes)


if __name__ == "__main__":
    unittest.main()
