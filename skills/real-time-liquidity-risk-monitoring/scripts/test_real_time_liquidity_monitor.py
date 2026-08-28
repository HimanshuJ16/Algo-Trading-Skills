"""Unit tests for real-time-liquidity-risk-monitoring.

Expected values are derived by hand from the published formulas, not by re-running the
implementation's own arithmetic:

    DTL   = |qty| / (participation * ADV)
    COL   = 0.5 * Notional * relative_spread            (BDSS 1999 Eq. 4, a = 0)
          + 0.5 * Notional * k * DTL                    (impact proxy)
    L-VaR = baseline VaR + sum(COL)
"""
import math
import unittest

from real_time_liquidity_monitor import (
    Config,
    PortfolioPositionLiquidity,
    RealTimeLiquidityMonitorEngine,
    STATUS_ALERT,
    STATUS_HEALTHY,
    STATUS_NO_POSITIONS,
)


def position(**overrides) -> PortfolioPositionLiquidity:
    """A liquid, unstressed baseline position; override one field per test."""
    base = dict(
        symbol="AAPL",
        position_size=10000.0,
        current_price=100.0,
        adv=500000.0,
        bid_ask_spread=0.05,
        l2_depth_top3=10000.0,
        normal_spread=0.05,
        normal_l2_depth=10000.0,
    )
    base.update(overrides)
    return PortfolioPositionLiquidity(**base)


class TestConfigValidation(unittest.TestCase):

    def test_rejects_participation_above_one(self):
        # A cap > 100% of ADV claims more volume than the session trades.
        with self.assertRaises(ValueError):
            Config(max_participation_pct=1.5)

    def test_rejects_zero_participation(self):
        # Would make DTL a division by zero.
        with self.assertRaises(ValueError):
            Config(max_participation_pct=0.0)

    def test_rejects_non_finite_threshold(self):
        with self.assertRaises(ValueError):
            Config(max_dtl_threshold_days=float("nan"))

    def test_rejects_negative_impact_coefficient(self):
        with self.assertRaises(ValueError):
            Config(market_impact_coeff_per_day=-0.1)

    def test_rejects_depth_threshold_above_one(self):
        # A depth drop cannot exceed 100%.
        with self.assertRaises(ValueError):
            Config(depth_drop_threshold_pct=1.5)

    def test_accepts_zero_impact_coefficient(self):
        # k = 0 disables the uncalibrated impact term, leaving the BDSS half-spread only.
        self.assertEqual(Config(market_impact_coeff_per_day=0.0).market_impact_coeff_per_day, 0.0)


class TestHealthyPortfolio(unittest.TestCase):

    def setUp(self):
        self.engine = RealTimeLiquidityMonitorEngine()

    def test_liquid_position_reports_healthy(self):
        # DTL = 10,000 / (0.10 * 500,000) = 0.2 days; spread 1.0x; depth unchanged.
        report = self.engine.audit_portfolio_liquidity([position()], baseline_var_usd=100000.0)
        self.assertEqual(report.status, STATUS_HEALTHY)
        self.assertEqual(report.total_dtl_breaches, 0)
        self.assertEqual(report.total_spread_spikes, 0)
        self.assertEqual(report.total_depth_collapses, 0)
        self.assertAlmostEqual(report.max_days_to_liquidate, 0.2, places=6)

    def test_cost_of_liquidity_matches_hand_computation(self):
        # Notional = 1,000,000. rel_spread = 0.05/100 = 0.0005. DTL = 0.2.
        # spread cost = 0.5 * 1e6 * 0.0005            =    250.00
        # impact cost = 0.5 * 1e6 * 0.10 * 0.2        = 10,000.00
        # COL = 10,250.00 ; L-VaR = 100,000 + 10,250  = 110,250.00
        report = self.engine.audit_portfolio_liquidity([position()], baseline_var_usd=100000.0)
        metric = report.position_metrics[0]
        self.assertAlmostEqual(metric.spread_cost_usd, 250.00, places=2)
        self.assertAlmostEqual(metric.impact_cost_usd, 10000.00, places=2)
        self.assertAlmostEqual(metric.l_var_contribution_usd, 10250.00, places=2)
        self.assertAlmostEqual(report.total_cost_of_liquidity_usd, 10250.00, places=2)
        self.assertAlmostEqual(report.portfolio_l_var_usd, 110250.00, places=2)
        self.assertAlmostEqual(report.baseline_var_usd, 100000.00, places=2)

    def test_zero_impact_coefficient_leaves_bdss_half_spread_only(self):
        engine = RealTimeLiquidityMonitorEngine(Config(market_impact_coeff_per_day=0.0))
        report = engine.audit_portfolio_liquidity([position()], baseline_var_usd=0.0)
        # BDSS Eq. 4 with a = 0: 0.5 * 1,000,000 * 0.0005 = 250.00, and nothing else.
        self.assertAlmostEqual(report.total_cost_of_liquidity_usd, 250.00, places=2)
        self.assertAlmostEqual(report.portfolio_l_var_usd, 250.00, places=2)

    def test_short_position_consumes_liquidity_identically_to_long(self):
        long_report = self.engine.audit_portfolio_liquidity(
            [position(position_size=10000.0)], baseline_var_usd=0.0)
        short_report = self.engine.audit_portfolio_liquidity(
            [position(position_size=-10000.0)], baseline_var_usd=0.0)
        self.assertEqual(long_report.max_days_to_liquidate, short_report.max_days_to_liquidate)
        self.assertEqual(long_report.total_portfolio_notional_usd,
                         short_report.total_portfolio_notional_usd)
        self.assertEqual(long_report.portfolio_l_var_usd, short_report.portfolio_l_var_usd)


class TestBreachDetection(unittest.TestCase):

    def setUp(self):
        self.engine = RealTimeLiquidityMonitorEngine()

    def test_dtl_and_spread_spike_and_depth_collapse(self):
        # DTL = 100,000 / (0.10 * 200,000) = 5.0 days (>= 2.0)
        # spread 0.25 / 0.05 = 5.0x (>= 2.0); depth 2,000 / 10,000 -> 80% drop (>= 50%)
        report = self.engine.audit_portfolio_liquidity(
            [position(symbol="ILLIQ", position_size=100000.0, adv=200000.0,
                      bid_ask_spread=0.25, l2_depth_top3=2000.0)],
            baseline_var_usd=100000.0,
        )
        self.assertEqual(report.status, STATUS_ALERT)
        self.assertEqual(report.total_dtl_breaches, 1)
        self.assertEqual(report.total_spread_spikes, 1)
        self.assertEqual(report.total_depth_collapses, 1)
        metric = report.position_metrics[0]
        self.assertAlmostEqual(metric.days_to_liquidate, 5.0, places=6)
        self.assertAlmostEqual(metric.spread_ratio, 5.0, places=6)
        self.assertAlmostEqual(metric.depth_drop_pct, 80.0, places=1)

    def test_thresholds_are_inclusive_on_all_three_checks(self):
        # Regression: DTL used a strict '>' while spread/depth used '>=', so a position
        # sitting exactly on the DTL limit reported no breach while the other two fired.
        # DTL = 40,000 / (0.10 * 200,000) = 2.0 exactly; spread 0.10/0.05 = 2.0x exactly;
        # depth 5,000 / 10,000 = 50.0% drop exactly.
        report = self.engine.audit_portfolio_liquidity(
            [position(symbol="EDGE", position_size=40000.0, adv=200000.0,
                      bid_ask_spread=0.10, l2_depth_top3=5000.0)],
            baseline_var_usd=0.0,
        )
        metric = report.position_metrics[0]
        self.assertTrue(metric.dtl_breached)
        self.assertTrue(metric.spread_spike_flag)
        self.assertTrue(metric.depth_collapse_flag)
        self.assertEqual(report.status, STATUS_ALERT)

    def test_just_below_thresholds_does_not_alert(self):
        report = self.engine.audit_portfolio_liquidity(
            [position(symbol="NEAR", position_size=39999.0, adv=200000.0,
                      bid_ask_spread=0.0999, l2_depth_top3=5001.0)],
            baseline_var_usd=0.0,
        )
        self.assertEqual(report.status, STATUS_HEALTHY)
        self.assertEqual(report.total_dtl_breaches, 0)
        self.assertEqual(report.total_spread_spikes, 0)
        self.assertEqual(report.total_depth_collapses, 0)

    def test_depth_above_normal_reports_zero_drop_not_negative(self):
        report = self.engine.audit_portfolio_liquidity(
            [position(l2_depth_top3=25000.0)], baseline_var_usd=0.0)
        self.assertEqual(report.position_metrics[0].depth_drop_pct, 0.0)
        self.assertFalse(report.position_metrics[0].depth_collapse_flag)

    def test_max_dtl_is_the_worst_symbol_not_the_last(self):
        report = self.engine.audit_portfolio_liquidity(
            [position(symbol="SLOW", position_size=100000.0, adv=200000.0),  # 5.0 days
             position(symbol="FAST", position_size=1000.0, adv=500000.0)],   # 0.02 days
            baseline_var_usd=0.0,
        )
        self.assertAlmostEqual(report.max_days_to_liquidate, 5.0, places=6)
        self.assertEqual(report.total_dtl_breaches, 1)

    def test_zero_spread_is_valid_and_costs_nothing(self):
        # A locked/zero-spread book is a legitimate observation, not an error.
        report = self.engine.audit_portfolio_liquidity(
            [position(bid_ask_spread=0.0)], baseline_var_usd=0.0)
        self.assertEqual(report.position_metrics[0].spread_ratio, 0.0)
        self.assertEqual(report.position_metrics[0].spread_cost_usd, 0.0)
        self.assertFalse(report.position_metrics[0].spread_spike_flag)

    def test_fully_evaporated_depth_is_a_hundred_percent_collapse(self):
        report = self.engine.audit_portfolio_liquidity(
            [position(l2_depth_top3=0.0)], baseline_var_usd=0.0)
        self.assertEqual(report.position_metrics[0].depth_drop_pct, 100.0)
        self.assertTrue(report.position_metrics[0].depth_collapse_flag)


class TestInputValidation(unittest.TestCase):
    """Regression tests: every case below previously returned a fabricated report."""

    def setUp(self):
        self.engine = RealTimeLiquidityMonitorEngine()

    def test_nan_spread_raises_instead_of_silently_clearing_the_flag(self):
        # Before: NaN >= threshold is False, so the spike never fired, the portfolio
        # reported LIQUIDITY_HEALTHY on that dimension, and L-VaR became NaN.
        with self.assertRaises(ValueError):
            self.engine.audit_portfolio_liquidity(
                [position(bid_ask_spread=float("nan"))], baseline_var_usd=100000.0)

    def test_nan_adv_raises(self):
        with self.assertRaises(ValueError):
            self.engine.audit_portfolio_liquidity(
                [position(adv=float("nan"))], baseline_var_usd=100000.0)

    def test_infinite_position_size_raises(self):
        with self.assertRaises(ValueError):
            self.engine.audit_portfolio_liquidity(
                [position(position_size=float("inf"))], baseline_var_usd=100000.0)

    def test_zero_adv_raises_instead_of_clamping_to_one_share_per_day(self):
        # Before: max(1.0, 0 * 0.10) = 1.0 share/day, so 10,000 shares "took" 10,000
        # days and the L-VaR ran to billions.
        with self.assertRaises(ValueError):
            self.engine.audit_portfolio_liquidity([position(adv=0.0)], baseline_var_usd=100000.0)

    def test_negative_adv_raises(self):
        with self.assertRaises(ValueError):
            self.engine.audit_portfolio_liquidity([position(adv=-500000.0)], baseline_var_usd=0.0)

    def test_zero_price_raises_instead_of_clamping_to_one_cent(self):
        # Before: max(0.01, 0.0) inflated the relative spread by up to 10,000x.
        with self.assertRaises(ValueError):
            self.engine.audit_portfolio_liquidity(
                [position(current_price=0.0)], baseline_var_usd=0.0)

    def test_zero_normal_spread_raises(self):
        # Before: max(0.0001, 0.0) produced an arbitrary spread ratio in the hundreds.
        with self.assertRaises(ValueError):
            self.engine.audit_portfolio_liquidity(
                [position(normal_spread=0.0)], baseline_var_usd=0.0)

    def test_zero_normal_depth_raises(self):
        with self.assertRaises(ValueError):
            self.engine.audit_portfolio_liquidity(
                [position(normal_l2_depth=0.0)], baseline_var_usd=0.0)

    def test_negative_spread_raises(self):
        # A crossed or corrupt book is a data fault, not a negative liquidity cost.
        with self.assertRaises(ValueError):
            self.engine.audit_portfolio_liquidity(
                [position(bid_ask_spread=-0.05)], baseline_var_usd=0.0)

    def test_negative_depth_raises(self):
        with self.assertRaises(ValueError):
            self.engine.audit_portfolio_liquidity(
                [position(l2_depth_top3=-100.0)], baseline_var_usd=0.0)

    def test_empty_symbol_raises(self):
        with self.assertRaises(ValueError):
            self.engine.audit_portfolio_liquidity([position(symbol="   ")], baseline_var_usd=0.0)

    def test_duplicate_symbol_raises_instead_of_double_counting(self):
        # Before: the same symbol twice doubled notional and doubled the L-VaR add-on.
        with self.assertRaises(ValueError):
            self.engine.audit_portfolio_liquidity(
                [position(symbol="AAPL"), position(symbol="AAPL")], baseline_var_usd=0.0)

    def test_negative_baseline_var_raises(self):
        with self.assertRaises(ValueError):
            self.engine.audit_portfolio_liquidity([position()], baseline_var_usd=-1.0)

    def test_nan_baseline_var_raises(self):
        with self.assertRaises(ValueError):
            self.engine.audit_portfolio_liquidity([position()], baseline_var_usd=float("nan"))

    def test_baseline_var_is_required(self):
        # Before: it defaulted to $100,000, fabricating a VaR for forgetful callers.
        with self.assertRaises(TypeError):
            self.engine.audit_portfolio_liquidity([position()])

    def test_none_positions_raises(self):
        with self.assertRaises(ValueError):
            self.engine.audit_portfolio_liquidity(None, baseline_var_usd=0.0)

    def test_validation_runs_before_any_metric_is_computed(self):
        # A bad symbol late in the list must abort the whole audit, not emit a partial
        # report covering only the symbols that happened to be valid.
        with self.assertRaises(ValueError):
            self.engine.audit_portfolio_liquidity(
                [position(symbol="GOOD"), position(symbol="BAD", adv=0.0)],
                baseline_var_usd=0.0,
            )


class TestEmptyPortfolio(unittest.TestCase):

    def test_no_positions_is_distinct_from_healthy(self):
        report = RealTimeLiquidityMonitorEngine().audit_portfolio_liquidity(
            [], baseline_var_usd=250000.0)
        self.assertEqual(report.status, STATUS_NO_POSITIONS)
        self.assertNotEqual(report.status, STATUS_HEALTHY)
        self.assertEqual(report.position_metrics, [])
        self.assertEqual(report.total_cost_of_liquidity_usd, 0.0)
        # No positions means no liquidation cost, so L-VaR collapses to the input VaR.
        self.assertEqual(report.portfolio_l_var_usd, 250000.0)


class TestReportIntegrity(unittest.TestCase):

    def test_portfolio_l_var_equals_baseline_plus_summed_contributions(self):
        engine = RealTimeLiquidityMonitorEngine()
        report = engine.audit_portfolio_liquidity(
            [position(symbol="A", position_size=100000.0, adv=200000.0, bid_ask_spread=0.25),
             position(symbol="B", position_size=5000.0, adv=1000000.0),
             position(symbol="C", position_size=250.0, adv=50000.0, current_price=42.5)],
            baseline_var_usd=100000.0,
        )
        summed = sum(m.l_var_contribution_usd for m in report.position_metrics)
        self.assertAlmostEqual(report.total_cost_of_liquidity_usd, summed, places=2)
        self.assertAlmostEqual(
            report.portfolio_l_var_usd, report.baseline_var_usd + summed, places=2)
        self.assertEqual(len(report.position_metrics), 3)
        for m in report.position_metrics:
            self.assertAlmostEqual(m.l_var_contribution_usd,
                                   m.spread_cost_usd + m.impact_cost_usd, places=2)
            self.assertTrue(math.isfinite(m.days_to_liquidate))

    def test_notional_uses_absolute_size_across_mixed_long_short_book(self):
        engine = RealTimeLiquidityMonitorEngine()
        report = engine.audit_portfolio_liquidity(
            [position(symbol="LONG", position_size=10000.0),
             position(symbol="SHORT", position_size=-10000.0)],
            baseline_var_usd=0.0,
        )
        # Gross, not net: a long and an offsetting short both have to be traded out.
        self.assertAlmostEqual(report.total_portfolio_notional_usd, 2000000.0, places=2)


if __name__ == '__main__':
    unittest.main()
