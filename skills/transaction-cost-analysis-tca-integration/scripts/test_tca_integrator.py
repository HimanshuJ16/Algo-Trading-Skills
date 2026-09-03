"""
Unit tests for transaction-cost-analysis-tca-integration.

Expected values are derived by hand from the definitions, not by re-running the
engine's own arithmetic. Where a test guards a specific defect fixed in 1.1.0 it
is marked REGRESSION and states the pre-1.1 behaviour it must reject.
"""
import logging
import math
import unittest

logging.getLogger("tca_integrator").addHandler(logging.NullHandler())

from tca_integrator import (
    BPS_PER_UNIT,
    DEFAULT_MARKET_IMPACT_GAMMA_BPS,
    FILL_SIZE_REL_TOL,
    SQRT_LAW_MAX_PARTICIPATION,
    SQRT_LAW_MIN_PARTICIPATION,
    TCABacktestIntegrator,
    TCAPortfolioSummary,
    TCATradeBreakdown,
)


class TestCostDecomposition(unittest.TestCase):
    """Per-component arithmetic against hand-computed values."""

    def setUp(self):
        self.tca = TCABacktestIntegrator(
            market_impact_gamma=15.0,
            fixed_commission_bps=2.5,
            max_acceptable_shortfall_bps=50.0,
        )

    def test_buy_decomposition_matches_hand_calculation(self):
        # 10,000 units on 100,000 ADV = 10% participation.
        #   delay        = (150.02 - 150.00) / 150.00 * 1e4 = 1.333333 bps
        #   half-spread  = 0.5 * 0.04 / 150.00 * 1e4        = 1.333333 bps
        #   impact       = 15 * sqrt(0.10)                  = 4.743416 bps
        #   commission                                      = 2.500000 bps
        #   estimated total                                 = 9.910083 bps
        trade = self.tca.analyze_trade(
            symbol="AAPL", action="BUY", order_size=10_000.0, adv=100_000.0,
            p_decision=150.00, p_arrival=150.02, p_fill=150.10, spread=0.04,
        )

        self.assertAlmostEqual(trade.delay_cost_bps, 4.0 / 3.0, places=9)
        self.assertAlmostEqual(trade.spread_cross_bps, 4.0 / 3.0, places=9)
        self.assertAlmostEqual(trade.market_impact_bps, 15.0 * math.sqrt(0.10), places=9)
        self.assertAlmostEqual(trade.commission_bps, 2.5, places=9)
        self.assertAlmostEqual(trade.estimated_shortfall_bps, 9.9100831569, places=7)

    def test_realized_shortfall_uses_the_fill_price(self):
        # REGRESSION: pre-1.1 p_fill was accepted, stored and never read, so a
        # catastrophic fill produced exactly the same shortfall as a perfect one.
        #   realized execution = (150.10 - 150.00) / 150.00 * 1e4 = 6.666667 bps
        #   realized total     = 6.666667 + 2.5                   = 9.166667 bps
        good = self.tca.analyze_trade(
            "AAPL", "BUY", 10_000.0, 100_000.0, 150.00, 150.02, 150.10, 0.04)
        self.assertAlmostEqual(good.realized_execution_cost_bps, 20.0 / 3.0, places=9)
        self.assertAlmostEqual(good.realized_shortfall_bps, 20.0 / 3.0 + 2.5, places=9)

        awful = self.tca.analyze_trade(
            "AAPL", "BUY", 10_000.0, 100_000.0, 150.00, 150.02, 300.00, 0.04)
        self.assertAlmostEqual(awful.realized_execution_cost_bps, 10_000.0, places=6)
        self.assertGreater(
            awful.realized_shortfall_bps, good.realized_shortfall_bps * 100.0)

    def test_model_error_is_realized_minus_estimated(self):
        # 9.166667 - 9.910083 = -0.743416 bps: the model over-predicted cost.
        trade = self.tca.analyze_trade(
            "AAPL", "BUY", 10_000.0, 100_000.0, 150.00, 150.02, 150.10, 0.04)
        self.assertAlmostEqual(trade.model_error_bps, -0.7434164903, places=7)
        self.assertAlmostEqual(
            trade.model_error_bps,
            trade.realized_shortfall_bps - trade.estimated_shortfall_bps, places=12)

    def test_sell_side_signs_are_mirrored(self):
        # For a SELL, a price fall between decision and arrival/fill is adverse,
        # so both delay and realized execution cost must come out POSITIVE.
        #   delay    = -1 * (99.90 - 100) / 100 * 1e4 = 10 bps
        #   realized = -1 * (99.80 - 100) / 100 * 1e4 = 20 bps
        #   spread   = 0.5 * 0.02 / 100 * 1e4         =  1 bps  (side-independent)
        trade = self.tca.analyze_trade(
            "MSFT", "SELL", 1_000.0, 1_000_000.0, 100.00, 99.90, 99.80, 0.02)
        self.assertAlmostEqual(trade.delay_cost_bps, 10.0, places=9)
        self.assertAlmostEqual(trade.realized_execution_cost_bps, 20.0, places=9)
        self.assertAlmostEqual(trade.spread_cross_bps, 1.0, places=9)

    def test_favourable_drift_is_a_negative_cost(self):
        # A BUY filled below the decision price is a gain, not a cost.
        trade = self.tca.analyze_trade(
            "MSFT", "BUY", 1_000.0, 1_000_000.0, 100.00, 100.00, 99.90, 0.0)
        self.assertAlmostEqual(trade.realized_execution_cost_bps, -10.0, places=9)

    def test_impact_scales_as_the_square_root_of_participation(self):
        # Quadrupling participation must exactly double the impact estimate.
        small = self.tca.analyze_trade(
            "X", "BUY", 1_000.0, 100_000.0, 100.0, 100.0, 100.0, 0.0)
        large = self.tca.analyze_trade(
            "X", "BUY", 4_000.0, 100_000.0, 100.0, 100.0, 100.0, 0.0)
        self.assertAlmostEqual(
            large.market_impact_bps, 2.0 * small.market_impact_bps, places=9)

    def test_executed_notional_and_currency_cost(self):
        # 10,000 units at the 150.00 decision price = 1,500,000 notional.
        # 6.666667 bps of that is 1,000.00; commission 2.5 bps is 375.00.
        trade = self.tca.analyze_trade(
            "AAPL", "BUY", 10_000.0, 100_000.0, 150.00, 150.02, 150.10, 0.04)
        self.assertAlmostEqual(trade.executed_notional, 1_500_000.0, places=6)
        self.assertAlmostEqual(trade.realized_cost_currency, 1_375.0, places=6)

    def test_deprecated_alias_still_returns_the_estimate(self):
        trade = self.tca.analyze_trade(
            "AAPL", "BUY", 10_000.0, 100_000.0, 150.00, 150.02, 150.10, 0.04)
        self.assertEqual(trade.total_shortfall_bps, trade.estimated_shortfall_bps)


class TestParticipationRange(unittest.TestCase):
    """The square-root law is flagged outside its fitted range, never clamped."""

    def setUp(self):
        self.tca = TCABacktestIntegrator(
            market_impact_gamma=15.0, fixed_commission_bps=0.0)

    def test_oversized_order_is_not_silently_clamped(self):
        # REGRESSION: pre-1.1 did min(1.0, size/adv), so a 4x-ADV order priced
        # identically to a 1x-ADV order at exactly gamma.
        at_adv = self.tca.analyze_trade(
            "X", "BUY", 100_000.0, 100_000.0, 100.0, 100.0, 100.0, 0.0)
        four_x = self.tca.analyze_trade(
            "X", "BUY", 400_000.0, 100_000.0, 100.0, 100.0, 100.0, 0.0)

        # Pre-1.1 both clamped to phi=1.0 and priced at exactly gamma=15.
        self.assertAlmostEqual(at_adv.market_impact_bps, 15.0, places=9)
        self.assertAlmostEqual(four_x.market_impact_bps, 30.0, places=9)  # 15*sqrt(4)
        # Both are above the 10% cut-off, so both are flagged as extrapolations.
        self.assertTrue(at_adv.participation_out_of_model_range)
        self.assertTrue(four_x.participation_out_of_model_range)

        in_range = self.tca.analyze_trade(
            "X", "BUY", 10_000.0, 100_000.0, 100.0, 100.0, 100.0, 0.0)
        self.assertFalse(in_range.participation_out_of_model_range)
        self.assertAlmostEqual(
            in_range.market_impact_bps, 15.0 * math.sqrt(0.10), places=9)

    def test_range_boundaries_are_inclusive(self):
        at_min = self.tca.analyze_trade(
            "X", "BUY", 1.0, 1.0 / SQRT_LAW_MIN_PARTICIPATION,
            100.0, 100.0, 100.0, 0.0)
        at_max = self.tca.analyze_trade(
            "X", "BUY", SQRT_LAW_MAX_PARTICIPATION * 1_000.0, 1_000.0,
            100.0, 100.0, 100.0, 0.0)
        self.assertFalse(at_min.participation_out_of_model_range)
        self.assertFalse(at_max.participation_out_of_model_range)

    def test_tiny_participation_is_flagged(self):
        tiny = self.tca.analyze_trade(
            "X", "BUY", 1.0, 1_000_000_000.0, 100.0, 100.0, 100.0, 0.0)
        self.assertTrue(tiny.participation_out_of_model_range)

    def test_out_of_range_emits_a_warning(self):
        with self.assertLogs("tca_integrator", level=logging.WARNING) as cm:
            self.tca.analyze_trade(
                "X", "BUY", 400_000.0, 100_000.0, 100.0, 100.0, 100.0, 0.0)
        self.assertTrue(any("square-root law" in m for m in cm.output))


class TestPartialFillsAndOpportunityCost(unittest.TestCase):
    """Perold's opportunity-cost component on the unexecuted remainder."""

    def setUp(self):
        self.tca = TCABacktestIntegrator(
            market_impact_gamma=0.0, fixed_commission_bps=0.0)

    def test_opportunity_cost_on_unfilled_remainder(self):
        # BUY 1,000 at a 100.00 decision price, only 400 filled at 100.00, and
        # the price ended at 110.00.
        #   opportunity  = (110 - 100) / 100 * 1e4        = 1000 bps
        #   fill ratio   = 0.4
        #   total IS     = 0.4*0 + 0.6*1000 + 0.4*0       =  600 bps
        #   opp currency = 0.10 * (600 unfilled * 100.00) = 6,000
        trade = self.tca.analyze_trade(
            "X", "BUY", 1_000.0, 1_000_000.0, 100.0, 100.0, 100.0, 0.0,
            filled_size=400.0, p_end=110.0)

        self.assertAlmostEqual(trade.unfilled_size, 600.0, places=9)
        self.assertAlmostEqual(trade.opportunity_cost_bps, 1_000.0, places=9)
        self.assertAlmostEqual(trade.total_implementation_shortfall_bps, 600.0, places=9)
        self.assertAlmostEqual(trade.opportunity_cost_currency, 6_000.0, places=6)
        self.assertAlmostEqual(trade.executed_notional, 40_000.0, places=6)
        self.assertAlmostEqual(trade.total_cost_currency, 6_000.0, places=6)

    def test_unfilled_without_p_end_reports_none_not_zero(self):
        # Reporting 0.0 would understate the cost of exactly the orders that
        # failed to fill, which is the expensive case.
        trade = self.tca.analyze_trade(
            "X", "BUY", 1_000.0, 1_000_000.0, 100.0, 100.0, 100.0, 0.0,
            filled_size=400.0)
        self.assertIsNone(trade.opportunity_cost_bps)
        self.assertIsNone(trade.total_implementation_shortfall_bps)
        self.assertIsNone(trade.opportunity_cost_currency)

    def test_unpriced_opportunity_cost_warns(self):
        with self.assertLogs("tca_integrator", level=logging.WARNING) as cm:
            self.tca.analyze_trade(
                "X", "BUY", 1_000.0, 1_000_000.0, 100.0, 100.0, 100.0, 0.0,
                filled_size=400.0)
        self.assertTrue(any("opportunity cost" in m for m in cm.output))

    def test_complete_fill_has_zero_opportunity_cost(self):
        trade = self.tca.analyze_trade(
            "X", "BUY", 1_000.0, 1_000_000.0, 100.0, 100.0, 100.5, 0.0)
        self.assertAlmostEqual(trade.filled_size, 1_000.0, places=9)
        self.assertAlmostEqual(trade.opportunity_cost_bps, 0.0, places=9)
        self.assertAlmostEqual(
            trade.total_implementation_shortfall_bps, 50.0, places=9)

    def test_complete_miss_pays_no_commission(self):
        tca = TCABacktestIntegrator(market_impact_gamma=0.0, fixed_commission_bps=5.0)
        trade = tca.analyze_trade(
            "X", "BUY", 1_000.0, 1_000_000.0, 100.0, 100.0, 100.0, 0.0,
            filled_size=0.0, p_end=101.0)
        self.assertAlmostEqual(trade.realized_shortfall_bps, 0.0, places=9)
        self.assertAlmostEqual(trade.executed_notional, 0.0, places=9)
        # Whole order missed: total IS is pure opportunity cost.
        self.assertAlmostEqual(trade.opportunity_cost_bps, 100.0, places=9)
        self.assertAlmostEqual(
            trade.total_implementation_shortfall_bps, 100.0, places=9)

    def test_fill_size_float_noise_counts_as_complete(self):
        # A 0.3-unit order filled by three 0.1-unit child fills accumulates to
        # 0.30000000000000004. That is a complete fill, not an over-fill and not
        # a 5e-17-unit unexecuted remainder demanding a p_end to price.
        order = 0.3
        noisy = sum([0.1] * 3)
        self.assertGreater(noisy, order)
        trade = self.tca.analyze_trade(
            "X", "BUY", order, 1_000.0, 100.0, 100.0, 100.0, 0.0,
            filled_size=noisy)
        self.assertEqual(trade.unfilled_size, 0.0)
        self.assertAlmostEqual(trade.opportunity_cost_bps, 0.0, places=9)
        self.assertIsNotNone(trade.total_implementation_shortfall_bps)

    def test_genuine_overfill_is_still_rejected(self):
        # Well outside the tolerance: a real over-fill must not be snapped away.
        with self.assertRaises(ValueError):
            self.tca.analyze_trade(
                "X", "BUY", 1_000.0, 1_000_000.0, 100.0, 100.0, 100.0, 0.0,
                filled_size=1_000.0 * (1.0 + 1_000.0 * FILL_SIZE_REL_TOL))
        # And the tolerance does not swallow a materially larger fill either.
        with self.assertRaises(ValueError):
            self.tca.analyze_trade(
                "X", "BUY", 1_000.0, 1_000_000.0, 100.0, 100.0, 100.0, 0.0,
                filled_size=1_001.0)

    def test_sell_side_opportunity_cost_sign(self):
        # Failing to sell into a falling market is an opportunity COST.
        trade = self.tca.analyze_trade(
            "X", "SELL", 1_000.0, 1_000_000.0, 100.0, 100.0, 100.0, 0.0,
            filled_size=500.0, p_end=90.0)
        self.assertAlmostEqual(trade.opportunity_cost_bps, 1_000.0, places=9)


class TestPortfolioAggregation(unittest.TestCase):
    """Notional-weighted drag against an explicit capital base."""

    def setUp(self):
        self.tca = TCABacktestIntegrator(
            market_impact_gamma=0.0, fixed_commission_bps=0.0,
            max_acceptable_shortfall_bps=50.0)

    def _flat_trade(self, size, p_fill, adv=10_000_000.0):
        return self.tca.analyze_trade(
            "X", "BUY", size, adv, 100.0, 100.0, p_fill, 0.0)

    def test_drag_is_notional_based_not_trade_count_based(self):
        # REGRESSION: pre-1.1 drag was sum(shortfall_bps) / 100, so 1,000 tiny
        # trades at 1 bp each subtracted 10 percentage points of return no matter
        # how little was actually traded.
        #   1,000 trades x 1 unit x 100.00 = 100,000 notional
        #   1 bp of 100,000               =      10.00 currency
        #   on 1,000,000 capital          =       0.001% drag
        tca = TCABacktestIntegrator(
            market_impact_gamma=0.0, fixed_commission_bps=1.0)
        trades = [
            tca.analyze_trade("X", "BUY", 1.0, 10_000_000.0, 100.0, 100.0, 100.0, 0.0)
            for _ in range(1_000)
        ]
        summary = tca.evaluate_portfolio_tca(
            trades, gross_return_pct=20.0, capital_base=1_000_000.0)

        self.assertAlmostEqual(summary.total_cost_currency, 10.0, places=6)
        self.assertAlmostEqual(summary.friction_drag_pct, 0.001, places=6)
        self.assertAlmostEqual(summary.net_tca_return_pct, 20.0, places=2)
        self.assertTrue(summary.is_strategy_viable)

    def test_equal_and_notional_weighted_shortfall_differ(self):
        #   A:    100 units, fill 101.00 -> 100 bps on     10,000 notional
        #   B: 10,000 units, fill 100.10 ->  10 bps on  1,000,000 notional
        #   equal-weighted    = (100 + 10) / 2                        = 55 bps
        #   notional-weighted = (100*10,000 + 10*1,000,000) / 1,010,000
        #                     = 11,000,000 / 1,010,000                = 10.891089 bps
        #   currency cost     = 100 + 1,000                           = 1,100
        trades = [self._flat_trade(100.0, 101.0), self._flat_trade(10_000.0, 100.10)]
        summary = self.tca.evaluate_portfolio_tca(trades, 15.0, capital_base=1_000_000.0)

        self.assertEqual(summary.total_trades_analyzed, 2)
        self.assertAlmostEqual(summary.avg_implementation_shortfall_bps, 55.0, places=2)
        self.assertAlmostEqual(summary.notional_weighted_shortfall_bps, 10.89, places=2)
        self.assertAlmostEqual(summary.total_executed_notional, 1_010_000.0, places=2)
        self.assertAlmostEqual(summary.total_cost_currency, 1_100.0, places=2)
        self.assertAlmostEqual(summary.friction_drag_pct, 0.11, places=6)
        self.assertAlmostEqual(summary.net_tca_return_pct, 14.89, places=2)

    def test_viability_uses_the_notional_weighted_figure(self):
        # Equal-weighted 55 bps breaches the 50 bps limit; notional-weighted
        # 10.89 bps does not, and the notional-weighted figure is the one that
        # ties to the money actually spent.
        trades = [self._flat_trade(100.0, 101.0), self._flat_trade(10_000.0, 100.10)]
        summary = self.tca.evaluate_portfolio_tca(trades, 15.0, capital_base=1_000_000.0)
        self.assertGreater(summary.avg_implementation_shortfall_bps, 50.0)
        self.assertLess(summary.notional_weighted_shortfall_bps, 50.0)
        self.assertTrue(summary.is_strategy_viable)

    def test_costs_can_flip_a_profitable_backtest_negative(self):
        trades = [self._flat_trade(10_000.0, 102.0)]  # 200 bps on 1,000,000
        summary = self.tca.evaluate_portfolio_tca(trades, 15.0, capital_base=100_000.0)
        self.assertAlmostEqual(summary.total_cost_currency, 20_000.0, places=2)
        self.assertAlmostEqual(summary.friction_drag_pct, 20.0, places=4)
        self.assertAlmostEqual(summary.net_tca_return_pct, -5.0, places=2)
        self.assertFalse(summary.is_strategy_viable)

    def test_impact_and_commission_currency_totals(self):
        tca = TCABacktestIntegrator(
            market_impact_gamma=15.0, fixed_commission_bps=2.5)
        # 10% participation -> impact 15*sqrt(0.1) = 4.743416 bps on 1,500,000
        # notional = 711.5124; commission 2.5 bps of 1,500,000 = 375.00.
        trades = [tca.analyze_trade(
            "AAPL", "BUY", 10_000.0, 100_000.0, 150.0, 150.0, 150.0, 0.0)]
        summary = tca.evaluate_portfolio_tca(trades, 10.0, capital_base=10_000_000.0)
        self.assertAlmostEqual(summary.total_market_impact_cost_usd, 711.51, places=2)
        self.assertAlmostEqual(summary.total_commission_cost_usd, 375.0, places=2)

    def test_unpriced_opportunity_trades_are_counted(self):
        trades = [
            self.tca.analyze_trade(
                "X", "BUY", 1_000.0, 1_000_000.0, 100.0, 100.0, 100.0, 0.0,
                filled_size=500.0),
            self._flat_trade(100.0, 100.0),
        ]
        summary = self.tca.evaluate_portfolio_tca(trades, 10.0, capital_base=1_000_000.0)
        self.assertEqual(summary.unpriced_opportunity_trades, 1)

    def test_empty_trade_list_leaves_return_untouched(self):
        summary = self.tca.evaluate_portfolio_tca([], 12.34, capital_base=1_000_000.0)
        self.assertIsInstance(summary, TCAPortfolioSummary)
        self.assertEqual(summary.total_trades_analyzed, 0)
        self.assertAlmostEqual(summary.friction_drag_pct, 0.0, places=9)
        self.assertAlmostEqual(summary.net_tca_return_pct, 12.34, places=2)
        self.assertTrue(summary.is_strategy_viable)

    def test_all_orders_missed_falls_back_to_equal_weighting(self):
        trades = [
            self.tca.analyze_trade(
                "X", "BUY", 1_000.0, 1_000_000.0, 100.0, 100.0, 100.0, 0.0,
                filled_size=0.0, p_end=101.0)
            for _ in range(3)
        ]
        summary = self.tca.evaluate_portfolio_tca(trades, 10.0, capital_base=1_000_000.0)
        self.assertAlmostEqual(summary.total_executed_notional, 0.0, places=9)
        # 100 bps of (1,000 unfilled units x 100.00) = 1,000 per trade, x3.
        self.assertAlmostEqual(summary.total_cost_currency, 3_000.0, places=2)
        self.assertAlmostEqual(summary.friction_drag_pct, 0.3, places=4)

    def test_capital_base_is_mandatory(self):
        with self.assertRaises(TypeError):
            self.tca.evaluate_portfolio_tca([], 10.0)  # type: ignore[call-arg]


class TestGammaCalibration(unittest.TestCase):
    """suggest_market_impact_gamma recovers a known coefficient."""

    def test_recovers_the_true_coefficient(self):
        # Construct fills whose residual is exactly 20 * sqrt(participation):
        #   phi = 0.01 -> residual 2.0 bps -> p_fill = 100.02
        #   phi = 0.04 -> residual 4.0 bps -> p_fill = 100.04
        # gamma_hat = (2.0*0.1 + 4.0*0.2) / (0.01 + 0.04) = 1.0 / 0.05 = 20.0
        tca = TCABacktestIntegrator(market_impact_gamma=0.0, fixed_commission_bps=0.0)
        trades = [
            tca.analyze_trade("X", "BUY", 10_000.0, 1_000_000.0,
                              100.0, 100.0, 100.02, 0.0),
            tca.analyze_trade("X", "BUY", 40_000.0, 1_000_000.0,
                              100.0, 100.0, 100.04, 0.0),
        ]
        self.assertAlmostEqual(tca.suggest_market_impact_gamma(trades), 20.0, places=6)

    def test_delay_and_spread_are_removed_before_fitting(self):
        # phi = 0.01 (sqrt 0.1). delay 1 bp + half-spread 1 bp + impact 2 bps
        # => realized execution cost 4 bps => p_fill = 100.04.
        # gamma_hat must be (4 - 1 - 1) * 0.1 / 0.01 = 20.0, not 40.0.
        tca = TCABacktestIntegrator(market_impact_gamma=0.0, fixed_commission_bps=0.0)
        trade = tca.analyze_trade(
            "X", "BUY", 10_000.0, 1_000_000.0, 100.0, 100.01, 100.04, 0.02)
        self.assertAlmostEqual(trade.delay_cost_bps, 1.0, places=9)
        self.assertAlmostEqual(trade.spread_cross_bps, 1.0, places=9)
        self.assertAlmostEqual(tca.suggest_market_impact_gamma([trade]), 20.0, places=6)

    def test_negative_fit_is_clamped_to_zero(self):
        tca = TCABacktestIntegrator(market_impact_gamma=0.0, fixed_commission_bps=0.0)
        favourable = tca.analyze_trade(
            "X", "BUY", 10_000.0, 1_000_000.0, 100.0, 100.0, 99.99, 0.0)
        with self.assertLogs("tca_integrator", level=logging.WARNING):
            self.assertEqual(tca.suggest_market_impact_gamma([favourable]), 0.0)

    def test_returns_none_when_nothing_filled(self):
        tca = TCABacktestIntegrator(market_impact_gamma=0.0, fixed_commission_bps=0.0)
        missed = tca.analyze_trade(
            "X", "BUY", 1_000.0, 1_000_000.0, 100.0, 100.0, 100.0, 0.0,
            filled_size=0.0, p_end=100.0)
        with self.assertLogs("tca_integrator", level=logging.WARNING):
            self.assertIsNone(tca.suggest_market_impact_gamma([missed]))

    def test_returns_none_for_no_trades(self):
        tca = TCABacktestIntegrator()
        with self.assertLogs("tca_integrator", level=logging.WARNING):
            self.assertIsNone(tca.suggest_market_impact_gamma([]))


class TestInputValidation(unittest.TestCase):
    """Bad input fails loudly instead of producing a plausible wrong number."""

    def setUp(self):
        self.tca = TCABacktestIntegrator()

    def _call(self, **overrides):
        kwargs = dict(
            symbol="X", action="BUY", order_size=1_000.0, adv=100_000.0,
            p_decision=100.0, p_arrival=100.0, p_fill=100.0, spread=0.0)
        kwargs.update(overrides)
        return self.tca.analyze_trade(**kwargs)

    def test_zero_adv_is_rejected(self):
        # REGRESSION: pre-1.1 used max(1.0, adv), so adv=0 became a 1-unit ADV,
        # pinning participation at 100% and the impact estimate at gamma.
        with self.assertRaises(ValueError):
            self._call(adv=0.0)

    def test_negative_adv_is_rejected(self):
        with self.assertRaises(ValueError):
            self._call(adv=-5_000.0)

    def test_zero_decision_price_is_rejected(self):
        # Pre-1.1 this raised a bare ZeroDivisionError from inside the arithmetic.
        with self.assertRaises(ValueError):
            self._call(p_decision=0.0)

    def test_negative_order_size_is_rejected(self):
        # Pre-1.1 this surfaced as "math domain error" from sqrt().
        with self.assertRaises(ValueError):
            self._call(order_size=-1_000.0)

    def test_nan_price_is_rejected(self):
        # REGRESSION: pre-1.1 a NaN propagated to net_tca_return_pct, where it
        # compared False against every threshold and quietly failed viability
        # without anyone learning the costs were never computed.
        for field in ("p_decision", "p_arrival", "p_fill"):
            with self.subTest(field=field), self.assertRaises(ValueError):
                self._call(**{field: float("nan")})

    def test_infinite_input_is_rejected(self):
        with self.assertRaises(ValueError):
            self._call(adv=float("inf"))

    def test_non_numeric_input_is_rejected(self):
        with self.assertRaises(TypeError):
            self._call(order_size="1000")  # type: ignore[arg-type]

    def test_unknown_action_is_rejected(self):
        # "SEL" must not be silently treated as a SELL and invert every sign.
        for action in ("SEL", "buy_to_open", "", "HOLD"):
            with self.subTest(action=action), self.assertRaises(ValueError):
                self._call(action=action)

    def test_action_is_case_and_whitespace_insensitive(self):
        self.assertEqual(self._call(action=" buy ").action, "BUY")
        self.assertEqual(self._call(action="Sell").action, "SELL")

    def test_empty_symbol_is_rejected(self):
        with self.assertRaises(ValueError):
            self._call(symbol="   ")

    def test_negative_spread_is_rejected(self):
        with self.assertRaises(ValueError):
            self._call(spread=-0.01)

    def test_overfill_is_rejected(self):
        with self.assertRaises(ValueError):
            self._call(filled_size=1_500.0)

    def test_negative_fill_is_rejected(self):
        with self.assertRaises(ValueError):
            self._call(filled_size=-1.0)

    def test_non_positive_p_end_is_rejected(self):
        with self.assertRaises(ValueError):
            self._call(filled_size=500.0, p_end=0.0)

    def test_negative_gamma_is_rejected(self):
        with self.assertRaises(ValueError):
            TCABacktestIntegrator(market_impact_gamma=-1.0)

    def test_negative_commission_is_rejected(self):
        with self.assertRaises(ValueError):
            TCABacktestIntegrator(fixed_commission_bps=-1.0)

    def test_non_positive_capital_base_is_rejected(self):
        with self.assertRaises(ValueError):
            self.tca.evaluate_portfolio_tca([], 10.0, capital_base=0.0)

    def test_nan_gross_return_is_rejected(self):
        with self.assertRaises(ValueError):
            self.tca.evaluate_portfolio_tca([], float("nan"), capital_base=1_000.0)


class TestModuleConstants(unittest.TestCase):

    def test_constants_are_sane(self):
        self.assertEqual(BPS_PER_UNIT, 10_000.0)
        self.assertEqual(DEFAULT_MARKET_IMPACT_GAMMA_BPS, 15.0)
        self.assertLess(SQRT_LAW_MIN_PARTICIPATION, SQRT_LAW_MAX_PARTICIPATION)

    def test_breakdown_is_returned(self):
        trade = TCABacktestIntegrator().analyze_trade(
            "X", "BUY", 1_000.0, 100_000.0, 100.0, 100.0, 100.0, 0.0)
        self.assertIsInstance(trade, TCATradeBreakdown)


if __name__ == "__main__":
    unittest.main()
