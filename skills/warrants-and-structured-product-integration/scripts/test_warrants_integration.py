"""Tests for warrants_integration.

Expected values here are derived *independently of the engine's own formulas*:

* Prices are checked against the canonical Black-Scholes worked example
  BS(S=100, K=100, T=1y, r=5%, sigma=20%) = 10.450584 / 5.573526, against
  put-call parity, and against hand-written arithmetic for the CBBC convention.
* Every Greek is checked against a Richardson-extrapolated central difference of
  the *price*, which never touches the closed-form Greek expressions.

Several tests are explicit regressions against version 1.1.0 defects and are
labelled as such; each fails against the old behaviour and passes against the fix.
"""

import math
import unittest

from warrants_integration import (
    CBBCCategory,
    DeltaHedgeSignal,
    KnockOutStatus,
    PricingModel,
    SettlementType,
    WarrantContract,
    WarrantEngineError,
    WarrantType,
    WarrantValuation,
    WarrantsIntegrationEngine,
    entitlement_ratio_from_conversion_ratio,
    standard_normal_cdf,
    standard_normal_pdf,
)

DAYS_PER_YEAR = 365.0


def richardson_central_difference(func, x, h):
    """Fourth-order accurate first derivative of ``func`` at ``x``."""
    coarse = (func(x + h) - func(x - h)) / (2.0 * h)
    fine = (func(x + h / 2.0) - func(x - h / 2.0)) / h
    return (4.0 * fine - coarse) / 3.0


class WarrantFixtureMixin:
    """Shared term sheets. 2800.HK-style: 10 warrants per share, so R = 0.1."""

    def setUp(self):
        self.engine = WarrantsIntegrationEngine()

    def covered(self, **overrides):
        params = dict(
            warrant_id="WRT-CALL-100",
            symbol="2800.HK_C100",
            underlying_symbol="2800.HK",
            warrant_type=WarrantType.COVERED_CALL,
            strike_price=100.0,
            entitlement_ratio=0.1,
            days_to_expiry=90,
            risk_free_rate=0.03,
            implied_volatility=0.25,
            dividend_yield=0.02,
        )
        params.update(overrides)
        return WarrantContract(**params)

    def bull_cbbc(self, **overrides):
        params = dict(
            warrant_id="CBBC-BULL-85",
            symbol="2800.HK_B85",
            underlying_symbol="2800.HK",
            warrant_type=WarrantType.TURBO_BULL_CBBC,
            strike_price=85.0,
            barrier_price=90.0,           # Category R: call price above the strike
            entitlement_ratio=0.1,
            days_to_expiry=60,
            funding_rate_annual=0.045,
            cbbc_category=CBBCCategory.CATEGORY_R,
        )
        params.update(overrides)
        return WarrantContract(**params)

    def bear_cbbc(self, **overrides):
        params = dict(
            warrant_id="CBBC-BEAR-120",
            symbol="2800.HK_R120",
            underlying_symbol="2800.HK",
            warrant_type=WarrantType.TURBO_BEAR_CBBC,
            strike_price=120.0,
            barrier_price=115.0,          # Category R: call price below the strike
            entitlement_ratio=0.1,
            days_to_expiry=60,
            funding_rate_annual=0.045,
            cbbc_category=CBBCCategory.CATEGORY_R,
        )
        params.update(overrides)
        return WarrantContract(**params)


class TestBlackScholesReferenceValues(WarrantFixtureMixin, unittest.TestCase):
    """Prices against published closed-form values and put-call parity."""

    def test_matches_canonical_black_scholes_worked_example(self):
        # BS(100, 100, 1y, r=5%, sigma=20%, q=0) = 10.450583572185565 (call),
        # 5.5735260222569727 (put). Entitlement ratio 1.0 so R does not scale it.
        anchor = dict(
            strike_price=100.0,
            days_to_expiry=365,
            risk_free_rate=0.05,
            implied_volatility=0.20,
            dividend_yield=0.0,
            entitlement_ratio=1.0,
        )
        call = self.engine.price_warrant(100.0, self.covered(**anchor))
        put = self.engine.price_warrant(
            100.0, self.covered(warrant_type=WarrantType.COVERED_PUT, **anchor)
        )
        self.assertAlmostEqual(call.fair_price, 10.450583572185565, places=12)
        self.assertAlmostEqual(put.fair_price, 5.5735260222569727, places=12)

    def test_put_call_parity_holds_with_dividend_yield(self):
        # C - P = R * (S e^{-qT} - K e^{-rT}); the classic detector of a
        # mis-signed dividend or discount term.
        call = self.engine.price_warrant(105.0, self.covered())
        put = self.engine.price_warrant(
            105.0, self.covered(warrant_type=WarrantType.COVERED_PUT)
        )
        t = 90.0 / DAYS_PER_YEAR
        expected = 0.1 * (105.0 * math.exp(-0.02 * t) - 100.0 * math.exp(-0.03 * t))
        self.assertAlmostEqual(call.fair_price - put.fair_price, expected, places=12)

    def test_entitlement_ratio_scales_price_and_delta_linearly(self):
        one_to_one = self.engine.price_warrant(105.0, self.covered(entitlement_ratio=1.0))
        ten_to_one = self.engine.price_warrant(105.0, self.covered(entitlement_ratio=0.1))
        self.assertAlmostEqual(ten_to_one.fair_price, one_to_one.fair_price * 0.1, places=12)
        self.assertAlmostEqual(ten_to_one.delta, one_to_one.delta * 0.1, places=12)
        # Gearing is a ratio and must be invariant to the entitlement ratio.
        self.assertAlmostEqual(
            ten_to_one.effective_gearing, one_to_one.effective_gearing, places=10
        )

    def test_dividend_yield_reduces_call_and_lifts_put(self):
        no_div = self.engine.price_warrant(105.0, self.covered(dividend_yield=0.0))
        with_div = self.engine.price_warrant(105.0, self.covered(dividend_yield=0.05))
        self.assertLess(with_div.fair_price, no_div.fair_price)
        self.assertLess(abs(with_div.delta), abs(no_div.delta))

        put_kwargs = dict(warrant_type=WarrantType.COVERED_PUT)
        put_no_div = self.engine.price_warrant(105.0, self.covered(dividend_yield=0.0, **put_kwargs))
        put_div = self.engine.price_warrant(105.0, self.covered(dividend_yield=0.05, **put_kwargs))
        self.assertGreater(put_div.fair_price, put_no_div.fair_price)


class TestGreeksAgainstFiniteDifferences(WarrantFixtureMixin, unittest.TestCase):
    """Every closed-form Greek must reproduce a numerical derivative of the price.

    Version 1.1.0 used ``N(d1)`` where call theta requires ``N(d2)``, and had the
    rate term of put theta at the wrong sign, overstating put decay by ~60%.
    These tests fail against both.
    """

    def _price_at_spot(self, contract_kwargs):
        return lambda s: self.engine.price_warrant(s, self.covered(**contract_kwargs)).fair_price

    def _delta_at_spot(self, contract_kwargs):
        return lambda s: self.engine.price_warrant(s, self.covered(**contract_kwargs)).delta

    def _assert_greeks(self, contract_kwargs, spot):
        valuation = self.engine.price_warrant(spot, self.covered(**contract_kwargs))

        fd_delta = richardson_central_difference(self._price_at_spot(contract_kwargs), spot, 1e-3)
        self.assertAlmostEqual(valuation.delta, fd_delta, places=9)

        fd_gamma = richardson_central_difference(self._delta_at_spot(contract_kwargs), spot, 1e-3)
        self.assertAlmostEqual(valuation.gamma, fd_gamma, places=9)

        def price_at_vol(sigma):
            kwargs = dict(contract_kwargs, implied_volatility=sigma)
            return self.engine.price_warrant(spot, self.covered(**kwargs)).fair_price

        # vega is reported per one volatility *point*, hence the /100.
        fd_vega = richardson_central_difference(price_at_vol, 0.25, 1e-4) / 100.0
        self.assertAlmostEqual(valuation.vega, fd_vega, places=9)

        def price_at_days(days):
            kwargs = dict(contract_kwargs, days_to_expiry=days)
            return self.engine.price_warrant(spot, self.covered(**kwargs)).fair_price

        # theta is per calendar day: the negated derivative with respect to
        # days remaining, by a one-day central difference.
        fd_theta = -(price_at_days(91) - price_at_days(89)) / 2.0
        self.assertAlmostEqual(valuation.theta, fd_theta, places=6)

    def test_call_greeks_out_of_the_money(self):
        self._assert_greeks({}, spot=92.0)

    def test_call_greeks_at_the_money(self):
        self._assert_greeks({}, spot=100.0)

    def test_call_greeks_in_the_money(self):
        self._assert_greeks({}, spot=118.0)

    def test_put_greeks_out_of_the_money(self):
        self._assert_greeks({"warrant_type": WarrantType.COVERED_PUT}, spot=118.0)

    def test_put_greeks_at_the_money(self):
        self._assert_greeks({"warrant_type": WarrantType.COVERED_PUT}, spot=100.0)

    def test_put_greeks_in_the_money(self):
        self._assert_greeks({"warrant_type": WarrantType.COVERED_PUT}, spot=92.0)

    def test_call_greeks_with_zero_dividend_and_high_rate(self):
        # High r and deep ITM is exactly where the N(d1)-for-N(d2) substitution
        # in version 1.1.0 did the most damage to theta.
        self._assert_greeks({"dividend_yield": 0.0, "risk_free_rate": 0.10}, spot=130.0)

    def test_put_theta_is_not_the_call_theta_rate_term(self):
        """Regression: v1.1.0 subtracted the rate term for puts instead of adding it."""
        contract = self.covered(warrant_type=WarrantType.COVERED_PUT, dividend_yield=0.0)
        valuation = self.engine.price_warrant(105.0, contract)

        t = 90.0 / DAYS_PER_YEAR
        sqrt_t = math.sqrt(t)
        d1 = (math.log(105.0 / 100.0) + (0.03 + 0.5 * 0.25 ** 2) * t) / (0.25 * sqrt_t)
        d2 = d1 - 0.25 * sqrt_t
        diffusion = -(105.0 * standard_normal_pdf(d1) * 0.25) / (2.0 * sqrt_t)
        rate_term = 0.03 * 100.0 * math.exp(-0.03 * t) * standard_normal_cdf(-d2)

        correct = (diffusion + rate_term) * 0.1 / DAYS_PER_YEAR
        v110_bug = (diffusion - rate_term) * 0.1 / DAYS_PER_YEAR

        self.assertAlmostEqual(valuation.theta, correct, places=12)
        self.assertNotAlmostEqual(valuation.theta, v110_bug, places=6)


class TestGearing(WarrantFixtureMixin, unittest.TestCase):
    """HKEX: gearing = S / (warrant price x conversion ratio); effective = gearing x delta."""

    def test_effective_gearing_equals_price_elasticity(self):
        spot = 105.0
        valuation = self.engine.price_warrant(spot, self.covered())
        # Elasticity computed straight from the price curve, no Greeks involved.
        elasticity = (
            spot
            / valuation.fair_price
            * richardson_central_difference(
                lambda s: self.engine.price_warrant(s, self.covered()).fair_price, spot, 1e-3
            )
        )
        self.assertAlmostEqual(valuation.effective_gearing, elasticity, places=8)

    def test_effective_gearing_never_exceeds_simple_gearing(self):
        valuation = self.engine.price_warrant(105.0, self.covered())
        self.assertLessEqual(valuation.effective_gearing, valuation.simple_gearing)

    def test_gearing_uses_market_price_when_supplied(self):
        contract = self.covered()
        theoretical = self.engine.price_warrant(105.0, contract)
        traded = self.engine.price_warrant(105.0, contract, market_price=1.20)
        self.assertAlmostEqual(traded.gearing_basis_price, 1.20, places=12)
        self.assertAlmostEqual(traded.simple_gearing, 105.0 * 0.1 / 1.20, places=12)
        # Fair price itself is untouched by the market price.
        self.assertAlmostEqual(traded.fair_price, theoretical.fair_price, places=12)

    def test_worthless_warrant_reports_zero_gearing_not_a_floored_price(self):
        """Regression: v1.1.0 floored the price at 0.0001 and reported 100,000x gearing."""
        contract = self.covered(
            strike_price=500.0, days_to_expiry=1, implied_volatility=0.10
        )
        valuation = self.engine.price_warrant(100.0, contract)
        self.assertEqual(valuation.fair_price, 0.0)
        self.assertEqual(valuation.simple_gearing, 0.0)
        self.assertEqual(valuation.effective_gearing, 0.0)
        self.assertLess(valuation.simple_gearing, 1000.0)

    def test_deep_itm_european_put_may_show_negative_time_value(self):
        contract = self.covered(
            warrant_type=WarrantType.COVERED_PUT,
            strike_price=200.0,
            days_to_expiry=730,
            risk_free_rate=0.08,
            dividend_yield=0.0,
        )
        valuation = self.engine.price_warrant(60.0, contract)
        self.assertLess(valuation.time_value, 0.0)
        self.assertAlmostEqual(
            valuation.time_value, valuation.fair_price - valuation.intrinsic_value, places=12
        )


class TestCBBCPricing(WarrantFixtureMixin, unittest.TestCase):
    """CBBCs price as intrinsic + funding cost with delta one, not vanilla BS."""

    def test_bull_cbbc_price_is_intrinsic_plus_funding_cost(self):
        valuation = self.engine.price_warrant(105.0, self.bull_cbbc())
        expected_intrinsic = (105.0 - 85.0) * 0.1
        expected_funding = 85.0 * 0.045 * (60.0 / 365.0) * 0.1
        self.assertAlmostEqual(valuation.intrinsic_value, expected_intrinsic, places=12)
        self.assertAlmostEqual(valuation.time_value, expected_funding, places=12)
        self.assertAlmostEqual(
            valuation.fair_price, expected_intrinsic + expected_funding, places=12
        )
        self.assertIs(valuation.pricing_model, PricingModel.CBBC_INTRINSIC_PLUS_FUNDING)

    def test_bull_cbbc_is_delta_one(self):
        """Regression: v1.1.0 priced CBBCs with vanilla BS, giving delta = R*N(d1) < R."""
        valuation = self.engine.price_warrant(105.0, self.bull_cbbc())
        self.assertAlmostEqual(valuation.delta, 0.1, places=12)
        # Numerically: a 1.00 move in spot moves the CBBC by exactly R.
        moved = self.engine.price_warrant(106.0, self.bull_cbbc())
        self.assertAlmostEqual(moved.fair_price - valuation.fair_price, 0.1, places=12)

    def test_bear_cbbc_is_delta_minus_one(self):
        valuation = self.engine.price_warrant(105.0, self.bear_cbbc())
        self.assertAlmostEqual(valuation.delta, -0.1, places=12)
        expected = (120.0 - 105.0) * 0.1 + 120.0 * 0.045 * (60.0 / 365.0) * 0.1
        self.assertAlmostEqual(valuation.fair_price, expected, places=12)

    def test_cbbc_theta_is_the_daily_funding_accrual(self):
        valuation = self.engine.price_warrant(105.0, self.bull_cbbc())
        expected = -(85.0 * 0.045 * 0.1) / 365.0
        self.assertAlmostEqual(valuation.theta, expected, places=12)
        # And it reconciles with a one-day roll-down of the price.
        one_day_less = self.engine.price_warrant(105.0, self.bull_cbbc(days_to_expiry=59))
        self.assertAlmostEqual(
            one_day_less.fair_price - valuation.fair_price, expected, places=12
        )

    def test_cbbc_convention_reports_no_convexity(self):
        valuation = self.engine.price_warrant(105.0, self.bull_cbbc())
        self.assertEqual(valuation.gamma, 0.0)
        self.assertEqual(valuation.vega, 0.0)

    def test_cbbc_with_zero_funding_rate_prices_at_intrinsic(self):
        valuation = self.engine.price_warrant(105.0, self.bull_cbbc(funding_rate_annual=0.0))
        self.assertAlmostEqual(valuation.fair_price, 2.0, places=12)
        self.assertEqual(valuation.time_value, 0.0)

    def test_cbbc_effective_gearing_is_simple_gearing(self):
        valuation = self.engine.price_warrant(105.0, self.bull_cbbc())
        self.assertAlmostEqual(
            valuation.effective_gearing, valuation.simple_gearing, places=12
        )


class TestMandatoryCallEvent(WarrantFixtureMixin, unittest.TestCase):
    def test_bull_cbbc_calls_at_or_below_the_call_price(self):
        engine = self.engine
        contract = self.bull_cbbc()
        self.assertFalse(engine.is_mandatory_call_triggered(90.01, contract))
        self.assertTrue(engine.is_mandatory_call_triggered(90.0, contract))   # touch = call
        self.assertTrue(engine.is_mandatory_call_triggered(89.99, contract))

    def test_bear_cbbc_calls_at_or_above_the_call_price(self):
        contract = self.bear_cbbc()
        self.assertFalse(self.engine.is_mandatory_call_triggered(114.99, contract))
        self.assertTrue(self.engine.is_mandatory_call_triggered(115.0, contract))
        self.assertTrue(self.engine.is_mandatory_call_triggered(115.01, contract))

    def test_covered_warrant_never_triggers_a_call(self):
        self.assertFalse(self.engine.is_mandatory_call_triggered(1.0, self.covered()))

    def test_called_bull_cbbc_zeroes_delta_and_gearing(self):
        valuation = self.engine.price_warrant(88.0, self.bull_cbbc())
        self.assertIs(valuation.status, KnockOutStatus.KNOCKED_OUT)
        self.assertIs(valuation.pricing_model, PricingModel.TERMINATED)
        self.assertEqual(valuation.delta, 0.0)
        self.assertEqual(valuation.gamma, 0.0)
        self.assertEqual(valuation.effective_gearing, 0.0)

    def test_category_r_pays_residual_value_against_the_strike(self):
        # Bull Cat R: strike 85, call price 90. Called at 88 -> (88-85) * 0.1.
        valuation = self.engine.price_warrant(88.0, self.bull_cbbc())
        self.assertAlmostEqual(valuation.residual_value, 0.3, places=12)
        self.assertAlmostEqual(valuation.fair_price, 0.3, places=12)

    def test_category_n_pays_nothing(self):
        # Cat N has call price == strike, so there is no buffer to recover.
        contract = self.bull_cbbc(
            strike_price=90.0, barrier_price=90.0, cbbc_category=CBBCCategory.CATEGORY_N
        )
        valuation = self.engine.price_warrant(89.0, contract)
        self.assertIs(valuation.status, KnockOutStatus.KNOCKED_OUT)
        self.assertEqual(valuation.residual_value, 0.0)
        self.assertEqual(valuation.fair_price, 0.0)

    def test_residual_value_uses_the_mce_settlement_price_not_the_trigger(self):
        contract = self.bull_cbbc()
        # The exchange fixes a bull CBBC on the LOWEST price of the valuation
        # period, so the settled residual is at most the provisional one.
        provisional = self.engine.price_warrant(88.0, contract).residual_value
        settled = self.engine.mandatory_call_residual_value(contract, 86.5)
        self.assertAlmostEqual(settled, 0.15, places=12)
        self.assertLess(settled, provisional)

    def test_residual_value_floors_at_zero(self):
        contract = self.bear_cbbc()
        self.assertEqual(self.engine.mandatory_call_residual_value(contract, 130.0), 0.0)

    def test_bull_cbbc_barrier_below_strike_is_rejected(self):
        with self.assertRaises(WarrantEngineError):
            self.engine.price_warrant(105.0, self.bull_cbbc(strike_price=95.0, barrier_price=90.0))

    def test_bear_cbbc_barrier_above_strike_is_rejected(self):
        with self.assertRaises(WarrantEngineError):
            self.engine.price_warrant(
                105.0, self.bear_cbbc(strike_price=110.0, barrier_price=115.0)
            )

    def test_non_finite_barrier_does_not_silently_disable_the_monitor(self):
        """A NaN call price must raise, not compare False on every tick forever."""
        contract = self.bull_cbbc(barrier_price=float("nan"))
        with self.assertRaises(WarrantEngineError):
            self.engine.is_mandatory_call_triggered(88.0, contract)

    def test_residual_value_validates_the_contract_not_just_the_price(self):
        contract = self.bull_cbbc(strike_price=float("nan"))
        with self.assertRaises(WarrantEngineError):
            self.engine.mandatory_call_residual_value(contract, 88.0)
        with self.assertRaises(WarrantEngineError):
            self.engine.mandatory_call_residual_value(self.bull_cbbc(), -1.0)

    def test_market_price_is_validated_even_on_a_terminated_contract(self):
        """Validation must not depend on which pricing branch the contract takes."""
        with self.assertRaises(WarrantEngineError):
            self.engine.price_warrant(88.0, self.bull_cbbc(), market_price=-5.0)
        with self.assertRaises(WarrantEngineError):
            self.engine.price_warrant(
                105.0, self.covered(days_to_expiry=0), market_price=float("nan")
            )

    def test_cbbc_without_a_declared_category_is_rejected(self):
        with self.assertRaises(WarrantEngineError):
            self.engine.price_warrant(
                105.0, self.bull_cbbc(cbbc_category=CBBCCategory.NOT_APPLICABLE)
            )


class TestExpiry(WarrantFixtureMixin, unittest.TestCase):
    def test_expired_warrant_settles_at_intrinsic_with_no_greeks(self):
        valuation = self.engine.price_warrant(105.0, self.covered(days_to_expiry=0))
        self.assertIs(valuation.status, KnockOutStatus.EXPIRED)
        self.assertAlmostEqual(valuation.fair_price, 0.5, places=12)   # (105-100)*0.1
        self.assertEqual(valuation.delta, 0.0)
        self.assertEqual(valuation.theta, 0.0)

    def test_negative_days_to_expiry_does_not_price_as_live(self):
        valuation = self.engine.price_warrant(105.0, self.covered(days_to_expiry=-5))
        self.assertIs(valuation.status, KnockOutStatus.EXPIRED)

    def test_call_event_takes_precedence_over_expiry(self):
        contract = self.bull_cbbc(days_to_expiry=0)
        valuation = self.engine.price_warrant(88.0, contract)
        self.assertIs(valuation.status, KnockOutStatus.KNOCKED_OUT)

    def test_one_day_warrant_still_prices(self):
        valuation = self.engine.price_warrant(105.0, self.covered(days_to_expiry=1))
        self.assertIs(valuation.status, KnockOutStatus.ACTIVE)
        self.assertGreater(valuation.fair_price, 0.5)


class TestDeltaHedgeSignal(WarrantFixtureMixin, unittest.TestCase):
    """The hedge target is ``-position * delta``. v1.1.0 returned ``+position * delta``."""

    def test_long_call_warrants_are_hedged_by_selling_the_underlying(self):
        valuation = self.engine.price_warrant(105.0, self.covered())
        signal = self.engine.calculate_delta_hedge_signal(
            valuation, position_warrants=1_000_000, current_underlying_hedged_shares=0.0
        )
        self.assertAlmostEqual(
            signal.warrant_book_delta_shares, 1_000_000 * valuation.delta, places=6
        )
        self.assertAlmostEqual(
            signal.required_underlying_delta_shares, -1_000_000 * valuation.delta, places=6
        )
        self.assertLess(signal.required_underlying_delta_shares, 0.0)
        self.assertEqual(signal.action, "SELL")

    def test_issued_call_warrants_are_hedged_by_buying_the_underlying(self):
        valuation = self.engine.price_warrant(105.0, self.covered())
        signal = self.engine.calculate_delta_hedge_signal(
            valuation, position_warrants=-1_000_000, current_underlying_hedged_shares=0.0
        )
        self.assertGreater(signal.required_underlying_delta_shares, 0.0)
        self.assertEqual(signal.action, "BUY")

    def test_issued_put_warrants_are_hedged_by_selling_the_underlying(self):
        valuation = self.engine.price_warrant(
            105.0, self.covered(warrant_type=WarrantType.COVERED_PUT)
        )
        self.assertLess(valuation.delta, 0.0)
        signal = self.engine.calculate_delta_hedge_signal(
            valuation, position_warrants=-1_000_000, current_underlying_hedged_shares=0.0
        )
        self.assertLess(signal.required_underlying_delta_shares, 0.0)
        self.assertEqual(signal.action, "SELL")

    def test_hedge_signal_carries_the_real_underlying_symbol(self):
        """Regression: v1.1.0 hard-coded the literal string "UNDERLYING"."""
        valuation = self.engine.price_warrant(105.0, self.covered())
        signal = self.engine.calculate_delta_hedge_signal(valuation, 1_000, 0.0)
        self.assertEqual(signal.underlying_symbol, "2800.HK")
        self.assertEqual(valuation.underlying_symbol, "2800.HK")

    def test_already_hedged_book_holds(self):
        valuation = self.engine.price_warrant(105.0, self.covered())
        target = -1_000_000 * valuation.delta
        signal = self.engine.calculate_delta_hedge_signal(valuation, 1_000_000, target)
        self.assertEqual(signal.action, "HOLD")
        self.assertAlmostEqual(signal.net_rebalance_shares, 0.0, places=9)

    def test_rebalance_threshold_suppresses_sub_lot_orders(self):
        valuation = self.engine.price_warrant(105.0, self.covered())
        target = -1_000_000 * valuation.delta
        drifted = target + 150.0
        self.assertEqual(
            self.engine.calculate_delta_hedge_signal(
                valuation, 1_000_000, drifted, rebalance_threshold_shares=500.0
            ).action,
            "HOLD",
        )
        self.assertEqual(
            self.engine.calculate_delta_hedge_signal(
                valuation, 1_000_000, drifted, rebalance_threshold_shares=100.0
            ).action,
            "SELL",
        )

    def test_called_cbbc_unwinds_the_entire_hedge_in_one_instruction(self):
        valuation = self.engine.price_warrant(88.0, self.bull_cbbc())
        signal = self.engine.calculate_delta_hedge_signal(
            valuation, position_warrants=-1_000_000, current_underlying_hedged_shares=100_000.0
        )
        self.assertEqual(signal.warrant_book_delta_shares, 0.0)
        self.assertEqual(signal.required_underlying_delta_shares, 0.0)
        self.assertAlmostEqual(signal.net_rebalance_shares, -100_000.0, places=9)
        self.assertEqual(signal.action, "SELL")

    def test_expired_warrant_also_unwinds_the_hedge(self):
        valuation = self.engine.price_warrant(105.0, self.covered(days_to_expiry=0))
        signal = self.engine.calculate_delta_hedge_signal(valuation, -1_000_000, 50_000.0)
        self.assertEqual(signal.required_underlying_delta_shares, 0.0)
        self.assertEqual(signal.action, "SELL")

    def test_fractional_position_warrants_rejected(self):
        valuation = self.engine.price_warrant(105.0, self.covered())
        with self.assertRaises(WarrantEngineError):
            self.engine.calculate_delta_hedge_signal(valuation, 1000.5, 0.0)
        with self.assertRaises(WarrantEngineError):
            self.engine.calculate_delta_hedge_signal(valuation, True, 0.0)

    def test_non_finite_hedged_shares_rejected(self):
        valuation = self.engine.price_warrant(105.0, self.covered())
        for bad in (float("nan"), float("inf")):
            with self.assertRaises(WarrantEngineError):
                self.engine.calculate_delta_hedge_signal(valuation, 1_000, bad)

    def test_zero_threshold_still_holds_a_flat_book(self):
        """A zero threshold must not turn an exactly flat book into a 0-share SELL."""
        valuation = self.engine.price_warrant(105.0, self.covered())
        target = -1_000_000 * valuation.delta
        signal = self.engine.calculate_delta_hedge_signal(
            valuation, 1_000_000, target, rebalance_threshold_shares=0.0
        )
        self.assertEqual(signal.net_rebalance_shares, 0.0)
        self.assertEqual(signal.action, "HOLD")

    def test_zero_position_targets_a_full_unwind(self):
        valuation = self.engine.price_warrant(105.0, self.covered())
        signal = self.engine.calculate_delta_hedge_signal(valuation, 0, 25_000.0)
        self.assertEqual(signal.required_underlying_delta_shares, 0.0)
        self.assertAlmostEqual(signal.net_rebalance_shares, -25_000.0, places=9)
        self.assertEqual(signal.action, "SELL")


class TestEntitlementRatioConversion(unittest.TestCase):
    def test_exchange_conversion_ratio_is_inverted(self):
        self.assertAlmostEqual(entitlement_ratio_from_conversion_ratio(10.0), 0.1, places=12)
        self.assertAlmostEqual(entitlement_ratio_from_conversion_ratio(1.0), 1.0, places=12)
        self.assertAlmostEqual(entitlement_ratio_from_conversion_ratio(500.0), 0.002, places=12)

    def test_invalid_conversion_ratio_rejected(self):
        for bad in (0.0, -10.0, float("nan"), float("inf")):
            with self.assertRaises(WarrantEngineError):
                entitlement_ratio_from_conversion_ratio(bad)


class TestInputValidation(WarrantFixtureMixin, unittest.TestCase):
    def test_non_positive_spot_rejected(self):
        for bad in (0.0, -100.0):
            with self.assertRaises(WarrantEngineError):
                self.engine.price_warrant(bad, self.covered())

    def test_non_finite_spot_rejected(self):
        for bad in (float("nan"), float("inf")):
            with self.assertRaises(WarrantEngineError):
                self.engine.price_warrant(bad, self.covered())

    def test_non_positive_strike_rejected(self):
        with self.assertRaises(WarrantEngineError):
            self.engine.price_warrant(105.0, self.covered(strike_price=0.0))

    def test_zero_volatility_rejected_rather_than_dividing_by_zero(self):
        """v1.1.0 raised ZeroDivisionError here, which callers cannot classify."""
        with self.assertRaises(WarrantEngineError):
            self.engine.price_warrant(105.0, self.covered(implied_volatility=0.0))

    def test_non_positive_entitlement_ratio_rejected(self):
        with self.assertRaises(WarrantEngineError):
            self.engine.price_warrant(105.0, self.covered(entitlement_ratio=0.0))

    def test_negative_dividend_yield_and_funding_rate_rejected(self):
        with self.assertRaises(WarrantEngineError):
            self.engine.price_warrant(105.0, self.covered(dividend_yield=-0.01))
        with self.assertRaises(WarrantEngineError):
            self.engine.price_warrant(105.0, self.bull_cbbc(funding_rate_annual=-0.01))

    def test_nan_volatility_rejected(self):
        with self.assertRaises(WarrantEngineError):
            self.engine.price_warrant(105.0, self.covered(implied_volatility=float("nan")))

    def test_non_integer_days_to_expiry_rejected(self):
        with self.assertRaises(WarrantEngineError):
            self.engine.price_warrant(105.0, self.covered(days_to_expiry=90.5))

    def test_non_positive_market_price_rejected(self):
        with self.assertRaises(WarrantEngineError):
            self.engine.price_warrant(105.0, self.covered(), market_price=0.0)

    def test_autocallable_note_raises_instead_of_pricing_as_a_put(self):
        """Regression: v1.1.0 fell through to the put branch and returned a price."""
        contract = self.covered(warrant_type=WarrantType.AUTOCALLABLE_NOTE)
        with self.assertRaises(WarrantEngineError) as ctx:
            self.engine.price_warrant(105.0, contract)
        self.assertIn("AUTOCALLABLE_NOTE", str(ctx.exception))

    def test_wrong_contract_type_rejected(self):
        with self.assertRaises(WarrantEngineError):
            self.engine.price_warrant(105.0, {"strike_price": 100.0})

    def test_wrong_valuation_type_rejected_by_hedge_signal(self):
        with self.assertRaises(WarrantEngineError):
            self.engine.calculate_delta_hedge_signal({"delta": 0.1}, 1_000, 0.0)


class TestStandardNormalHelpers(unittest.TestCase):
    def test_cdf_reference_points(self):
        self.assertAlmostEqual(standard_normal_cdf(0.0), 0.5, places=12)
        self.assertAlmostEqual(standard_normal_cdf(1.959963984540054), 0.975, places=12)
        self.assertAlmostEqual(standard_normal_cdf(-1.959963984540054), 0.025, places=12)

    def test_pdf_reference_points(self):
        self.assertAlmostEqual(standard_normal_pdf(0.0), 0.3989422804014327, places=15)
        self.assertAlmostEqual(standard_normal_pdf(1.0), 0.24197072451914337, places=15)


class TestDataclassSurface(WarrantFixtureMixin, unittest.TestCase):
    def test_valuation_and_signal_are_the_documented_types(self):
        valuation = self.engine.price_warrant(105.0, self.covered())
        self.assertIsInstance(valuation, WarrantValuation)
        signal = self.engine.calculate_delta_hedge_signal(valuation, 1_000, 0.0)
        self.assertIsInstance(signal, DeltaHedgeSignal)
        self.assertIs(valuation.status, KnockOutStatus.ACTIVE)

    def test_settlement_type_default_is_cash(self):
        self.assertIs(self.covered().settlement_type, SettlementType.CASH_SETTLED)

    def test_engine_does_not_round_its_outputs(self):
        # v1.1.0 rounded gamma to 6dp, which on a high-priced underlying is a
        # single significant figure.
        valuation = self.engine.price_warrant(38_000.0, self.covered(strike_price=38_000.0))
        self.assertNotEqual(valuation.gamma, round(valuation.gamma, 6))


if __name__ == "__main__":
    unittest.main()
