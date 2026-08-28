"""
Unit tests for the real-time Greeks recalculation engine.

Expected Greeks are derived by hand from the closed-form Black-Scholes-Merton
formulas in the module docstring at S = K = 100, sigma = 0.20, T = 1, r = q = 0,
where d1 = +0.10 and d2 = -0.10 exactly:

    N(0.1)   = 0.539827837277029
    phi(0.1) = 0.3969525474770118

    delta_call = 0.539827837277029
    delta_put  = 0.539827837277029 - 1 = -0.460172162722971
    gamma      = 0.3969525474770118 / (100 * 0.20 * 1) = 0.0198476273738506
    vega       = 100 * 0.3969525474770118 * 1 / 100    = 0.3969525474770118
    theta      = -(100 * 0.3969525474770118 * 0.20) / 2 / 365 = -0.0108754122596442

They are never taken from the implementation's own arithmetic.
"""
import math
import unittest

from real_time_greeks_recalculator import (
    METHOD_FULL_BS,
    METHOD_TAYLOR,
    STANDARD_US_EQUITY_OPTION_MULTIPLIER,
    STATUS_NO_POSITIONS,
    STATUS_RECALCULATED,
    TRIGGER_ANCHOR_AGE,
    TRIGGER_DELTA_OUT_OF_BOUNDS,
    TRIGGER_FORCED,
    TRIGGER_IV_MOVE,
    TRIGGER_NEAR_EXPIRY,
    TRIGGER_NO_ANCHOR,
    TRIGGER_NONE,
    TRIGGER_SPOT_MOVE,
    OptionPosition,
    RealTimeGreeksRecalculationEngine,
    RecalculationTriggerConfig,
    black_scholes_merton_greeks,
)

# Hand-derived reference values at S = K = 100, sigma = 0.20, T = 1, r = q = 0.
REF_DELTA_CALL = 0.539827837277029
REF_DELTA_PUT = -0.460172162722971
REF_GAMMA = 0.0198476273738506
REF_VEGA = 0.3969525474770118
REF_THETA = -0.0108754122596442


def _position(**overrides) -> OptionPosition:
    """A valid, unremarkable long ATM call; override one field to isolate a behaviour."""
    base = dict(
        symbol="AAPL261218C00100000",
        underlying_symbol="AAPL",
        option_type="CALL",
        strike=100.0,
        position_qty=10,
        multiplier=STANDARD_US_EQUITY_OPTION_MULTIPLIER,
        implied_vol=0.20,
        time_to_expiry_years=1.0,
    )
    base.update(overrides)
    return OptionPosition(**base)


class TestBlackScholesMertonGreeks(unittest.TestCase):
    """The closed form, against values derived independently of the implementation."""

    def test_atm_call_matches_hand_derived_greeks(self):
        g = black_scholes_merton_greeks(100.0, 100.0, 1.0, 0.20, "CALL")
        self.assertAlmostEqual(g.d1, 0.10, places=12)
        self.assertAlmostEqual(g.d2, -0.10, places=12)
        self.assertAlmostEqual(g.delta, REF_DELTA_CALL, places=12)
        self.assertAlmostEqual(g.gamma, REF_GAMMA, places=12)
        self.assertAlmostEqual(g.vega, REF_VEGA, places=12)
        self.assertAlmostEqual(g.theta, REF_THETA, places=12)
        # price = 100 N(d1) - 100 N(d2) = 100 (0.539827837 - 0.460172163)
        self.assertAlmostEqual(g.price, 7.965567455405804, places=10)

    def test_atm_put_delta_is_call_delta_minus_one(self):
        p = black_scholes_merton_greeks(100.0, 100.0, 1.0, 0.20, "PUT")
        self.assertAlmostEqual(p.delta, REF_DELTA_PUT, places=12)
        # Gamma and vega are strike-symmetric and shared between calls and puts.
        self.assertAlmostEqual(p.gamma, REF_GAMMA, places=12)
        self.assertAlmostEqual(p.vega, REF_VEGA, places=12)

    def test_put_call_delta_parity_carries_the_dividend_discount(self):
        # dC/dS - dP/dS = e^{-qT}, not 1, once the underlying yields.
        kwargs = dict(spot=100.0, strike=95.0, time_to_expiry_years=2.0,
                      implied_vol=0.25, risk_free_rate=0.04, dividend_yield=0.03)
        call = black_scholes_merton_greeks(option_type="CALL", **kwargs)
        put = black_scholes_merton_greeks(option_type="PUT", **kwargs)
        self.assertAlmostEqual(call.delta - put.delta, math.exp(-0.03 * 2.0), places=12)

    def test_put_call_price_parity(self):
        # C - P = S e^{-qT} - K e^{-rT}
        kwargs = dict(spot=110.0, strike=95.0, time_to_expiry_years=0.75,
                      implied_vol=0.30, risk_free_rate=0.05, dividend_yield=0.02)
        call = black_scholes_merton_greeks(option_type="CALL", **kwargs)
        put = black_scholes_merton_greeks(option_type="PUT", **kwargs)
        expected = 110.0 * math.exp(-0.02 * 0.75) - 95.0 * math.exp(-0.05 * 0.75)
        self.assertAlmostEqual(call.price - put.price, expected, places=10)

    def test_dividend_yield_lowers_call_delta(self):
        no_div = black_scholes_merton_greeks(100.0, 100.0, 1.0, 0.20, "CALL")
        with_div = black_scholes_merton_greeks(
            100.0, 100.0, 1.0, 0.20, "CALL", dividend_yield=0.05)
        self.assertLess(with_div.delta, no_div.delta)

    def test_deep_itm_call_delta_approaches_one_and_gamma_zero(self):
        g = black_scholes_merton_greeks(300.0, 100.0, 1.0, 0.20, "CALL")
        self.assertGreater(g.delta, 0.999)
        self.assertLessEqual(g.delta, 1.0)
        self.assertLess(g.gamma, 1e-6)

    def test_expired_or_negative_expiry_raises_rather_than_being_clamped(self):
        for bad_t in (0.0, -0.5):
            with self.assertRaises(ValueError) as ctx:
                black_scholes_merton_greeks(100.0, 100.0, bad_t, 0.20, "CALL")
            self.assertIn("time_to_expiry_years", str(ctx.exception))

    def test_non_positive_or_non_finite_inputs_raise(self):
        base = dict(spot=100.0, strike=100.0, time_to_expiry_years=1.0,
                    implied_vol=0.20, option_type="CALL")
        for field in ("spot", "strike", "implied_vol"):
            for bad in (0.0, -1.0, float("nan"), float("inf")):
                kwargs = dict(base)
                kwargs[field] = bad
                with self.assertRaises(ValueError):
                    black_scholes_merton_greeks(**kwargs)

    def test_unknown_option_type_raises_instead_of_defaulting_to_put(self):
        # Regression: an `if CALL else PUT` branch silently sign-flips delta on "C".
        for bad in ("C", "P", "call option", "", None):
            with self.assertRaises(ValueError):
                black_scholes_merton_greeks(100.0, 100.0, 1.0, 0.20, bad)

    def test_option_type_is_case_insensitive(self):
        lower = black_scholes_merton_greeks(100.0, 100.0, 1.0, 0.20, "call")
        upper = black_scholes_merton_greeks(100.0, 100.0, 1.0, 0.20, "CALL")
        self.assertEqual(lower.delta, upper.delta)


class TestFirstTickAndScaling(unittest.TestCase):
    """A contract the engine has never seen must be priced, then scaled correctly."""

    def setUp(self):
        self.engine = RealTimeGreeksRecalculationEngine()

    def test_first_sight_of_a_contract_forces_a_full_revaluation(self):
        report = self.engine.recalculate_portfolio_greeks("AAPL", 100.0, [_position()])
        result = report.position_results[0]
        self.assertEqual(report.status, STATUS_RECALCULATED)
        self.assertEqual(result.method, METHOD_FULL_BS)
        self.assertEqual(result.trigger_reason, TRIGGER_NO_ANCHOR)
        self.assertEqual(report.positions_full_revalued, 1)
        self.assertEqual(report.positions_taylor_updated, 0)
        # No anchor to compare against, so no approximation error is claimed.
        self.assertIsNone(result.spot_taylor_value_error_per_unit)

    def test_position_scaling_uses_deliverable_units(self):
        # 10 contracts x 100 deliverable units = 1,000 units.
        report = self.engine.recalculate_portfolio_greeks("AAPL", 100.0, [_position()])
        r = report.position_results[0]
        self.assertAlmostEqual(r.position_delta_units, round(1000 * REF_DELTA_CALL, 2))
        self.assertAlmostEqual(r.position_dollar_delta,
                               round(1000 * REF_DELTA_CALL * 100.0, 2))
        self.assertAlmostEqual(r.position_gamma_units, round(1000 * REF_GAMMA, 6))
        # Dollar gamma = gamma units x S^2 x 0.01 = dollar delta gained on a +1% move.
        self.assertAlmostEqual(r.position_dollar_gamma,
                               round(1000 * REF_GAMMA * 100.0 * 100.0 * 0.01, 2))
        self.assertAlmostEqual(r.position_vega_usd, round(1000 * REF_VEGA, 2))
        self.assertAlmostEqual(r.position_theta_daily_usd, round(1000 * REF_THETA, 2))

    def test_adjusted_deliverable_is_not_assumed_to_be_one_hundred(self):
        # 10 contracts against a post-reverse-split deliverable of 5 units.
        report = self.engine.recalculate_portfolio_greeks(
            "AAPL", 100.0, [_position(multiplier=5.0)])
        self.assertAlmostEqual(report.position_results[0].position_delta_units,
                               round(50 * REF_DELTA_CALL, 2))

    def test_short_position_sign_comes_from_quantity(self):
        report = self.engine.recalculate_portfolio_greeks(
            "AAPL", 100.0, [_position(position_qty=-10)])
        r = report.position_results[0]
        # Per-unit delta stays as quoted; the position delta is negative.
        self.assertAlmostEqual(r.delta, round(REF_DELTA_CALL, 6))
        self.assertAlmostEqual(r.position_delta_units, round(-1000 * REF_DELTA_CALL, 2))
        # Short gamma, short vega, positive (collected) theta.
        self.assertLess(r.position_gamma_units, 0.0)
        self.assertLess(r.position_vega_usd, 0.0)
        self.assertGreater(r.position_theta_daily_usd, 0.0)

    def test_nets_are_order_independent(self):
        legs = [
            _position(symbol="A", position_qty=7),
            _position(symbol="B", option_type="PUT", position_qty=-3),
            _position(symbol="C", strike=110.0, position_qty=1e6),
            _position(symbol="D", strike=90.0, position_qty=-1e6),
        ]
        forward = RealTimeGreeksRecalculationEngine().recalculate_portfolio_greeks(
            "AAPL", 100.0, legs)
        backward = RealTimeGreeksRecalculationEngine().recalculate_portfolio_greeks(
            "AAPL", 100.0, list(reversed(legs)))
        self.assertEqual(forward.net_delta_units, backward.net_delta_units)
        self.assertEqual(forward.net_dollar_delta, backward.net_dollar_delta)
        self.assertEqual(forward.net_vega_usd, backward.net_vega_usd)


class TestTaylorStep(unittest.TestCase):
    """The cheap path, and the arithmetic it is allowed to use."""

    def setUp(self):
        self.engine = RealTimeGreeksRecalculationEngine()
        self.pos = _position()
        self.engine.recalculate_portfolio_greeks("AAPL", 100.0, [self.pos], 0.0)

    def test_small_move_steps_delta_by_gamma_times_the_move(self):
        # 100.00 -> 100.20 is +0.20%, inside the 0.5% threshold.
        report = self.engine.recalculate_portfolio_greeks(
            "AAPL", 100.20, [self.pos], 1.0)
        r = report.position_results[0]
        self.assertEqual(r.method, METHOD_TAYLOR)
        self.assertEqual(r.trigger_reason, TRIGGER_NONE)
        self.assertEqual(report.triggers, [])
        expected_delta = REF_DELTA_CALL + REF_GAMMA * 0.20
        self.assertAlmostEqual(r.delta, round(expected_delta, 6), places=6)

    def test_gamma_vega_and_theta_are_carried_forward_frozen(self):
        report = self.engine.recalculate_portfolio_greeks(
            "AAPL", 100.20, [self.pos], 1.0)
        r = report.position_results[0]
        self.assertAlmostEqual(r.gamma, round(REF_GAMMA, 6), places=9)
        self.assertAlmostEqual(r.vega, round(REF_VEGA, 6), places=9)
        self.assertAlmostEqual(r.theta, round(REF_THETA, 6), places=9)

    def test_taylor_value_change_is_the_second_order_expansion(self):
        report = self.engine.recalculate_portfolio_greeks(
            "AAPL", 100.20, [self.pos], 1.0)
        expected = REF_DELTA_CALL * 0.20 + 0.5 * REF_GAMMA * 0.20 ** 2
        self.assertAlmostEqual(
            report.position_results[0].taylor_value_change_per_unit,
            round(expected, 6), places=6)

    def test_no_approximation_error_is_reported_on_a_taylor_tick(self):
        # The engine has not repriced, so it has no truth to compare against and must
        # not invent one.
        report = self.engine.recalculate_portfolio_greeks(
            "AAPL", 100.20, [self.pos], 1.0)
        self.assertIsNone(
            report.position_results[0].spot_taylor_value_error_per_unit)

    def test_anchor_is_unchanged_by_a_taylor_tick(self):
        self.engine.recalculate_portfolio_greeks("AAPL", 100.20, [self.pos], 1.0)
        anchor = self.engine.anchor_for(self.pos.symbol)
        self.assertEqual(anchor.spot, 100.0)
        self.assertAlmostEqual(anchor.delta, REF_DELTA_CALL, places=12)


class TestAnchorDrift(unittest.TestCase):
    """
    The defect this engine exists to prevent: measuring the move against the previous
    tick instead of the last full revaluation.
    """

    def setUp(self):
        self.engine = RealTimeGreeksRecalculationEngine()
        self.pos = _position()
        self.engine.recalculate_portfolio_greeks("AAPL", 100.0, [self.pos], 0.0)

    def test_cumulative_drift_across_sub_threshold_ticks_forces_a_reval(self):
        # Each step is +0.4%, individually inside the 0.5% threshold. Against a
        # last-tick baseline neither would ever reprice; against the anchor the
        # second step is 0.8% away and must.
        first = self.engine.recalculate_portfolio_greeks(
            "AAPL", 100.40, [self.pos], 1.0)
        self.assertEqual(first.position_results[0].method, METHOD_TAYLOR)
        self.assertAlmostEqual(
            first.position_results[0].spot_drift_from_anchor_pct, 0.004, places=6)

        second = self.engine.recalculate_portfolio_greeks(
            "AAPL", 100.80, [self.pos], 2.0)
        self.assertEqual(second.position_results[0].method, METHOD_FULL_BS)
        self.assertEqual(second.position_results[0].trigger_reason, TRIGGER_SPOT_MOVE)
        self.assertAlmostEqual(
            second.position_results[0].spot_drift_from_anchor_pct, 0.008, places=6)

    def test_a_long_ramp_of_micro_ticks_cannot_run_away(self):
        # 60 consecutive +0.1% ticks. A last-tick baseline would never trip and would
        # carry the book +6% on a frozen gamma; the anchor must reprice repeatedly and
        # the published delta must stay inside [0, 1].
        spot = 100.0
        revals = 0
        for i in range(1, 61):
            spot *= 1.001
            report = self.engine.recalculate_portfolio_greeks(
                "AAPL", spot, [self.pos], float(i))
            r = report.position_results[0]
            self.assertGreaterEqual(r.delta, 0.0)
            self.assertLessEqual(r.delta, 1.0)
            if r.method == METHOD_FULL_BS:
                revals += 1
        self.assertGreaterEqual(revals, 5)
        # And the anchor tracks the market rather than the original 100.
        self.assertGreater(self.engine.anchor_for(self.pos.symbol).spot, 105.0)

    def test_reval_reports_the_realised_error_of_the_step_it_replaced(self):
        report = self.engine.recalculate_portfolio_greeks("AAPL", 102.0, [self.pos], 1.0)
        r = report.position_results[0]
        self.assertEqual(r.method, METHOD_FULL_BS)
        error = r.spot_taylor_value_error_per_unit
        self.assertIsNotNone(error)
        # Truth at the anchor's vol and expiry, minus the delta-gamma estimate.
        anchor_price = black_scholes_merton_greeks(100.0, 100.0, 1.0, 0.20, "CALL").price
        true_at_new_spot = black_scholes_merton_greeks(
            102.0, 100.0, 1.0, 0.20, "CALL").price
        approx = REF_DELTA_CALL * 2.0 + 0.5 * REF_GAMMA * 4.0
        self.assertAlmostEqual(
            error, round((true_at_new_spot - anchor_price) - approx, 6), places=6)


class TestTriggers(unittest.TestCase):
    """Every reason a cached Greek stops being usable."""

    def setUp(self):
        self.engine = RealTimeGreeksRecalculationEngine()
        self.pos = _position()
        self.engine.recalculate_portfolio_greeks("AAPL", 100.0, [self.pos], 0.0)

    def test_threshold_is_strict_so_a_move_exactly_at_it_still_steps(self):
        report = self.engine.recalculate_portfolio_greeks("AAPL", 100.5, [self.pos], 1.0)
        self.assertEqual(report.position_results[0].spot_drift_from_anchor_pct, 0.005)
        self.assertEqual(report.position_results[0].method, METHOD_TAYLOR)

    def test_a_move_past_the_threshold_revalues(self):
        report = self.engine.recalculate_portfolio_greeks("AAPL", 100.6, [self.pos], 1.0)
        self.assertEqual(report.position_results[0].method, METHOD_FULL_BS)
        self.assertEqual(report.triggers, [TRIGGER_SPOT_MOVE])

    def test_a_downward_move_triggers_symmetrically(self):
        report = self.engine.recalculate_portfolio_greeks("AAPL", 99.4, [self.pos], 1.0)
        self.assertEqual(report.position_results[0].method, METHOD_FULL_BS)
        self.assertEqual(report.position_results[0].trigger_reason, TRIGGER_SPOT_MOVE)

    def test_vol_move_revalues_with_no_spot_move_at_all(self):
        # Cached gamma and vega are functions of vol; a vol repricing invalidates them
        # even on a perfectly flat tape.
        moved = _position(implied_vol=0.21)
        report = self.engine.recalculate_portfolio_greeks("AAPL", 100.0, [moved], 1.0)
        self.assertEqual(report.position_results[0].method, METHOD_FULL_BS)
        self.assertEqual(report.position_results[0].trigger_reason, TRIGGER_IV_MOVE)
        self.assertEqual(report.position_results[0].spot_drift_from_anchor_pct, 0.0)

    def test_a_vol_move_inside_the_band_still_steps(self):
        report = self.engine.recalculate_portfolio_greeks(
            "AAPL", 100.0, [_position(implied_vol=0.2004)], 1.0)
        self.assertEqual(report.position_results[0].method, METHOD_TAYLOR)

    def test_stale_anchor_revalues_on_a_quiet_book(self):
        # No spot move, no vol move: only the clock has advanced past 60s.
        report = self.engine.recalculate_portfolio_greeks(
            "AAPL", 100.0, [self.pos], 61.0)
        self.assertEqual(report.position_results[0].method, METHOD_FULL_BS)
        self.assertEqual(report.position_results[0].trigger_reason, TRIGGER_ANCHOR_AGE)

    def test_near_expiry_always_revalues(self):
        # Inside one calendar day the expansion is the wrong shape, not merely stale.
        near = _position(time_to_expiry_years=0.5 / 365.0)
        engine = RealTimeGreeksRecalculationEngine()
        engine.recalculate_portfolio_greeks("AAPL", 100.0, [near], 0.0)
        report = engine.recalculate_portfolio_greeks("AAPL", 100.0001, [near], 0.1)
        self.assertEqual(report.position_results[0].method, METHOD_FULL_BS)
        self.assertEqual(report.position_results[0].trigger_reason, TRIGGER_NEAR_EXPIRY)

    def test_taylor_delta_outside_the_admissible_band_forces_a_reval(self):
        # A huge gamma and a loose spot threshold let the linear step push a call
        # delta above 1. That is not a small error; it is an impossible Greek.
        config = RecalculationTriggerConfig(full_recalc_spot_move_pct=10.0,
                                            max_anchor_age_seconds=1e9)
        engine = RealTimeGreeksRecalculationEngine(config)
        pos = _position(implied_vol=0.05, time_to_expiry_years=0.02)
        engine.recalculate_portfolio_greeks("AAPL", 100.0, [pos], 0.0)
        report = engine.recalculate_portfolio_greeks("AAPL", 130.0, [pos], 1.0)
        r = report.position_results[0]
        self.assertEqual(r.method, METHOD_FULL_BS)
        self.assertEqual(r.trigger_reason, TRIGGER_DELTA_OUT_OF_BOUNDS)
        self.assertLessEqual(r.delta, 1.0)

    def test_delta_bound_guard_can_be_disabled(self):
        config = RecalculationTriggerConfig(full_recalc_spot_move_pct=10.0,
                                            max_anchor_age_seconds=1e9,
                                            enforce_delta_bounds=False)
        engine = RealTimeGreeksRecalculationEngine(config)
        pos = _position(implied_vol=0.05, time_to_expiry_years=0.02)
        engine.recalculate_portfolio_greeks("AAPL", 100.0, [pos], 0.0)
        report = engine.recalculate_portfolio_greeks("AAPL", 130.0, [pos], 1.0)
        self.assertEqual(report.position_results[0].method, METHOD_TAYLOR)
        self.assertGreater(report.position_results[0].delta, 1.0)

    def test_caller_can_force_a_full_revaluation(self):
        report = self.engine.recalculate_portfolio_greeks(
            "AAPL", 100.0, [self.pos], 1.0, force_full_reval=True)
        self.assertEqual(report.position_results[0].trigger_reason, TRIGGER_FORCED)

    def test_reset_drops_the_anchor_and_forces_a_reval(self):
        self.engine.reset(self.pos.symbol)
        self.assertIsNone(self.engine.anchor_for(self.pos.symbol))
        report = self.engine.recalculate_portfolio_greeks(
            "AAPL", 100.0, [self.pos], 1.0)
        self.assertEqual(report.position_results[0].trigger_reason, TRIGGER_NO_ANCHOR)

    def test_reset_all_clears_every_anchor_and_the_tick_clock(self):
        self.engine.reset()
        self.assertIsNone(self.engine.anchor_for(self.pos.symbol))
        # The out-of-order guard is cleared too, so an earlier timestamp is accepted.
        self.engine.recalculate_portfolio_greeks("AAPL", 100.0, [self.pos], 0.0)


class TestUnderlyingIsolation(unittest.TestCase):
    """A tick is one price for one underlying. It must not reach any other name."""

    def setUp(self):
        self.engine = RealTimeGreeksRecalculationEngine()

    def test_positions_on_other_underlyings_are_excluded_not_repriced(self):
        aapl = _position(symbol="AAPL_C", underlying_symbol="AAPL")
        msft = _position(symbol="MSFT_C", underlying_symbol="MSFT")
        report = self.engine.recalculate_portfolio_greeks("AAPL", 100.0, [aapl, msft])
        self.assertEqual(len(report.position_results), 1)
        self.assertEqual(report.position_results[0].symbol, "AAPL_C")
        self.assertEqual(report.positions_skipped_other_underlying, 1)
        # The MSFT leg was never priced at AAPL's spot, so it has no anchor.
        self.assertIsNone(self.engine.anchor_for("MSFT_C"))
        self.assertAlmostEqual(report.net_delta_units, round(1000 * REF_DELTA_CALL, 2))

    def test_underlying_match_is_case_insensitive(self):
        report = self.engine.recalculate_portfolio_greeks(
            "aapl", 100.0, [_position(underlying_symbol="AAPL")])
        self.assertEqual(report.positions_skipped_other_underlying, 0)
        self.assertEqual(len(report.position_results), 1)

    def test_a_book_with_no_leg_on_this_underlying_reports_no_positions(self):
        report = self.engine.recalculate_portfolio_greeks(
            "TSLA", 100.0, [_position(underlying_symbol="AAPL")])
        self.assertEqual(report.status, STATUS_NO_POSITIONS)
        self.assertEqual(report.positions_skipped_other_underlying, 1)
        self.assertEqual(report.net_delta_units, 0.0)
        self.assertEqual(report.position_results, [])

    def test_empty_book_reports_no_positions(self):
        report = self.engine.recalculate_portfolio_greeks("AAPL", 100.0, [])
        self.assertEqual(report.status, STATUS_NO_POSITIONS)
        self.assertEqual(report.positions_skipped_other_underlying, 0)

    def test_two_underlyings_keep_independent_anchors(self):
        aapl = _position(symbol="AAPL_C", underlying_symbol="AAPL")
        msft = _position(symbol="MSFT_C", underlying_symbol="MSFT")
        self.engine.recalculate_portfolio_greeks("AAPL", 100.0, [aapl, msft], 0.0)
        self.engine.recalculate_portfolio_greeks("MSFT", 400.0, [aapl, msft], 0.0)
        self.assertEqual(self.engine.anchor_for("AAPL_C").spot, 100.0)
        self.assertEqual(self.engine.anchor_for("MSFT_C").spot, 400.0)


class TestTickAndPositionValidation(unittest.TestCase):
    """Corrupt input must raise, never net."""

    def setUp(self):
        self.engine = RealTimeGreeksRecalculationEngine()

    def test_non_positive_or_non_finite_spot_raises(self):
        for bad in (0.0, -1.0, float("nan"), float("inf")):
            with self.assertRaises(ValueError):
                self.engine.recalculate_portfolio_greeks("AAPL", bad, [_position()])

    def test_nan_spot_is_rejected_rather_than_reading_as_a_small_move(self):
        # abs(nan) > threshold is False, so an unvalidated NaN would look like a
        # micro-tick and pin the book on the Taylor path forever.
        self.engine.recalculate_portfolio_greeks("AAPL", 100.0, [_position()], 0.0)
        with self.assertRaises(ValueError):
            self.engine.recalculate_portfolio_greeks(
                "AAPL", float("nan"), [_position()], 1.0)

    def test_blank_underlying_symbol_raises(self):
        with self.assertRaises(ValueError):
            self.engine.recalculate_portfolio_greeks("   ", 100.0, [_position()])

    def test_duplicate_position_symbol_raises(self):
        with self.assertRaises(ValueError) as ctx:
            self.engine.recalculate_portfolio_greeks(
                "AAPL", 100.0, [_position(), _position()])
        self.assertIn("duplicate", str(ctx.exception).lower())

    def test_out_of_order_tick_raises(self):
        self.engine.recalculate_portfolio_greeks("AAPL", 100.0, [_position()], 10.0)
        with self.assertRaises(ValueError) as ctx:
            self.engine.recalculate_portfolio_greeks("AAPL", 100.1, [_position()], 9.0)
        self.assertIn("out-of-order", str(ctx.exception))

    def test_repeated_timestamp_is_accepted(self):
        # Two ticks inside one clock granule is normal; only going backwards is not.
        self.engine.recalculate_portfolio_greeks("AAPL", 100.0, [_position()], 10.0)
        self.engine.recalculate_portfolio_greeks("AAPL", 100.1, [_position()], 10.0)

    def test_invalid_position_fields_raise(self):
        bad_fields = [
            dict(multiplier=0.0),
            dict(multiplier=-100.0),
            dict(strike=0.0),
            dict(implied_vol=0.0),
            dict(implied_vol=-0.2),
            dict(time_to_expiry_years=0.0),
            dict(time_to_expiry_years=-1.0),
            dict(position_qty=float("nan")),
            dict(position_qty=float("inf")),
            dict(symbol="  "),
            dict(underlying_symbol=""),
            dict(option_type="C"),
            dict(risk_free_rate=float("nan")),
            dict(dividend_yield=float("inf")),
        ]
        for overrides in bad_fields:
            with self.subTest(**overrides):
                with self.assertRaises(ValueError):
                    self.engine.recalculate_portfolio_greeks(
                        "AAPL", 100.0, [_position(**overrides)])

    def test_string_numbers_are_rejected_not_coerced(self):
        with self.assertRaises(ValueError):
            self.engine.recalculate_portfolio_greeks(
                "AAPL", 100.0, [_position(implied_vol="0.20")])

    def test_validate_normalises_in_place_and_is_idempotent(self):
        # Documented in the module: a caller whose book is stable between changes may
        # validate once at load. That is only safe if validate() is a fixed point.
        pos = _position(symbol="  AAPL_C  ", underlying_symbol=" aapl ",
                        option_type="call")
        pos.validate()
        self.assertEqual((pos.symbol, pos.underlying_symbol, pos.option_type),
                         ("AAPL_C", "aapl", "CALL"))
        snapshot = vars(pos).copy()
        pos.validate()
        self.assertEqual(vars(pos), snapshot)

    def test_non_position_object_raises(self):
        with self.assertRaises(ValueError):
            self.engine.recalculate_portfolio_greeks(
                "AAPL", 100.0, [{"symbol": "AAPL_C"}])

    def test_one_bad_leg_rejects_the_whole_tick(self):
        good = _position(symbol="GOOD")
        bad = _position(symbol="BAD", multiplier=0.0)
        with self.assertRaises(ValueError):
            self.engine.recalculate_portfolio_greeks("AAPL", 100.0, [good, bad])
        # No partial snapshot was published, and no anchor was created.
        self.assertIsNone(self.engine.anchor_for("GOOD"))


class TestConfigValidation(unittest.TestCase):
    """A silently disabled trigger is the worst outcome, so reject one at construction."""

    def test_non_positive_thresholds_raise(self):
        for kwargs in (dict(full_recalc_spot_move_pct=0.0),
                       dict(full_recalc_spot_move_pct=-0.01),
                       dict(full_recalc_iv_move_abs=0.0),
                       dict(max_anchor_age_seconds=-1.0),
                       dict(near_expiry_years=0.0)):
            with self.subTest(**kwargs):
                with self.assertRaises(ValueError):
                    RecalculationTriggerConfig(**kwargs)

    def test_non_finite_threshold_raises(self):
        with self.assertRaises(ValueError):
            RecalculationTriggerConfig(full_recalc_spot_move_pct=float("nan"))

    def test_defaults_are_the_documented_ones(self):
        config = RecalculationTriggerConfig()
        self.assertEqual(config.full_recalc_spot_move_pct, 0.005)
        self.assertEqual(config.full_recalc_iv_move_abs, 0.005)
        self.assertEqual(config.max_anchor_age_seconds, 60.0)
        self.assertAlmostEqual(config.near_expiry_years, 1.0 / 365.0)
        self.assertTrue(config.enforce_delta_bounds)

    def test_engine_rejects_a_foreign_config_object(self):
        with self.assertRaises(ValueError):
            RealTimeGreeksRecalculationEngine(config={"full_recalc_spot_move_pct": 0.01})


if __name__ == "__main__":
    unittest.main()
