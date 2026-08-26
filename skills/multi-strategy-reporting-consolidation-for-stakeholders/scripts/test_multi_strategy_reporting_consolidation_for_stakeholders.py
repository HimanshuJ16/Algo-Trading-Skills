import logging
import math
import unittest

from multi_strategy_reporting_consolidation_for_stakeholders import (
    ConsolidationError,
    MultiStrategyReportingConsolidatorEngine,
    ReportingConfig,
    StrategyTelemetry,
)

# Several cases deliberately trigger report warnings; keep them out of the test
# output instead of falling through to logging's lastResort stderr handler.
logging.getLogger(
    "multi_strategy_reporting_consolidation_for_stakeholders"
).addHandler(logging.NullHandler())

# ---------------------------------------------------------------------------
# Headline scenario, constructed so every expected value is derivable by hand.
#
#   A_t = 0.0006 + 0.010 * s_t          s_t = +1 on even t, -1 on odd t
#   B_t = 0.0004 - 0.004 * s_t          (perfectly anti-correlated with A)
#   w_A = w_B = 0.5
#
# Each series deviates from its own mean by a constant amplitude, so its sample
# standard deviation is amplitude * sqrt(n / (n - 1)):
#
#   sigma_A ~ 0.010,  sigma_B ~ 0.004,  sigma_P ~ 0.5 * (0.010 - 0.004) = 0.003
#
# The diversification ratio is therefore exact and independent of n, of the
# (n-1) correction, and of the annualization factor -- they all cancel:
#
#   DR = (0.5*0.010 + 0.5*0.004) / 0.003 = 0.007 / 0.003 = 7/3 = 2.3333...
#
# and because the portfolio series has mean 0.0005/day, the Sharpe ratio is
# (0.0005 * 252 - 0.04) / sigma_P_annualized = 0.086 / 0.0481070 = 1.7877.
# ---------------------------------------------------------------------------
N_OBS = 50
RETS_A = [0.0006 + (0.010 if t % 2 == 0 else -0.010) for t in range(N_OBS)]
RETS_B = [0.0004 - (0.004 if t % 2 == 0 else -0.004) for t in range(N_OBS)]


def _telemetry(**overrides):
    base = dict(
        strategy_id="STAT_ARB_01",
        strategy_name="Statistical Arbitrage",
        allocated_capital_usd=500000.0,
        realized_pnl_usd=15000.0,
        unrealized_pnl_usd=3000.0,
        daily_returns=list(RETS_A),
        max_drawdown_pct=2.5,
    )
    base.update(overrides)
    return StrategyTelemetry(**base)


class TestConsolidationHeadlineMetrics(unittest.TestCase):

    def setUp(self):
        self.engine = MultiStrategyReportingConsolidatorEngine()
        self.strat_a = _telemetry()
        self.strat_b = _telemetry(
            strategy_id="TREND_01",
            strategy_name="CTA Trend Following",
            realized_pnl_usd=5000.0,
            unrealized_pnl_usd=2000.0,
            daily_returns=list(RETS_B),
            max_drawdown_pct=3.0,
        )
        self.report = self.engine.consolidate_reports([self.strat_a, self.strat_b])

    def test_capital_and_pnl_aggregation(self):
        self.assertEqual(self.report.status, "REPORT_CONSOLIDATED_SUCCESS")
        self.assertEqual(self.report.total_allocated_capital_usd, 1000000.0)
        self.assertEqual(self.report.total_net_pnl_usd, 25000.0)
        self.assertEqual(self.report.portfolio_return_pct, 2.5)
        self.assertEqual(self.report.observations, N_OBS)

    def test_diversification_ratio_matches_closed_form(self):
        # 0.007 / 0.003 exactly -- see the derivation above.
        self.assertAlmostEqual(self.report.diversification_ratio, 2.33, places=2)
        self.assertEqual(self.report.portfolio_annualized_volatility_pct, 4.81)
        self.assertEqual(self.report.weighted_sum_volatility_pct, 11.22)
        self.assertLess(
            self.report.portfolio_annualized_volatility_pct,
            self.report.weighted_sum_volatility_pct,
        )

    def test_portfolio_sharpe_is_not_an_average_of_sleeve_sharpes(self):
        self.assertEqual(self.report.portfolio_sharpe_ratio, 1.79)
        sleeve_sharpes = [c.sharpe_ratio for c in self.report.strategy_contributions]
        self.assertEqual(sleeve_sharpes, [0.69, 0.95])
        naive_average = sum(sleeve_sharpes) / len(sleeve_sharpes)
        self.assertGreater(self.report.portfolio_sharpe_ratio, naive_average)

    def test_sleeve_volatilities_and_weights(self):
        a, b = self.report.strategy_contributions
        self.assertEqual((a.weight_pct, b.weight_pct), (50.0, 50.0))
        self.assertEqual(a.annualized_volatility_pct, 16.04)
        self.assertEqual(b.annualized_volatility_pct, 6.41)
        self.assertEqual((a.net_pnl_usd, b.net_pnl_usd), (18000.0, 7000.0))
        self.assertEqual((a.pnl_contribution_pct, b.pnl_contribution_pct), (72.0, 28.0))

    def test_reported_drawdown_is_passed_through_and_window_drawdown_recomputed(self):
        a, b = self.report.strategy_contributions
        self.assertEqual((a.reported_max_drawdown_pct, b.reported_max_drawdown_pct), (2.5, 3.0))
        self.assertEqual((a.window_max_drawdown_pct, b.window_max_drawdown_pct), (0.94, 0.36))
        self.assertEqual(self.report.max_strategy_max_drawdown_pct, 0.94)

    def test_series_implied_return_reconciles_against_pnl_return(self):
        # Compounded joint series: +2.51% vs +2.50% booked PnL on allocated capital.
        self.assertEqual(self.report.series_implied_return_pct, 2.51)


class TestDegenerateVolatility(unittest.TestCase):
    """
    Regression: perfectly offsetting sleeves used to be assigned a placeholder daily
    standard deviation of 0.0001, which turned an undefined Sharpe ratio into a
    reported 371.67 and an undefined diversification ratio into 74.23x.
    """

    def setUp(self):
        self.engine = MultiStrategyReportingConsolidatorEngine()

    def test_perfectly_offsetting_sleeves_report_nan_not_a_giant_sharpe(self):
        rets_a = [0.01, -0.005] * 25          # equal-weight blend is a constant +0.25%/day
        rets_b = [-0.005, 0.01] * 25
        report = self.engine.consolidate_reports([
            _telemetry(daily_returns=rets_a),
            _telemetry(strategy_id="TREND_01", daily_returns=rets_b),
        ])
        self.assertEqual(report.portfolio_annualized_volatility_pct, 0.0)
        self.assertTrue(math.isnan(report.portfolio_sharpe_ratio))
        self.assertTrue(math.isnan(report.diversification_ratio))
        self.assertTrue(any("undefined, not large" in w for w in report.warnings))

    def test_flat_sleeve_gets_nan_sharpe_and_zero_volatility(self):
        report = self.engine.consolidate_reports([
            _telemetry(daily_returns=[0.0] * 30),
            _telemetry(strategy_id="TREND_01", daily_returns=[0.001, -0.001] * 15),
        ])
        flat = report.strategy_contributions[0]
        self.assertEqual(flat.annualized_volatility_pct, 0.0)
        self.assertTrue(math.isnan(flat.sharpe_ratio))
        # The portfolio itself still has volatility, so its metrics stay defined.
        self.assertFalse(math.isnan(report.portfolio_sharpe_ratio))
        self.assertTrue(any("STAT_ARB_01" in w for w in report.warnings))


class TestDiversificationRatioProperties(unittest.TestCase):

    def setUp(self):
        self.engine = MultiStrategyReportingConsolidatorEngine()

    def test_single_strategy_ratio_is_exactly_one(self):
        report = self.engine.consolidate_reports([_telemetry()])
        self.assertEqual(report.diversification_ratio, 1.0)
        self.assertEqual(
            report.portfolio_annualized_volatility_pct,
            report.weighted_sum_volatility_pct,
        )

    def test_identical_sleeves_give_no_diversification_benefit(self):
        # Perfectly correlated sleeves: sigma_p == sum w_k sigma_k, so DR == 1.0
        # regardless of how capital is split between them.
        report = self.engine.consolidate_reports([
            _telemetry(allocated_capital_usd=750000.0),
            _telemetry(strategy_id="CLONE_01", allocated_capital_usd=250000.0),
        ])
        self.assertEqual(report.diversification_ratio, 1.0)


class TestPortfolioDrawdown(unittest.TestCase):

    def test_portfolio_drawdown_is_not_the_worst_sleeve_drawdown(self):
        # Sleeves trough on different days: each loses 10% once, the equal-weight
        # portfolio loses 5% twice -> 1 - 0.95 * 0.95 = 9.75%, not 10%.
        engine = MultiStrategyReportingConsolidatorEngine()
        report = engine.consolidate_reports([
            _telemetry(daily_returns=[-0.10, 0.0, 0.0, 0.0]),
            _telemetry(strategy_id="TREND_01", daily_returns=[0.0, 0.0, -0.10, 0.0]),
        ])
        self.assertEqual(report.max_strategy_max_drawdown_pct, 10.0)
        self.assertEqual(report.portfolio_max_drawdown_pct, 9.75)


class TestPnlContributionEdgeCases(unittest.TestCase):

    def setUp(self):
        self.engine = MultiStrategyReportingConsolidatorEngine()

    def test_negative_total_pnl_inverts_contribution_sign_and_warns(self):
        report = self.engine.consolidate_reports([
            _telemetry(realized_pnl_usd=-150000.0, unrealized_pnl_usd=0.0),
            _telemetry(
                strategy_id="TREND_01",
                realized_pnl_usd=50000.0,
                unrealized_pnl_usd=0.0,
                daily_returns=list(RETS_B),
            ),
        ])
        losing, winning = report.strategy_contributions
        self.assertEqual(report.total_net_pnl_usd, -100000.0)
        self.assertEqual(losing.pnl_contribution_pct, 150.0)
        self.assertEqual(winning.pnl_contribution_pct, -50.0)
        self.assertTrue(any("inverts sign" in w for w in report.warnings))

    def test_zero_total_pnl_yields_nan_contributions(self):
        report = self.engine.consolidate_reports([
            _telemetry(realized_pnl_usd=1000.0, unrealized_pnl_usd=0.0),
            _telemetry(
                strategy_id="TREND_01",
                realized_pnl_usd=-1000.0,
                unrealized_pnl_usd=0.0,
                daily_returns=list(RETS_B),
            ),
        ])
        for contribution in report.strategy_contributions:
            self.assertTrue(math.isnan(contribution.pnl_contribution_pct))
        self.assertTrue(any("exactly zero" in w for w in report.warnings))


class TestAnnualizationWarning(unittest.TestCase):

    def test_sub_year_window_warns_about_gips_2_a_12(self):
        engine = MultiStrategyReportingConsolidatorEngine()
        report = engine.consolidate_reports([_telemetry()])
        self.assertEqual(report.observations, 50)
        self.assertTrue(any("2.A.12" in w for w in report.warnings))

    def test_full_year_window_is_not_flagged(self):
        engine = MultiStrategyReportingConsolidatorEngine()
        rets = [0.0006 + (0.010 if t % 2 == 0 else -0.010) for t in range(252)]
        report = engine.consolidate_reports([_telemetry(daily_returns=rets)])
        self.assertEqual(report.observations, 252)
        self.assertEqual(report.warnings, [])

    def test_annualization_follows_configured_trading_days(self):
        # Halving the annualization factor scales annualized volatility by sqrt(1/2)
        # and leaves the diversification ratio (a ratio of volatilities) unchanged.
        base = MultiStrategyReportingConsolidatorEngine().consolidate_reports([_telemetry()])
        halved = MultiStrategyReportingConsolidatorEngine(
            ReportingConfig(trading_days_per_year=126)
        ).consolidate_reports([_telemetry()])
        self.assertAlmostEqual(
            halved.portfolio_annualized_volatility_pct,
            base.portfolio_annualized_volatility_pct / math.sqrt(2.0),
            places=2,
        )
        self.assertEqual(halved.diversification_ratio, base.diversification_ratio)


class TestInputValidation(unittest.TestCase):

    def setUp(self):
        self.engine = MultiStrategyReportingConsolidatorEngine()

    def test_empty_strategy_list_rejected(self):
        with self.assertRaises(ConsolidationError):
            self.engine.consolidate_reports([])

    def test_unequal_series_lengths_rejected_not_truncated(self):
        # Regression: the shorter (late-launch) sleeve used to be paired with the
        # oldest observations of the longer one, silently corrupting every
        # co-movement figure.
        with self.assertRaises(ConsolidationError) as ctx:
            self.engine.consolidate_reports([
                _telemetry(daily_returns=[0.001] * 60),
                _telemetry(strategy_id="TREND_01", daily_returns=[0.002] * 40),
            ])
        self.assertIn("aligned by date", str(ctx.exception))

    def test_single_observation_rejected(self):
        with self.assertRaises(ConsolidationError):
            self.engine.consolidate_reports([_telemetry(daily_returns=[0.001])])

    def test_empty_return_series_rejected(self):
        with self.assertRaises(ConsolidationError):
            self.engine.consolidate_reports([_telemetry(daily_returns=[])])

    def test_non_finite_return_rejected(self):
        for bad in (float("nan"), float("inf"), float("-inf")):
            with self.subTest(bad=bad):
                with self.assertRaises(ConsolidationError):
                    self.engine.consolidate_reports(
                        [_telemetry(daily_returns=[0.001, bad, 0.002])]
                    )

    def test_return_below_minus_one_rejected(self):
        # A percent-scaled series (-5 meaning -5%) would otherwise drive the
        # compounded equity path negative and make drawdown meaningless.
        with self.assertRaises(ConsolidationError) as ctx:
            self.engine.consolidate_reports([_telemetry(daily_returns=[0.01, -5.0])])
        self.assertIn("-100%", str(ctx.exception))

    def test_total_wipeout_of_minus_one_is_allowed(self):
        report = self.engine.consolidate_reports([_telemetry(daily_returns=[-1.0, 0.0, 0.0])])
        self.assertEqual(report.portfolio_max_drawdown_pct, 100.0)

    def test_negative_allocated_capital_rejected(self):
        with self.assertRaises(ConsolidationError):
            self.engine.consolidate_reports([
                _telemetry(allocated_capital_usd=1500000.0),
                _telemetry(strategy_id="TREND_01", allocated_capital_usd=-500000.0),
            ])

    def test_zero_total_capital_rejected(self):
        with self.assertRaises(ConsolidationError):
            self.engine.consolidate_reports([_telemetry(allocated_capital_usd=0.0)])

    def test_non_finite_capital_rejected(self):
        with self.assertRaises(ConsolidationError):
            self.engine.consolidate_reports([_telemetry(allocated_capital_usd=float("nan"))])

    def test_non_finite_pnl_rejected(self):
        with self.assertRaises(ConsolidationError):
            self.engine.consolidate_reports([_telemetry(realized_pnl_usd=float("inf"))])

    def test_duplicate_strategy_id_rejected(self):
        with self.assertRaises(ConsolidationError) as ctx:
            self.engine.consolidate_reports([_telemetry(), _telemetry()])
        self.assertIn("Duplicate strategy_id", str(ctx.exception))

    def test_invalid_config_rejected(self):
        with self.assertRaises(ConsolidationError):
            MultiStrategyReportingConsolidatorEngine(ReportingConfig(trading_days_per_year=0))
        with self.assertRaises(ConsolidationError):
            MultiStrategyReportingConsolidatorEngine(
                ReportingConfig(risk_free_rate_ann=float("nan"))
            )

    def test_consolidation_error_is_a_value_error(self):
        with self.assertRaises(ValueError):
            self.engine.consolidate_reports([])


if __name__ == '__main__':
    unittest.main()
