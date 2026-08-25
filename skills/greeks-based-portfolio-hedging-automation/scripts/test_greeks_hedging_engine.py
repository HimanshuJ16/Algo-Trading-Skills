"""
Unit tests for greeks-based-portfolio-hedging-automation.

Expected values are derived by hand from the contract terms rather than by
re-running the engine's own arithmetic, so a formula change breaks a test.
"""
import logging
import math
import unittest

from greeks_hedging_engine import (
    GreeksPortfolioHedgingEngine,
    HedgeInstrument,
    OptionPosition,
    STANDARD_US_EQUITY_OPTION_MULTIPLIER,
)

# Breach paths log at WARNING by design; assertions read report.warnings instead.
logging.getLogger("greeks_hedging_engine").setLevel(logging.CRITICAL)

# SPY shares: one share carries $500 of delta at a $500 spot.
SPY_SHARES = HedgeInstrument(symbol="SPY", price=500.0, multiplier=1.0, delta_per_unit=1.0)

# CME E-mini S&P 500: $50 per index point, so one contract at 5,000 carries
# $250,000 of delta.
ES_FUTURE = HedgeInstrument(symbol="ESZ5", price=5000.0, multiplier=50.0, delta_per_unit=1.0)


def equity_option(symbol, underlying, qty, delta, vega, spot, multiplier=100.0, beta=1.0):
    return OptionPosition(
        symbol=symbol,
        underlying_symbol=underlying,
        quantity=qty,
        delta=delta,
        vega=vega,
        underlying_price=spot,
        multiplier=multiplier,
        beta=beta,
    )


class TestNetGreeksAggregation(unittest.TestCase):
    def setUp(self):
        self.engine = GreeksPortfolioHedgingEngine(
            max_allowed_delta_usd=50000.0,
            max_allowed_vega_usd=10000.0,
            min_rebalance_delta_usd=10000.0,
        )

    def test_contract_multiplier_scales_dollar_delta(self):
        """
        1,000 SPY calls control 100,000 shares, not 1,000.

        Hand-derived: 1,000 contracts x 100 shares x 0.60 delta x $500 spot
        = $30,000,000 of dollar delta. Omitting the multiplier understates this
        100x and produces a hedge 100x too small.
        """
        positions = [equity_option("SPY_C500", "SPY", 1000.0, 0.60, 0.20, 500.0)]

        summary = self.engine.compute_net_greeks(positions)

        self.assertEqual(summary.net_delta_usd, 30_000_000.00)
        self.assertTrue(summary.is_delta_breached)

    def test_vega_uses_contract_multiplier_not_a_hardcoded_100(self):
        """
        Dollar vega is quantity x multiplier x vega, with vega quoted per vol point.

        Uses an OCC-adjusted contract delivering 10 shares to separate the true
        multiplier from a hardcoded 100: 500 x 10 x 0.30 = $1,500 per vol point.
        A hardcoded 100 would report $15,000 and falsely breach the limit.
        """
        positions = [equity_option("ADJ_C", "ADJ", 500.0, 0.40, 0.30, 50.0, multiplier=10.0)]

        summary = self.engine.compute_net_greeks(positions)

        self.assertEqual(summary.net_vega_usd, 1500.00)
        self.assertFalse(summary.is_vega_breached)

    def test_short_position_signs_flow_from_quantity(self):
        """Short options carry negative dollar delta and negative dollar vega."""
        positions = [equity_option("SPY_C", "SPY", -10.0, 0.50, 0.20, 500.0)]

        summary = self.engine.compute_net_greeks(positions)

        # -10 x 100 x 0.50 x 500 = -250,000 ; -10 x 100 x 0.20 = -200
        self.assertEqual(summary.net_delta_usd, -250_000.00)
        self.assertEqual(summary.net_vega_usd, -200.00)

    def test_delta_usd_is_grouped_by_underlying(self):
        positions = [
            equity_option("AAPL_C", "AAPL", 10.0, 0.50, 0.10, 200.0),   # +100,000
            equity_option("AAPL_P", "AAPL", 10.0, -0.25, 0.10, 200.0),  # -50,000
            equity_option("MSFT_C", "MSFT", 5.0, 0.40, 0.10, 400.0),    # +80,000
        ]

        summary = self.engine.compute_net_greeks(positions)

        self.assertEqual(summary.delta_usd_by_underlying, {"AAPL": 50_000.00, "MSFT": 80_000.00})
        self.assertEqual(summary.net_delta_usd, 130_000.00)

    def test_empty_portfolio_is_flat(self):
        summary = self.engine.compute_net_greeks([])

        self.assertEqual(summary.total_positions, 0)
        self.assertEqual(summary.net_delta_usd, 0.0)
        self.assertEqual(summary.net_vega_usd, 0.0)
        self.assertFalse(summary.is_delta_breached)
        self.assertFalse(summary.is_vega_breached)

    def test_standard_multiplier_constant_matches_us_equity_option(self):
        self.assertEqual(STANDARD_US_EQUITY_OPTION_MULTIPLIER, 100.0)


class TestHedgeTriggerSemantics(unittest.TestCase):
    def setUp(self):
        self.engine = GreeksPortfolioHedgingEngine(
            max_allowed_delta_usd=50000.0,
            max_allowed_vega_usd=10000.0,
            min_rebalance_delta_usd=10000.0,
        )

    def test_exposure_between_min_size_and_limit_is_not_hedged(self):
        """
        The trigger is max_allowed_delta_usd, not min_rebalance_delta_usd.

        4 contracts x 100 x 0.50 x $100 = $20,000 of delta: above the $10,000
        minimum order size but well inside the $50,000 limit. Treating the size
        floor as the trigger would fire a hedge here and pay the spread for it.
        """
        positions = [equity_option("XYZ_C", "XYZ", 4.0, 0.50, 0.01, 100.0)]

        report = self.engine.evaluate_and_hedge(positions, SPY_SHARES)

        self.assertFalse(report.is_hedging_required)
        self.assertEqual(report.recommended_hedge_orders, [])
        self.assertTrue(report.is_residual_within_limits)

    def test_delta_neutral_portfolio_no_hedge(self):
        positions = [
            equity_option("SPY_C", "SPY", 100.0, 0.50, 0.10, 500.0),    # +2,500,000
            equity_option("SPY_P", "SPY", 100.0, -0.50, 0.10, 500.0),   # -2,500,000
        ]

        report = self.engine.evaluate_and_hedge(positions, SPY_SHARES)

        self.assertFalse(report.is_hedging_required)
        self.assertEqual(report.net_greeks.net_delta_usd, 0.0)
        self.assertEqual(len(report.recommended_hedge_orders), 0)

    def test_breach_generates_offsetting_share_hedge(self):
        """
        1,000 calls x 100 x 0.60 x $500 = $30,000,000 long delta.
        SPY at $500 carries $500 per share, so the offset is 60,000 shares SOLD.
        """
        positions = [equity_option("SPY_C500", "SPY", 1000.0, 0.60, 0.01, 500.0)]

        report = self.engine.evaluate_and_hedge(positions, SPY_SHARES)

        self.assertTrue(report.is_hedging_required)
        self.assertEqual(len(report.recommended_hedge_orders), 1)

        hedge = report.recommended_hedge_orders[0]
        self.assertEqual(hedge.hedge_leg, "DELTA")
        self.assertEqual(hedge.action, "SELL")
        self.assertEqual(hedge.quantity, 60_000.0)
        self.assertEqual(hedge.target_symbol, "SPY")
        self.assertEqual(report.residual_delta_usd, 0.0)
        self.assertTrue(report.is_residual_within_limits)

    def test_short_book_hedges_by_buying(self):
        positions = [equity_option("SPY_C500", "SPY", -1000.0, 0.60, 0.01, 500.0)]

        report = self.engine.evaluate_and_hedge(positions, SPY_SHARES)

        hedge = report.recommended_hedge_orders[0]
        self.assertEqual(hedge.action, "BUY")
        self.assertEqual(hedge.quantity, 60_000.0)

    def test_hedge_suppressed_below_minimum_rebalance_size(self):
        """A breach too small to be worth the spread is reported, not traded."""
        engine = GreeksPortfolioHedgingEngine(
            max_allowed_delta_usd=5000.0,
            max_allowed_vega_usd=10000.0,
            min_rebalance_delta_usd=50000.0,
        )
        positions = [equity_option("XYZ_C", "XYZ", 4.0, 0.50, 0.01, 100.0)]  # $20,000

        report = engine.evaluate_and_hedge(positions, SPY_SHARES)

        self.assertTrue(report.is_hedging_required)
        self.assertEqual(report.recommended_hedge_orders, [])
        self.assertEqual(report.residual_delta_usd, 20_000.00)
        self.assertFalse(report.is_residual_within_limits)
        self.assertTrue(any(w.startswith("HEDGE_SUPPRESSED_BELOW_MIN_SIZE") for w in report.warnings))


class TestBetaWeighting(unittest.TestCase):
    def setUp(self):
        self.engine = GreeksPortfolioHedgingEngine(
            max_allowed_delta_usd=50000.0,
            max_allowed_vega_usd=10000.0,
            min_rebalance_delta_usd=10000.0,
        )

    def test_beta_weighted_delta_sizes_the_index_proxy_hedge(self):
        """
        100 calls x 100 x 0.50 x $150 = $750,000 raw dollar delta on a beta-1.72
        name. Against an S&P proxy that is 1.72 x 750,000 = $1,290,000 of
        index-equivalent delta, i.e. 2,580 SPY shares at $500 - not 1,500.
        """
        positions = [equity_option("HIB_C", "HIB", 100.0, 0.50, 0.01, 150.0, beta=1.72)]

        report = self.engine.evaluate_and_hedge(positions, SPY_SHARES)

        self.assertEqual(report.net_greeks.net_delta_usd, 750_000.00)
        self.assertEqual(report.net_greeks.beta_weighted_delta_usd, 1_290_000.00)

        hedge = report.recommended_hedge_orders[0]
        self.assertEqual(hedge.action, "SELL")
        self.assertEqual(hedge.quantity, 2580.0)

    def test_default_beta_reproduces_unweighted_exposure(self):
        positions = [equity_option("SPY_C", "SPY", 10.0, 0.50, 0.01, 500.0)]

        summary = self.engine.compute_net_greeks(positions)

        self.assertEqual(summary.net_delta_usd, summary.beta_weighted_delta_usd)

    def test_negative_beta_position_offsets_a_long_book(self):
        """An inverse-beta holding reduces, not adds to, index-equivalent delta."""
        positions = [
            equity_option("SPY_C", "SPY", 10.0, 0.50, 0.01, 500.0),                 # +250,000
            equity_option("INV_C", "INV", 10.0, 0.50, 0.01, 500.0, beta=-1.0),      # -250,000 weighted
        ]

        summary = self.engine.compute_net_greeks(positions)

        self.assertEqual(summary.net_delta_usd, 500_000.00)
        self.assertEqual(summary.beta_weighted_delta_usd, 0.0)
        self.assertFalse(summary.is_delta_breached)


class TestLotGranularityAndResidual(unittest.TestCase):
    def setUp(self):
        self.engine = GreeksPortfolioHedgingEngine(
            max_allowed_delta_usd=50000.0,
            max_allowed_vega_usd=10000.0,
            min_rebalance_delta_usd=10000.0,
        )

    def test_futures_hedge_truncates_toward_zero_never_overshoots(self):
        """
        140 contracts x 100 x 0.50 x $100 = $700,000 long delta. One ES contract
        carries $250,000, so the exact hedge is 2.8 contracts. Truncating gives 2
        (residual +$200,000, still long); rounding would give 3 and flip the book
        $50,000 short - an overshoot past neutral the risk limit never asked for.
        """
        positions = [equity_option("XYZ_C", "XYZ", 140.0, 0.50, 0.01, 100.0)]

        report = self.engine.evaluate_and_hedge(positions, ES_FUTURE)

        hedge = report.recommended_hedge_orders[0]
        self.assertEqual(hedge.action, "SELL")
        self.assertEqual(hedge.quantity, 2.0)
        self.assertEqual(report.residual_delta_usd, 200_000.00)
        self.assertFalse(report.is_residual_within_limits)

    def test_breach_smaller_than_one_hedge_contract_is_reported_not_silently_dropped(self):
        """
        $60,000 of delta breaches the $50,000 limit but is under one $250,000 ES
        contract. The engine must say so rather than return an empty order list
        that reads as "nothing to do".
        """
        positions = [equity_option("XYZ_C", "XYZ", 12.0, 0.50, 0.01, 100.0)]

        report = self.engine.evaluate_and_hedge(positions, ES_FUTURE)

        self.assertTrue(report.is_hedging_required)
        self.assertEqual(report.recommended_hedge_orders, [])
        self.assertEqual(report.residual_delta_usd, 60_000.00)
        self.assertFalse(report.is_residual_within_limits)
        self.assertTrue(any(w.startswith("DELTA_BREACH_UNHEDGEABLE") for w in report.warnings))


class TestVegaHedging(unittest.TestCase):
    def setUp(self):
        self.engine = GreeksPortfolioHedgingEngine(
            max_allowed_delta_usd=50000.0,
            max_allowed_vega_usd=10000.0,
            min_rebalance_delta_usd=10000.0,
        )
        # SPY ATM call used as the vega overlay: $60 of vega and $25,000 of delta
        # per contract at a $500 spot.
        self.spy_call = HedgeInstrument(
            symbol="SPY_30D_C500", price=500.0, multiplier=100.0,
            delta_per_unit=0.50, vega_per_unit=0.60,
        )
        # -200 contracts x 100 x 1.00 = -$20,000 of vega, delta-neutral.
        self.short_vega_book = [equity_option("SPY_STRADDLE", "SPY", -200.0, 0.0, 1.00, 500.0)]

    def test_vega_breach_without_a_vega_instrument_is_flagged_not_ignored(self):
        report = self.engine.evaluate_and_hedge(self.short_vega_book, SPY_SHARES)

        self.assertTrue(report.net_greeks.is_vega_breached)
        self.assertTrue(report.is_hedging_required)
        self.assertEqual(report.recommended_hedge_orders, [])
        self.assertEqual(report.residual_vega_usd, -20_000.00)
        self.assertFalse(report.is_residual_within_limits)
        self.assertTrue(any(w.startswith("VEGA_BREACH_UNHEDGED") for w in report.warnings))

    def test_vega_hedge_injects_delta_that_the_delta_leg_then_neutralises(self):
        """
        Short $20,000 of vega. The overlay carries $60 per contract, so buying 333
        leaves -$20 of residual vega (334 would overshoot). Those 333 contracts
        also inject 333 x $25,000 = $8,325,000 of delta, which the delta leg must
        sell 16,650 SPY shares to remove. Sizing delta first would miss all of it.
        """
        report = self.engine.evaluate_and_hedge(
            self.short_vega_book, SPY_SHARES, vega_hedge_instrument=self.spy_call
        )

        self.assertEqual(len(report.recommended_hedge_orders), 2)

        vega_leg, delta_leg = report.recommended_hedge_orders
        self.assertEqual(vega_leg.hedge_leg, "VEGA")
        self.assertEqual(vega_leg.action, "BUY")
        self.assertEqual(vega_leg.quantity, 333.0)
        self.assertEqual(vega_leg.delta_usd_offset, 8_325_000.00)

        self.assertEqual(delta_leg.hedge_leg, "DELTA")
        self.assertEqual(delta_leg.action, "SELL")
        self.assertEqual(delta_leg.quantity, 16_650.0)

        self.assertEqual(report.residual_vega_usd, -20.00)
        self.assertEqual(report.residual_delta_usd, 0.0)
        self.assertTrue(report.is_residual_within_limits)

    def test_exactly_divisible_vega_hedge_leaves_no_residual(self):
        chunky = HedgeInstrument(
            symbol="SPY_LEAP_C", price=500.0, multiplier=100.0,
            delta_per_unit=0.50, vega_per_unit=5.00,   # $500 of vega per contract
        )

        report = self.engine.evaluate_and_hedge(
            self.short_vega_book, SPY_SHARES, vega_hedge_instrument=chunky
        )

        vega_leg = report.recommended_hedge_orders[0]
        self.assertEqual(vega_leg.quantity, 40.0)          # 20,000 / 500 = 40 exactly
        self.assertEqual(report.residual_vega_usd, 0.0)

    def test_vega_hedge_larger_than_the_breach_is_not_forced(self):
        """
        A small book breaching a $1,000 vega limit by $1,500 cannot be hedged with
        an index option carrying $2,000 of vega per contract: one contract would
        flip the book long vega. The engine reports it instead of overshooting.
        """
        engine = GreeksPortfolioHedgingEngine(max_allowed_vega_usd=1000.0)
        spx_leap = HedgeInstrument(
            symbol="SPX_LEAP_C", price=5000.0, multiplier=100.0,
            delta_per_unit=0.50, vega_per_unit=20.00,   # $2,000 of vega per contract
        )
        book = [equity_option("SPY_STRADDLE", "SPY", -15.0, 0.0, 1.00, 500.0)]  # -$1,500

        report = engine.evaluate_and_hedge(book, SPY_SHARES, vega_hedge_instrument=spx_leap)

        self.assertTrue(report.is_hedging_required)
        self.assertEqual(report.recommended_hedge_orders, [])
        self.assertEqual(report.residual_vega_usd, -1500.00)
        self.assertFalse(report.is_residual_within_limits)
        self.assertTrue(any(w.startswith("VEGA_HEDGE_ROUNDS_TO_ZERO") for w in report.warnings))


class TestInputValidation(unittest.TestCase):
    def setUp(self):
        self.engine = GreeksPortfolioHedgingEngine()

    def test_multiplier_is_required(self):
        with self.assertRaises(TypeError):
            OptionPosition("SPY_C", "SPY", 10.0, 0.50, 0.20, 500.0)

    def test_non_finite_greek_is_rejected(self):
        for bad in (float("nan"), float("inf")):
            with self.subTest(bad=bad):
                positions = [equity_option("SPY_C", "SPY", 10.0, 0.50, bad, 500.0)]
                with self.assertRaises(ValueError):
                    self.engine.compute_net_greeks(positions)

    def test_percent_quoted_delta_is_rejected(self):
        positions = [equity_option("SPY_C", "SPY", 10.0, 60.0, 0.20, 500.0)]

        with self.assertRaises(ValueError):
            self.engine.compute_net_greeks(positions)

    def test_non_positive_price_and_multiplier_are_rejected(self):
        for kwargs in ({"spot": 0.0}, {"spot": -100.0}, {"multiplier": 0.0}, {"multiplier": -100.0}):
            with self.subTest(**kwargs):
                positions = [equity_option("SPY_C", "SPY", 10.0, 0.50, 0.20,
                                           kwargs.get("spot", 500.0),
                                           multiplier=kwargs.get("multiplier", 100.0))]
                with self.assertRaises(ValueError):
                    self.engine.compute_net_greeks(positions)

    def test_zero_priced_hedge_instrument_is_rejected_not_divided_by(self):
        positions = [equity_option("SPY_C", "SPY", 1000.0, 0.60, 0.01, 500.0)]
        broken = HedgeInstrument(symbol="SPY", price=0.0, multiplier=1.0)

        with self.assertRaises(ValueError):
            self.engine.evaluate_and_hedge(positions, broken)

    def test_hedge_instrument_multiplier_is_required(self):
        """Forgetting multiplier=50 on an E-mini must fail loudly, not hedge 50x."""
        with self.assertRaises(TypeError):
            HedgeInstrument(symbol="ESZ5", price=5000.0)

    def test_one_shot_iterator_does_not_aggregate_to_a_falsely_flat_book(self):
        """
        The book is traversed more than once. A generator consumed by validation
        would leave every total at zero and report a flat, unhedged portfolio.
        """
        positions = [equity_option("SPY_C500", "SPY", 1000.0, 0.60, 0.01, 500.0)]

        summary = self.engine.compute_net_greeks(p for p in positions)

        self.assertEqual(summary.total_positions, 1)
        self.assertEqual(summary.net_delta_usd, 30_000_000.00)

    def test_invalid_engine_configuration_is_rejected(self):
        for kwargs in (
            {"max_allowed_delta_usd": 0.0},
            {"max_allowed_delta_usd": -1.0},
            {"max_allowed_vega_usd": 0.0},
            {"min_rebalance_delta_usd": -1.0},
            {"max_allowed_delta_usd": math.nan},
            {"hedge_order_type": "STOP"},
        ):
            with self.subTest(**kwargs):
                with self.assertRaises(ValueError):
                    GreeksPortfolioHedgingEngine(**kwargs)

    def test_order_type_is_configurable(self):
        engine = GreeksPortfolioHedgingEngine(hedge_order_type="LIMIT")
        positions = [equity_option("SPY_C", "SPY", 1000.0, 0.60, 0.01, 500.0)]

        report = engine.evaluate_and_hedge(positions, SPY_SHARES)

        self.assertEqual(report.recommended_hedge_orders[0].order_type, "LIMIT")


if __name__ == "__main__":
    unittest.main()
