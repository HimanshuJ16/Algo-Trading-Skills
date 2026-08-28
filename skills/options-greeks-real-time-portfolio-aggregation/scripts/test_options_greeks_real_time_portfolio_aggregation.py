"""
Unit tests for the options portfolio Greeks aggregator.

Expected values are derived by hand from the scaling conventions in the module
docstring, never by re-running the implementation's own arithmetic.
"""
import math
import unittest

from options_greeks_real_time_portfolio_aggregation import (
    MAX_ABS_DELTA_PER_UNIT,
    STANDARD_US_EQUITY_OPTION_MULTIPLIER,
    STATUS_DOLLAR_DELTA_BREACH,
    STATUS_DOLLAR_GAMMA_BREACH,
    STATUS_HEALTHY,
    STATUS_THETA_BREACH,
    STATUS_VEGA_BREACH,
    OptionPosition,
    OptionsGreeksRealTimePortfolioAggregationEngine,
    PortfolioGreeksLimits,
    PortfolioGreeksReport,
)


def _position(**overrides) -> OptionPosition:
    """A valid, unremarkable long call; override one field to isolate a behaviour."""
    base = dict(
        symbol="AAPL240119C00150000",
        underlying_symbol="AAPL",
        position_qty=10,
        multiplier=100,
        spot_price=150.0,
        delta=0.60,
        gamma=0.02,
        theta=-0.10,
        vega=0.15,
    )
    base.update(overrides)
    return OptionPosition(**base)


class TestPositionScaling(unittest.TestCase):
    """Per-position scaling: contracts -> deliverable units -> currency."""

    def test_long_call_scales_by_deliverable_units(self):
        # 10 contracts x 100 deliverable units x 0.60 delta = 600 units.
        # 600 units x $150 = $90,000 of dollar delta.
        pos = _position()
        self.assertEqual(pos.scaled_qty, 1000)
        self.assertEqual(pos.delta_shares, 600.0)
        self.assertEqual(pos.dollar_delta_usd, 90000.0)
        # Gamma: 1000 units x 0.02 = 20 delta units per $1 move.
        # Dollar gamma: 20 x 150 x 150 x 0.01 = $4,500 of delta per +1% move.
        self.assertEqual(pos.gamma_shares, 20.0)
        self.assertEqual(pos.dollar_gamma_usd, 4500.0)
        # Theta: 1000 x -0.10 = -$100 per calendar day. Vega: 1000 x 0.15 = $150/vol pt.
        self.assertEqual(pos.theta_daily_usd, -100.0)
        self.assertEqual(pos.vega_usd, 150.0)

    def test_short_position_sign_comes_from_quantity(self):
        # A short call keeps a positive per-unit delta; the sign is on the quantity.
        short_call = _position(position_qty=-10)
        self.assertEqual(short_call.delta_shares, -600.0)
        self.assertEqual(short_call.dollar_delta_usd, -90000.0)
        # A short put has a negative per-unit delta, so the position delta is positive.
        short_put = _position(symbol="AAPL240119P00150000", position_qty=-5, delta=-0.40)
        self.assertEqual(short_put.delta_shares, 200.0)
        self.assertEqual(short_put.dollar_delta_usd, 30000.0)

    def test_occ_adjusted_contract_must_use_the_deliverable_not_100(self):
        # OIC: a 1-for-20 reverse split leaves the premium multiplier at 100 but sets
        # the deliverable to 5 shares. Greeks scale with the deliverable.
        adjusted = _position(multiplier=5, spot_price=300.0)
        self.assertEqual(adjusted.dollar_delta_usd, 10 * 5 * 0.60 * 300.0)
        self.assertEqual(adjusted.dollar_delta_usd, 9000.0)
        # Using the premium multiplier instead overstates the risk exactly 20x.
        wrong = _position(multiplier=STANDARD_US_EQUITY_OPTION_MULTIPLIER, spot_price=300.0)
        self.assertEqual(wrong.dollar_delta_usd, 180000.0)
        self.assertEqual(wrong.dollar_delta_usd / adjusted.dollar_delta_usd, 20.0)


class TestPortfolioAggregation(unittest.TestCase):

    def setUp(self):
        self.engine = OptionsGreeksRealTimePortfolioAggregationEngine()

    def test_skill_md_verification_example(self):
        # 10 long calls, M=100, S=$100, delta 0.50, gamma 0.02, theta -0.05, vega 0.10.
        # Delta 10*100*0.50 = 500 units; dollar delta 500*100 = $50,000.
        # Gamma 10*100*0.02 = 20; dollar gamma 20*100*100*0.01 = $2,000.
        # Theta 1000*-0.05 = -$50/day; vega 1000*0.10 = $100/vol pt.
        report = self.engine.aggregate_portfolio_greeks([
            _position(spot_price=100.0, delta=0.50, gamma=0.02, theta=-0.05, vega=0.10)
        ])
        self.assertEqual(report.net_delta_shares, 500.0)
        self.assertEqual(report.net_dollar_delta_usd, 50000.0)
        self.assertEqual(report.net_gamma, 20.0)
        self.assertEqual(report.net_dollar_gamma_usd, 2000.0)
        self.assertEqual(report.net_theta_daily_usd, -50.0)
        self.assertEqual(report.net_vega_usd, 100.0)
        self.assertEqual(report.status, STATUS_HEALTHY)
        self.assertEqual(report.breaches, [])

    def test_multi_leg_single_underlying_nets_correctly(self):
        # Long 10 calls: +600 delta units, $90,000; gamma 20, dollar gamma $4,500;
        #                theta -$100; vega +$150.
        # Short 5 puts:  +200 delta units, $30,000; gamma -5, dollar gamma -$1,125;
        #                theta +$40;  vega -$60.
        long_call = _position()
        short_put = _position(
            symbol="AAPL240119P00150000", position_qty=-5,
            delta=-0.40, gamma=0.01, theta=-0.08, vega=0.12,
        )
        report = self.engine.aggregate_portfolio_greeks([long_call, short_put])

        self.assertEqual(report.total_positions, 2)
        self.assertEqual(report.net_delta_shares, 800.0)
        self.assertEqual(report.net_dollar_delta_usd, 120000.0)
        self.assertEqual(report.net_gamma, 15.0)
        self.assertEqual(report.net_dollar_gamma_usd, 3375.0)
        self.assertEqual(report.net_theta_daily_usd, -60.0)
        self.assertEqual(report.net_vega_usd, 90.0)
        self.assertEqual(report.status, STATUS_HEALTHY)
        self.assertTrue(report.is_single_underlying)
        self.assertEqual(report.underlying_count, 1)
        self.assertEqual(report.by_underlying["AAPL"]["net_delta"], 800.0)
        self.assertEqual(report.by_underlying["AAPL"]["net_dollar_delta"], 120000.0)
        self.assertEqual(report.by_underlying["AAPL"]["net_dollar_gamma"], 3375.0)

    def test_empty_portfolio_is_flat_and_healthy(self):
        report = self.engine.aggregate_portfolio_greeks([])
        self.assertIsInstance(report, PortfolioGreeksReport)
        self.assertEqual(report.total_positions, 0)
        self.assertEqual(report.net_dollar_delta_usd, 0.0)
        self.assertEqual(report.net_dollar_gamma_usd, 0.0)
        self.assertEqual(report.by_underlying, {})
        self.assertEqual(report.status, STATUS_HEALTHY)
        self.assertFalse(report.is_single_underlying)
        self.assertEqual(report.underlying_count, 0)

    def test_generator_input_is_not_silently_exhausted(self):
        # A one-shot iterator consumed by validation would aggregate to an all-zero
        # book: a risk monitor reporting "flat" for a live position.
        legs = (_position(symbol=f"AAPL_LEG_{i}") for i in range(2))
        report = self.engine.aggregate_portfolio_greeks(legs)
        self.assertEqual(report.total_positions, 2)
        self.assertEqual(report.net_dollar_delta_usd, 180000.0)

    def test_grouping_is_per_underlying_and_case_normalised(self):
        report = self.engine.aggregate_portfolio_greeks([
            _position(),
            _position(symbol="aapl_lower", underlying_symbol="aapl"),
            _position(symbol="MSFT_C", underlying_symbol="MSFT", spot_price=400.0),
        ])
        self.assertEqual(set(report.by_underlying), {"AAPL", "MSFT"})
        self.assertEqual(report.underlying_count, 2)
        self.assertFalse(report.is_single_underlying)
        # Two identical AAPL legs: 2 x $90,000.
        self.assertEqual(report.by_underlying["AAPL"]["net_dollar_delta"], 180000.0)
        # MSFT: 1000 units x 0.60 x $400 = $240,000.
        self.assertEqual(report.by_underlying["MSFT"]["net_dollar_delta"], 240000.0)


class TestCrossUnderlyingNormalisation(unittest.TestCase):
    """Raw delta/gamma do not travel across underlyings; the dollar figures do."""

    def setUp(self):
        self.engine = OptionsGreeksRealTimePortfolioAggregationEngine()

    def test_dollar_gamma_equalises_two_names_at_different_price_levels(self):
        # A: S=$500, gamma 0.001 per unit -> 0.1 delta units/$1 -> 0.1*500*500*0.01 = $250.
        # B: S=$50,  gamma 0.1   per unit -> 10  delta units/$1 -> 10 *50 *50 *0.01 = $250.
        # Raw gamma differs 100x; the economic gamma exposure is identical.
        high_priced = _position(
            symbol="HI_C", underlying_symbol="HI", position_qty=1,
            spot_price=500.0, gamma=0.001, delta=0.10, theta=0.0, vega=0.0,
        )
        low_priced = _position(
            symbol="LO_C", underlying_symbol="LO", position_qty=1,
            spot_price=50.0, gamma=0.1, delta=0.10, theta=0.0, vega=0.0,
        )
        self.assertEqual(high_priced.dollar_gamma_usd, 250.0)
        self.assertEqual(low_priced.dollar_gamma_usd, 250.0)
        self.assertNotEqual(high_priced.gamma_shares, low_priced.gamma_shares)

        report = self.engine.aggregate_portfolio_greeks([high_priced, low_priced])
        self.assertEqual(report.net_dollar_gamma_usd, 500.0)
        # The raw sum 0.1 + 10 = 10.1 is dimensionally meaningless across two names,
        # so the report flags it rather than presenting it as a portfolio number.
        self.assertEqual(report.net_gamma, 10.1)
        self.assertFalse(report.is_single_underlying)
        self.assertIn("not\ncomparable".replace("\n", " "), report.audit_notes)

    def test_dollar_gamma_matches_a_directly_simulated_one_percent_move(self):
        # Independent check: reprice delta at S*1.01 using delta' = delta + gamma*dS
        # and compare the change in dollar delta against the dollar gamma figure.
        pos = _position(position_qty=7, spot_price=200.0, gamma=0.03, delta=0.45)
        bumped_spot = 200.0 * 1.01
        bumped_delta = 0.45 + 0.03 * (bumped_spot - 200.0)
        bumped_dollar_delta = 7 * 100 * bumped_delta * bumped_spot
        # Dollar gamma isolates the delta change, valued at the original spot.
        expected = (bumped_delta - 0.45) * 7 * 100 * 200.0
        self.assertAlmostEqual(pos.dollar_gamma_usd, expected, places=6)
        self.assertGreater(bumped_dollar_delta, pos.dollar_delta_usd)


class TestLimitAuditing(unittest.TestCase):

    def test_dollar_delta_breach(self):
        # 100 contracts x 100 x 0.80 = 8,000 units x $500 = $4,000,000 vs a $500k limit.
        engine = OptionsGreeksRealTimePortfolioAggregationEngine(
            PortfolioGreeksLimits(max_dollar_delta_usd=500000.0))
        report = engine.aggregate_portfolio_greeks([
            _position(symbol="NVDA240621C00500000", underlying_symbol="NVDA",
                      position_qty=100, spot_price=500.0, delta=0.80,
                      gamma=0.01, theta=-0.20, vega=0.30)
        ])
        self.assertEqual(report.net_dollar_delta_usd, 4000000.0)
        self.assertEqual(report.status, STATUS_DOLLAR_DELTA_BREACH)
        self.assertEqual(report.breaches, [STATUS_DOLLAR_DELTA_BREACH])
        self.assertTrue(report.is_dollar_delta_breached)
        self.assertFalse(report.is_theta_breached)
        self.assertFalse(report.is_vega_breached)

    def test_every_simultaneous_breach_is_reported_not_just_the_first(self):
        # Regression: an if/elif chain reported only DOLLAR_DELTA_BREACH here, so a
        # risk operator reading `status` would believe theta and vega were inside.
        # 100 x 100 = 10,000 units: dollar delta 10,000 x 0.80 x $500 = $4,000,000;
        # theta 10,000 x -1.00 = -$10,000/day; vega 10,000 x 2.00 = $20,000/vol pt.
        engine = OptionsGreeksRealTimePortfolioAggregationEngine(PortfolioGreeksLimits(
            max_dollar_delta_usd=500000.0,
            max_negative_theta_usd=-5000.0,
            max_vega_usd=10000.0,
        ))
        report = engine.aggregate_portfolio_greeks([
            _position(symbol="NVDA_C", underlying_symbol="NVDA", position_qty=100,
                      spot_price=500.0, delta=0.80, gamma=0.01, theta=-1.00, vega=2.00)
        ])
        self.assertEqual(report.net_dollar_delta_usd, 4000000.0)
        self.assertEqual(report.net_theta_daily_usd, -10000.0)
        self.assertEqual(report.net_vega_usd, 20000.0)
        self.assertEqual(report.status, STATUS_DOLLAR_DELTA_BREACH)
        self.assertEqual(
            report.breaches,
            [STATUS_DOLLAR_DELTA_BREACH, STATUS_THETA_BREACH, STATUS_VEGA_BREACH],
        )
        self.assertTrue(report.is_theta_breached)
        self.assertTrue(report.is_vega_breached)
        self.assertIn(STATUS_THETA_BREACH, report.audit_notes)

    def test_theta_limit_is_a_signed_floor_on_decay_not_an_absolute_value(self):
        # A short-premium book *collecting* $50,000/day is not a theta breach.
        # Under an abs() reading of the limit it would be flagged, which would make
        # every income strategy permanently non-compliant.
        engine = OptionsGreeksRealTimePortfolioAggregationEngine()
        collecting = _position(position_qty=-1000, delta=0.0, gamma=0.0,
                               theta=-0.50, vega=0.0, spot_price=100.0)
        report = engine.aggregate_portfolio_greeks([collecting])
        self.assertEqual(report.net_theta_daily_usd, 50000.0)
        self.assertFalse(report.is_theta_breached)
        self.assertEqual(report.status, STATUS_HEALTHY)

    def test_theta_breach_boundary(self):
        engine = OptionsGreeksRealTimePortfolioAggregationEngine(
            PortfolioGreeksLimits(max_negative_theta_usd=-5000.0))
        # Exactly at the floor: 1,000 units x -$5.00 = -$5,000/day. Not a breach.
        at_floor = _position(position_qty=10, delta=0.0, gamma=0.0, theta=-5.0, vega=0.0)
        self.assertFalse(engine.aggregate_portfolio_greeks([at_floor]).is_theta_breached)
        # One cent past it.
        past_floor = _position(position_qty=10, delta=0.0, gamma=0.0,
                               theta=-5.00001, vega=0.0)
        report = engine.aggregate_portfolio_greeks([past_floor])
        self.assertEqual(report.net_theta_daily_usd, -5000.01)
        self.assertTrue(report.is_theta_breached)
        self.assertEqual(report.status, STATUS_THETA_BREACH)

    def test_dollar_delta_boundary_is_strictly_greater_than(self):
        engine = OptionsGreeksRealTimePortfolioAggregationEngine(
            PortfolioGreeksLimits(max_dollar_delta_usd=500000.0))
        # 10 x 100 x 1.00 x $500 = exactly $500,000.
        at_limit = _position(position_qty=10, spot_price=500.0, delta=1.0,
                             gamma=0.0, theta=0.0, vega=0.0)
        report = engine.aggregate_portfolio_greeks([at_limit])
        self.assertEqual(report.net_dollar_delta_usd, 500000.0)
        self.assertEqual(report.status, STATUS_HEALTHY)
        # $500,010 is over.
        over = _position(position_qty=10, spot_price=500.01, delta=1.0,
                         gamma=0.0, theta=0.0, vega=0.0)
        self.assertEqual(
            engine.aggregate_portfolio_greeks([over]).status, STATUS_DOLLAR_DELTA_BREACH)

    def test_short_vol_book_breaches_the_magnitude_vega_limit(self):
        # Vega is capped both ways: -$20,000/vol pt is as much exposure as +$20,000.
        engine = OptionsGreeksRealTimePortfolioAggregationEngine(
            PortfolioGreeksLimits(max_vega_usd=10000.0))
        short_vol = _position(position_qty=-100, delta=0.0, gamma=0.0,
                              theta=0.0, vega=2.00)
        report = engine.aggregate_portfolio_greeks([short_vol])
        self.assertEqual(report.net_vega_usd, -20000.0)
        self.assertTrue(report.is_vega_breached)
        self.assertEqual(report.status, STATUS_VEGA_BREACH)

    def test_dollar_gamma_limit_is_opt_in(self):
        # 10,000 units x 0.01 = 100 delta units/$1; 100 x 500 x 500 x 0.01 = $250,000.
        gamma_heavy = _position(symbol="NVDA_C", underlying_symbol="NVDA",
                                position_qty=100, spot_price=500.0, delta=0.0,
                                gamma=0.01, theta=0.0, vega=0.0)
        unaudited = OptionsGreeksRealTimePortfolioAggregationEngine()
        report = unaudited.aggregate_portfolio_greeks([gamma_heavy])
        self.assertEqual(report.net_dollar_gamma_usd, 250000.0)
        self.assertFalse(report.is_dollar_gamma_breached)
        self.assertEqual(report.status, STATUS_HEALTHY)

        audited = OptionsGreeksRealTimePortfolioAggregationEngine(
            PortfolioGreeksLimits(max_abs_dollar_gamma_usd=100000.0))
        breach = audited.aggregate_portfolio_greeks([gamma_heavy])
        self.assertTrue(breach.is_dollar_gamma_breached)
        self.assertEqual(breach.status, STATUS_DOLLAR_GAMMA_BREACH)
        self.assertEqual(breach.breaches, [STATUS_DOLLAR_GAMMA_BREACH])

    def test_reported_value_and_breach_decision_never_disagree(self):
        # A raw total of $500,000.004 rounds to the reported $500,000.00, which is not
        # over the limit. The status must agree with the number it is printed beside.
        engine = OptionsGreeksRealTimePortfolioAggregationEngine(
            PortfolioGreeksLimits(max_dollar_delta_usd=500000.0))
        pos = _position(position_qty=1, multiplier=1, spot_price=500000.004,
                        delta=1.0, gamma=0.0, theta=0.0, vega=0.0)
        report = engine.aggregate_portfolio_greeks([pos])
        self.assertEqual(report.net_dollar_delta_usd, 500000.0)
        self.assertEqual(report.status, STATUS_HEALTHY)


class TestNumericalStability(unittest.TestCase):

    def test_totals_are_order_independent_under_cancellation(self):
        # Naive left-to-right accumulation absorbs the $1 leg into the $1e16 leg and
        # returns $0.00; fsum keeps it. Aggregation must not depend on book ordering.
        big_long = _position(symbol="BIG_L", position_qty=1e14, multiplier=100,
                             spot_price=1.0, delta=1.0, gamma=0.0, theta=0.0, vega=0.0)
        small = _position(symbol="SMALL", position_qty=0.01, multiplier=100,
                          spot_price=1.0, delta=1.0, gamma=0.0, theta=0.0, vega=0.0)
        big_short = _position(symbol="BIG_S", position_qty=-1e14, multiplier=100,
                              spot_price=1.0, delta=1.0, gamma=0.0, theta=0.0, vega=0.0)

        engine = OptionsGreeksRealTimePortfolioAggregationEngine()
        forward = engine.aggregate_portfolio_greeks([big_long, small, big_short])
        reverse = engine.aggregate_portfolio_greeks([big_short, small, big_long])

        self.assertEqual(forward.net_dollar_delta_usd, 1.0)
        self.assertEqual(forward.net_dollar_delta_usd, reverse.net_dollar_delta_usd)
        # Demonstrates what the naive accumulation this replaces would have returned.
        naive = 0.0
        for value in (1e16, 1.0, -1e16):
            naive += value
        self.assertEqual(naive, 0.0)


class TestInputRejection(unittest.TestCase):
    """Corrupt input must raise, never net into a total that reads as compliant."""

    def setUp(self):
        self.engine = OptionsGreeksRealTimePortfolioAggregationEngine()

    def test_nan_compares_as_inside_every_limit(self):
        # The reason validation is mandatory rather than advisory: an unvalidated NaN
        # book would be reported PORTFOLIO_GREEKS_HEALTHY.
        self.assertFalse(abs(float("nan")) > 500000.0)
        self.assertFalse(float("nan") < -5000.0)

    def test_non_finite_greeks_raise(self):
        for field_name, bad in (
            ("delta", float("nan")),
            ("gamma", float("inf")),
            ("theta", float("nan")),
            ("vega", float("-inf")),
            ("position_qty", float("nan")),
            ("spot_price", float("inf")),
            ("multiplier", float("nan")),
        ):
            with self.subTest(field=field_name):
                with self.assertRaises(ValueError):
                    self.engine.aggregate_portfolio_greeks([_position(**{field_name: bad})])

    def test_percent_quoted_delta_raises(self):
        # A feed sending 60 instead of 0.60 would overstate exposure 100x.
        self.assertEqual(MAX_ABS_DELTA_PER_UNIT, 1.0)
        with self.assertRaises(ValueError) as ctx:
            self.engine.aggregate_portfolio_greeks([_position(delta=60.0)])
        self.assertIn("percent", str(ctx.exception))
        with self.assertRaises(ValueError):
            self.engine.aggregate_portfolio_greeks([_position(delta=-1.0001)])
        # The boundary itself is legal: a deep-ITM call or the underlying is delta 1.
        self.engine.aggregate_portfolio_greeks([_position(delta=1.0)])
        self.engine.aggregate_portfolio_greeks([_position(delta=-1.0)])

    def test_non_positive_multiplier_or_spot_raises(self):
        for field_name, bad in (
            ("multiplier", 0),
            ("multiplier", -100),
            ("spot_price", 0.0),
            ("spot_price", -150.0),
        ):
            with self.subTest(field=field_name, value=bad):
                with self.assertRaises(ValueError):
                    self.engine.aggregate_portfolio_greeks([_position(**{field_name: bad})])

    def test_string_typed_numbers_raise_a_named_field_error(self):
        # Vendor JSON routinely quotes numbers. float("0.6") would pass a value-only
        # check while the field stayed a str, surfacing later as an opaque TypeError
        # from the scaling arithmetic instead of naming the bad field here.
        with self.assertRaises(ValueError) as ctx:
            self.engine.aggregate_portfolio_greeks([_position(delta="0.6")])
        self.assertIn("delta", str(ctx.exception))
        self.assertIn("real number", str(ctx.exception))
        with self.assertRaises(ValueError):
            self.engine.aggregate_portfolio_greeks([_position(multiplier="100")])
        with self.assertRaises(ValueError):
            self.engine.aggregate_portfolio_greeks([_position(spot_price=None)])

    def test_blank_or_non_string_symbols_raise(self):
        for kwargs in (
            {"symbol": "   "},
            {"symbol": ""},
            {"underlying_symbol": ""},
            {"underlying_symbol": "  "},
            {"underlying_symbol": None},
            {"symbol": None},
        ):
            with self.subTest(**kwargs):
                with self.assertRaises(ValueError):
                    self.engine.aggregate_portfolio_greeks([_position(**kwargs)])

    def test_one_bad_leg_rejects_the_whole_book(self):
        # Partial aggregation of a book with a corrupt leg is worse than no number.
        with self.assertRaises(ValueError):
            self.engine.aggregate_portfolio_greeks(
                [_position(), _position(symbol="BAD", delta=float("nan")), _position()])

    def test_zero_quantity_leg_is_legal_and_contributes_nothing(self):
        report = self.engine.aggregate_portfolio_greeks([_position(), _position(
            symbol="CLOSED", position_qty=0)])
        self.assertEqual(report.total_positions, 2)
        self.assertEqual(report.net_dollar_delta_usd, 90000.0)


class TestLimitsValidation(unittest.TestCase):

    def test_magnitude_limits_must_be_positive(self):
        for kwargs in (
            {"max_dollar_delta_usd": 0.0},
            {"max_dollar_delta_usd": -1.0},
            {"max_vega_usd": 0.0},
            {"max_vega_usd": -1.0},
            {"max_abs_dollar_gamma_usd": 0.0},
            {"max_dollar_delta_usd": float("nan")},
        ):
            with self.subTest(**kwargs):
                with self.assertRaises(ValueError):
                    OptionsGreeksRealTimePortfolioAggregationEngine(
                        PortfolioGreeksLimits(**kwargs))

    def test_positive_theta_floor_is_rejected(self):
        # A floor of +5000 would breach on every book that is not collecting more
        # than $5,000/day, i.e. almost all of them.
        with self.assertRaises(ValueError) as ctx:
            OptionsGreeksRealTimePortfolioAggregationEngine(
                PortfolioGreeksLimits(max_negative_theta_usd=5000.0))
        self.assertIn("signed floor", str(ctx.exception))

    def test_zero_theta_floor_is_legal(self):
        engine = OptionsGreeksRealTimePortfolioAggregationEngine(
            PortfolioGreeksLimits(max_negative_theta_usd=0.0))
        report = engine.aggregate_portfolio_greeks([_position(theta=-0.10)])
        self.assertTrue(report.is_theta_breached)

    def test_defaults_are_valid(self):
        engine = OptionsGreeksRealTimePortfolioAggregationEngine()
        self.assertEqual(engine.limits.max_dollar_delta_usd, 500000.0)
        self.assertEqual(engine.limits.max_negative_theta_usd, -5000.0)
        self.assertEqual(engine.limits.max_vega_usd, 10000.0)
        self.assertIsNone(engine.limits.max_abs_dollar_gamma_usd)
        self.assertTrue(math.isfinite(engine.limits.max_dollar_delta_usd))


if __name__ == "__main__":
    unittest.main()
