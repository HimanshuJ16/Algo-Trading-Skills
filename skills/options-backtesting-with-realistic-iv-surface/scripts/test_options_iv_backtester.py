"""
Unit tests for options-backtesting-with-realistic-iv-surface.

Expected values are derived independently of the implementation:

- The flat-volatility Black-Scholes benchmark reproduces the worked example in Hull,
  "Options, Futures, and Other Derivatives" (S = 42, K = 40, r = 10%, sigma = 20%,
  T = 0.5 -> d1 = 0.7693, d2 = 0.6278, call = 4.76, put = 0.81).
- Greeks are checked against central finite differences of the *price* function, so a
  transcription error in a closed-form Greek cannot be masked by the test repeating
  the same expression.
- Put-call parity (C - P = S e^-qT - K e^-rT) is an identity the pricer must satisfy
  but never computes, so it is an independent structural check.
"""
import logging
import math
import unittest

from options_iv_backtester import (
    MAX_SKEW_TERM_SCALE,
    MIN_STRIKE_IV,
    REFERENCE_TENOR_YEARS,
    OptionsIVSurfaceEngine,
)


class TestTermSkewScale(unittest.TestCase):
    """s(T): the term-structure axis of the surface."""

    def setUp(self):
        self.engine = OptionsIVSurfaceEngine(skew_alpha=-0.30, smile_beta=0.50)

    def test_scale_is_unity_at_reference_tenor(self):
        self.assertAlmostEqual(
            self.engine.term_skew_scale(REFERENCE_TENOR_YEARS), 1.0, places=12
        )

    def test_scale_follows_inverse_square_root_power_law(self):
        # gamma = 0.5: quadrupling the tenor halves the skew offset.
        four_x = self.engine.term_skew_scale(4.0 * REFERENCE_TENOR_YEARS)
        self.assertAlmostEqual(four_x, 0.5, places=12)
        # ...and quartering it doubles the offset.
        quarter_x = self.engine.term_skew_scale(REFERENCE_TENOR_YEARS / 4.0)
        self.assertAlmostEqual(quarter_x, 2.0, places=12)

    def test_scale_is_capped_and_logs_for_ultra_short_tenors(self):
        # 30 / 4^2 = 1.875 days is where the cap starts to bind.
        with self.assertLogs("options_iv_backtester", level=logging.WARNING):
            capped = self.engine.term_skew_scale(0.5 / 365.0)
        self.assertEqual(capped, MAX_SKEW_TERM_SCALE)

    def test_zero_decay_disables_term_structure(self):
        flat = OptionsIVSurfaceEngine(skew_term_decay=0.0)
        self.assertEqual(flat.term_skew_scale(1.0 / 365.0), 1.0)
        self.assertEqual(flat.term_skew_scale(5.0), 1.0)

    def test_non_positive_tenor_raises(self):
        for bad in (0.0, -0.25):
            with self.assertRaises(ValueError):
                self.engine.term_skew_scale(bad)

    def test_negative_decay_exponent_rejected_at_construction(self):
        with self.assertRaises(ValueError):
            OptionsIVSurfaceEngine(skew_term_decay=-0.5)


class TestStrikeIVSurface(unittest.TestCase):
    """sigma(m, T) = atm + [alpha (m-1) + beta (m-1)^2] * s(T)."""

    def setUp(self):
        self.engine = OptionsIVSurfaceEngine(skew_alpha=-0.30, smile_beta=0.50)
        self.spot = 100.0
        self.atm_vol = 0.20

    def test_atm_strike_returns_atm_vol_at_every_tenor(self):
        for tte in (7 / 365.0, REFERENCE_TENOR_YEARS, 2.0):
            self.assertAlmostEqual(
                self.engine.get_strike_iv(self.spot, self.spot, tte, self.atm_vol),
                self.atm_vol,
                places=12,
            )

    def test_documented_formula_is_applied_undamped_at_reference_tenor(self):
        # Regression guard: the offset was previously halved by an undocumented 0.5
        # factor, so alpha = -0.30 behaved as -0.15. Hand-computed at m = 0.90:
        #   offset = -0.30 * (-0.10) + 0.50 * 0.01 = 0.030 + 0.005 = 0.035
        #   sigma  = 0.20 + 0.035 * 1.0 = 0.235      (NOT 0.2175)
        iv = self.engine.get_strike_iv(self.spot, 90.0, REFERENCE_TENOR_YEARS, self.atm_vol)
        self.assertAlmostEqual(iv, 0.235, places=12)

    def test_otm_put_carries_skew_premium_over_otm_call(self):
        put_wing = self.engine.get_strike_iv(self.spot, 90.0, 0.25, self.atm_vol)
        call_wing = self.engine.get_strike_iv(self.spot, 110.0, 0.25, self.atm_vol)
        self.assertGreater(put_wing, self.atm_vol)
        self.assertGreater(put_wing, call_wing)

    def test_skew_flattens_with_maturity(self):
        # The core term-structure claim: a 2-year 10%-OTM put must show materially
        # less skew premium than a 1-week one.
        near = self.engine.get_strike_iv(self.spot, 90.0, 7 / 365.0, self.atm_vol)
        far = self.engine.get_strike_iv(self.spot, 90.0, 2.0, self.atm_vol)
        self.assertGreater(near - self.atm_vol, far - self.atm_vol)
        # Hand-computed: s(2y) = sqrt((30/365) / 2) = 0.202721...
        expected_far = self.atm_vol + 0.035 * math.sqrt(REFERENCE_TENOR_YEARS / 2.0)
        self.assertAlmostEqual(far, expected_far, places=12)

    def test_extreme_wing_clamps_low_and_logs(self):
        # alpha = -3.0 at m = 2.0 drives the raw offset to -3.0 + 1.0 = -2.0.
        steep = OptionsIVSurfaceEngine(skew_alpha=-3.0, smile_beta=1.0)
        with self.assertLogs("options_iv_backtester", level=logging.WARNING):
            iv = steep.get_strike_iv(self.spot, 200.0, REFERENCE_TENOR_YEARS, 0.20)
        self.assertEqual(iv, MIN_STRIKE_IV)

    def test_invalid_surface_inputs_raise(self):
        for kwargs in (
            {"spot": 0.0, "strike": 100.0, "tte_years": 0.25, "atm_vol": 0.20},
            {"spot": -100.0, "strike": 100.0, "tte_years": 0.25, "atm_vol": 0.20},
            {"spot": 100.0, "strike": 0.0, "tte_years": 0.25, "atm_vol": 0.20},
            {"spot": 100.0, "strike": 100.0, "tte_years": 0.25, "atm_vol": 0.0},
            {"spot": float("nan"), "strike": 100.0, "tte_years": 0.25, "atm_vol": 0.20},
            {"spot": 100.0, "strike": 100.0, "tte_years": float("inf"), "atm_vol": 0.20},
        ):
            with self.subTest(**kwargs):
                with self.assertRaises(ValueError):
                    self.engine.get_strike_iv(**kwargs)


class TestBlackScholesBenchmark(unittest.TestCase):
    """Flat surface (alpha = beta = 0) must reproduce textbook Black-Scholes."""

    def setUp(self):
        # Flat smile so the surface returns exactly atm_vol and the pricer alone is
        # under test.
        self.engine = OptionsIVSurfaceEngine(
            risk_free_rate=0.10, skew_alpha=0.0, smile_beta=0.0
        )
        self.args = dict(spot=42.0, strike=40.0, tte_years=0.5, atm_vol=0.20)

    def test_matches_hull_worked_example(self):
        call = self.engine.price_option("CALL", **self.args)
        put = self.engine.price_option("PUT", **self.args)
        # Hull: call = 4.76, put = 0.81 (2dp as published).
        self.assertAlmostEqual(call.option_price, 4.76, places=2)
        self.assertAlmostEqual(put.option_price, 0.81, places=2)

    def test_price_is_not_quantized_to_cents(self):
        # Rounding to 2dp would make these equal; the pricer must not round.
        call = self.engine.price_option("CALL", **self.args)
        self.assertNotEqual(round(call.option_price, 2), call.option_price)

    def test_deep_otm_price_is_not_floored_to_a_synthetic_tick(self):
        # A 1-day 50%-OTM call is worth far less than a cent. The old engine floored
        # every price at 0.01, inventing premium on wings a backtest should let
        # expire worthless.
        far = self.engine.price_option(
            "CALL", spot=100.0, strike=150.0, tte_years=1 / 365.0, atm_vol=0.20
        )
        self.assertGreaterEqual(far.option_price, 0.0)
        self.assertLess(far.option_price, 1e-6)


class TestPutCallParity(unittest.TestCase):
    """C - P = S e^-qT - K e^-rT, an identity the pricer never computes."""

    def _assert_parity(self, engine, spot, strike, tte, q):
        call = engine.price_option("CALL", spot, strike, tte, 0.20, dividend_yield=q)
        put = engine.price_option("PUT", spot, strike, tte, 0.20, dividend_yield=q)
        # Both legs must have been priced on the same strike IV for parity to hold.
        self.assertEqual(call.strike_iv, put.strike_iv)
        expected = spot * math.exp(-q * tte) - strike * math.exp(-engine.risk_free_rate * tte)
        self.assertAlmostEqual(call.option_price - put.option_price, expected, places=10)

    def test_parity_holds_on_the_skewed_surface(self):
        engine = OptionsIVSurfaceEngine(risk_free_rate=0.05, skew_alpha=-0.30, smile_beta=0.50)
        for strike in (80.0, 100.0, 120.0):
            with self.subTest(strike=strike):
                self._assert_parity(engine, 100.0, strike, 0.25, q=0.0)

    def test_parity_holds_with_a_dividend_yield(self):
        engine = OptionsIVSurfaceEngine(risk_free_rate=0.05, skew_alpha=-0.30, smile_beta=0.50)
        self._assert_parity(engine, 100.0, 95.0, 0.75, q=0.035)


class TestDividendYield(unittest.TestCase):
    """Merton continuous-yield extension."""

    def setUp(self):
        self.engine = OptionsIVSurfaceEngine(
            risk_free_rate=0.10, skew_alpha=0.0, smile_beta=0.0
        )

    def test_yield_lowers_calls_and_raises_puts(self):
        base = dict(spot=42.0, strike=40.0, tte_years=0.5, atm_vol=0.20)
        call_no_div = self.engine.price_option("CALL", **base)
        call_div = self.engine.price_option("CALL", dividend_yield=0.03, **base)
        put_no_div = self.engine.price_option("PUT", **base)
        put_div = self.engine.price_option("PUT", dividend_yield=0.03, **base)
        self.assertLess(call_div.option_price, call_no_div.option_price)
        self.assertGreater(put_div.option_price, put_no_div.option_price)

    def test_matches_independently_computed_merton_values(self):
        # S=42, K=40, r=10%, q=3%, sigma=20%, T=0.5, computed from the closed form in
        # Merton (1973) / Hull with the e^-qT substitution.
        call = self.engine.price_option(
            "CALL", spot=42.0, strike=40.0, tte_years=0.5, atm_vol=0.20, dividend_yield=0.03
        )
        put = self.engine.price_option(
            "PUT", spot=42.0, strike=40.0, tte_years=0.5, atm_vol=0.20, dividend_yield=0.03
        )
        self.assertAlmostEqual(call.option_price, 4.282312, places=6)
        self.assertAlmostEqual(put.option_price, 0.956787, places=6)

    def test_engine_level_yield_is_used_when_not_overridden(self):
        with_default = OptionsIVSurfaceEngine(
            risk_free_rate=0.10, skew_alpha=0.0, smile_beta=0.0, dividend_yield=0.03
        )
        implicit = with_default.price_option("CALL", 42.0, 40.0, 0.5, 0.20)
        explicit = self.engine.price_option(
            "CALL", 42.0, 40.0, 0.5, 0.20, dividend_yield=0.03
        )
        self.assertAlmostEqual(implicit.option_price, explicit.option_price, places=12)
        self.assertEqual(implicit.dividend_yield, 0.03)


class TestGreeksAgainstFiniteDifferences(unittest.TestCase):
    """
    Each analytic Greek is compared to a central difference of the price function.
    The surface is held flat so bumping S does not also move the strike IV, which
    would fold a surface derivative into the measured delta.
    """

    def setUp(self):
        self.engine = OptionsIVSurfaceEngine(
            risk_free_rate=0.05, skew_alpha=0.0, smile_beta=0.0, dividend_yield=0.02
        )
        self.spot = 100.0
        self.strike = 105.0
        self.tte = 0.5
        self.vol = 0.25

    def _price(self, spot=None, tte=None, vol=None, option_type="CALL"):
        return self.engine.price_option(
            option_type,
            self.spot if spot is None else spot,
            self.strike,
            self.tte if tte is None else tte,
            self.vol if vol is None else vol,
        ).option_price

    def test_delta_and_gamma_match_finite_differences(self):
        h = 1e-4 * self.spot
        for option_type in ("CALL", "PUT"):
            with self.subTest(option_type=option_type):
                up = self._price(spot=self.spot + h, option_type=option_type)
                down = self._price(spot=self.spot - h, option_type=option_type)
                mid = self._price(option_type=option_type)
                greeks = self.engine.price_option(
                    option_type, self.spot, self.strike, self.tte, self.vol
                ).greeks
                self.assertAlmostEqual(greeks.delta, (up - down) / (2 * h), places=7)
                self.assertAlmostEqual(
                    greeks.gamma, (up - 2 * mid + down) / (h * h), places=6
                )

    def test_vega_matches_finite_difference_per_volatility_point(self):
        h = 1e-5
        for option_type in ("CALL", "PUT"):
            with self.subTest(option_type=option_type):
                up = self._price(vol=self.vol + h, option_type=option_type)
                down = self._price(vol=self.vol - h, option_type=option_type)
                greeks = self.engine.price_option(
                    option_type, self.spot, self.strike, self.tte, self.vol
                ).greeks
                # Analytic vega is per 1 vol POINT, the difference is per unit sigma.
                self.assertAlmostEqual(greeks.vega * 100.0, (up - down) / (2 * h), places=6)

    def test_theta_matches_finite_difference_per_calendar_day(self):
        h = 1e-5
        for option_type in ("CALL", "PUT"):
            with self.subTest(option_type=option_type):
                # Theta is dV/dt = -dV/dT; one calendar day of decay.
                longer = self._price(tte=self.tte + h, option_type=option_type)
                shorter = self._price(tte=self.tte - h, option_type=option_type)
                d_price_d_tte = (longer - shorter) / (2 * h)
                greeks = self.engine.price_option(
                    option_type, self.spot, self.strike, self.tte, self.vol
                ).greeks
                self.assertAlmostEqual(greeks.theta, -d_price_d_tte / 365.0, places=8)

    def test_put_delta_is_negative_and_call_delta_bounded_by_dividend_discount(self):
        call = self.engine.price_option("CALL", 200.0, 100.0, self.tte, self.vol).greeks
        put = self.engine.price_option("PUT", 50.0, 100.0, self.tte, self.vol).greeks
        discount_q = math.exp(-0.02 * self.tte)
        self.assertLessEqual(call.delta, discount_q + 1e-12)
        self.assertGreater(call.delta, 0.0)
        self.assertGreaterEqual(put.delta, -discount_q - 1e-12)
        self.assertLess(put.delta, 0.0)


class TestExpiryHandling(unittest.TestCase):
    """A backtest holding to expiry must settle at intrinsic value."""

    def setUp(self):
        self.engine = OptionsIVSurfaceEngine(skew_alpha=-0.30, smile_beta=0.50)

    def test_zero_tenor_settles_at_intrinsic_with_no_time_value(self):
        itm_call = self.engine.price_option("CALL", 105.0, 100.0, 0.0, 0.20)
        self.assertTrue(itm_call.is_expired)
        self.assertEqual(itm_call.option_price, 5.0)
        self.assertEqual(itm_call.greeks.delta, 1.0)
        self.assertEqual(itm_call.greeks.gamma, 0.0)
        self.assertEqual(itm_call.greeks.theta, 0.0)
        self.assertEqual(itm_call.greeks.vega, 0.0)

        otm_put = self.engine.price_option("PUT", 105.0, 100.0, 0.0, 0.20)
        self.assertEqual(otm_put.option_price, 0.0)
        self.assertEqual(otm_put.greeks.delta, 0.0)

        itm_put = self.engine.price_option("PUT", 95.0, 100.0, 0.0, 0.20)
        self.assertEqual(itm_put.option_price, 5.0)
        self.assertEqual(itm_put.greeks.delta, -1.0)

    def test_price_converges_to_intrinsic_as_tenor_shrinks(self):
        # The skew scale is capped this far inside expiry, and says so.
        with self.assertLogs("options_iv_backtester", level=logging.WARNING):
            near = self.engine.price_option("CALL", 105.0, 100.0, 1e-8, 0.20)
        self.assertAlmostEqual(near.option_price, 5.0, places=4)
        self.assertFalse(near.is_expired)

    def test_negative_tenor_raises(self):
        with self.assertRaises(ValueError):
            self.engine.price_option("CALL", 100.0, 100.0, -0.01, 0.20)


class TestInputValidation(unittest.TestCase):
    """AI-agent misuse cases: wrong type strings and non-finite market data."""

    def setUp(self):
        self.engine = OptionsIVSurfaceEngine()

    def test_option_type_is_case_and_whitespace_insensitive(self):
        for spelling in ("call", "  Call ", "CALL"):
            with self.subTest(spelling=spelling):
                self.assertEqual(
                    self.engine.price_option(spelling, 100.0, 100.0, 0.25, 0.20).option_type,
                    "CALL",
                )

    def test_unknown_option_type_raises_instead_of_silently_pricing_a_put(self):
        for bad in ("C", "P", "calls", "", "CALL_SPREAD", None, 1):
            with self.subTest(bad=bad):
                with self.assertRaises(ValueError):
                    self.engine.price_option(bad, 100.0, 100.0, 0.25, 0.20)

    def test_non_finite_market_data_raises_rather_than_propagating_nan(self):
        nan = float("nan")
        for kwargs in (
            {"spot": nan, "strike": 100.0, "tte_years": 0.25, "atm_vol": 0.20},
            {"spot": 100.0, "strike": nan, "tte_years": 0.25, "atm_vol": 0.20},
            {"spot": 100.0, "strike": 100.0, "tte_years": nan, "atm_vol": 0.20},
            {"spot": 100.0, "strike": 100.0, "tte_years": 0.25, "atm_vol": nan},
        ):
            with self.subTest(**kwargs):
                with self.assertRaises(ValueError):
                    self.engine.price_option("CALL", **kwargs)

    def test_non_finite_dividend_yield_raises(self):
        with self.assertRaises(ValueError):
            self.engine.price_option(
                "CALL", 100.0, 100.0, 0.25, 0.20, dividend_yield=float("inf")
            )

    def test_non_positive_spot_strike_or_vol_raises_instead_of_dividing_by_zero(self):
        for kwargs in (
            {"spot": 0.0, "strike": 100.0, "tte_years": 0.25, "atm_vol": 0.20},
            {"spot": 100.0, "strike": 0.0, "tte_years": 0.25, "atm_vol": 0.20},
            {"spot": -1.0, "strike": 100.0, "tte_years": 0.25, "atm_vol": 0.20},
            {"spot": 100.0, "strike": 100.0, "tte_years": 0.25, "atm_vol": -0.20},
        ):
            with self.subTest(**kwargs):
                with self.assertRaises(ValueError):
                    self.engine.price_option("CALL", **kwargs)


class TestSkewPricingImpact(unittest.TestCase):
    """The reason the skill exists: flat ATM IV misprices the wings."""

    def test_flat_iv_underprices_the_otm_put_hedge(self):
        skewed = OptionsIVSurfaceEngine(risk_free_rate=0.05, skew_alpha=-0.30, smile_beta=0.50)
        flat = OptionsIVSurfaceEngine(risk_free_rate=0.05, skew_alpha=0.0, smile_beta=0.0)
        args = dict(spot=100.0, strike=90.0, tte_years=REFERENCE_TENOR_YEARS, atm_vol=0.20)
        skewed_put = skewed.price_option("PUT", **args)
        flat_put = flat.price_option("PUT", **args)
        self.assertGreater(skewed_put.strike_iv, flat_put.strike_iv)
        self.assertGreater(skewed_put.option_price, flat_put.option_price)

    def test_result_records_the_surface_inputs_used(self):
        engine = OptionsIVSurfaceEngine(risk_free_rate=0.04, dividend_yield=0.01)
        res = engine.price_option("PUT", 100.0, 95.0, 4.0 * REFERENCE_TENOR_YEARS, 0.20)
        self.assertEqual(res.risk_free_rate, 0.04)
        self.assertEqual(res.dividend_yield, 0.01)
        self.assertAlmostEqual(res.term_skew_scale, 0.5, places=12)


if __name__ == "__main__":
    unittest.main()
