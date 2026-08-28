"""Tests for the single stock futures no-arbitrage band engine.

Expected values are derived by hand from the cost-of-carry definitions rather than by
re-running the module's own arithmetic. The reference case throughout is
``S = 2500``, ``r = 6%``, ``T = 30/365 = 0.0821917808...`` years:

    2500 * exp(0.06 * 30/365)           = 2512.359216856823  -> ceiling
    2500 * exp((0.06 - 0.005) * 30/365) = 2511.3269525899577 -> floor at a 0.5% borrow
    2500 * exp((0.06 - 0.20) * 30/365)  = 2471.397753179941  -> floor at a 20% borrow
      20 * exp(-0.06 * 15/365)          =   19.950745680965  -> PV of a 20.00 dividend
                                                                at day 15
"""

import math
import unittest

from ssf_handling import (
    DAY_COUNT_ACT_360,
    SEBI_EXTRAORDINARY_DIVIDEND_THRESHOLD_PCT,
    US_REG_T_INITIAL_MARGIN_PCT,
    US_SECURITY_FUTURES_MIN_MARGIN_PCT,
    DividendEvent,
    ExDividendAdjustmentResult,
    SSFArbitrageSignal,
    SSFConfigError,
    SSFContractSpec,
    SSFFairValueResult,
    SSFInputError,
    SSFSettlementType,
    SingleStockFuturesEngine,
)

# Hand-derived reference values (see module docstring).
CEILING_NO_DIV = 2512.359216856823
FLOOR_NO_DIV = 2511.3269525899577
HTB_FLOOR = 2471.397753179941
DIV_PV_20_AT_DAY_15 = 19.950745680965042


def make_spec(**overrides) -> SSFContractSpec:
    """NSE Reliance-style contract, physically settled, with explicit margins in tests."""
    params = dict(
        symbol="RELIANCE.NS-FUT",
        underlying_spot_symbol="RELIANCE.NS",
        exchange="NSE",
        lot_size=250,
        days_to_expiry=30,
        settlement_type=SSFSettlementType.PHYSICAL_DELIVERY,
        risk_free_rate_annual=0.06,
        currency="INR",
    )
    params.update(overrides)
    return SSFContractSpec(**params)


class TestNoArbitrageBand(unittest.TestCase):
    """The band, not a single fair value, is what the signals are measured against."""

    def setUp(self):
        self.engine = SingleStockFuturesEngine(arbitrage_cost_threshold_pct=0.3)

    def _value(self, spec, spot, market):
        return self.engine.compute_fair_value_and_arbitrage(
            spec=spec,
            spot_price=spot,
            market_ssf_price=market,
            ssf_margin_pct=0.15,
            spot_margin_pct=0.50,
        )

    def test_band_collapses_to_the_textbook_forward_with_no_borrow_cost(self):
        # With zero borrow fee and zero lending income both edges must equal
        # S * exp(rT) exactly -- the classic (S - PV(D)) * e^{rT} forward.
        spec = make_spec(short_borrow_rate_annual=0.0, lending_income_rate_annual=0.0)
        res = self._value(spec, 2500.0, 2512.36)
        self.assertAlmostEqual(res.no_arbitrage_upper_bound, CEILING_NO_DIV, places=4)
        self.assertAlmostEqual(res.no_arbitrage_lower_bound, CEILING_NO_DIV, places=4)
        self.assertAlmostEqual(res.theoretical_fair_value, CEILING_NO_DIV, places=4)

    def test_borrow_fee_widens_the_band_downwards_only(self):
        # The 0.5% borrow fee is paid by the short seller, so it may move the FLOOR and
        # must not move the CEILING. v1.0.0 applied it to both.
        spec = make_spec(short_borrow_rate_annual=0.005)
        res = self._value(spec, 2500.0, 2512.0)
        self.assertAlmostEqual(res.no_arbitrage_upper_bound, CEILING_NO_DIV, places=4)
        self.assertAlmostEqual(res.no_arbitrage_lower_bound, FLOOR_NO_DIV, places=4)
        self.assertLess(res.no_arbitrage_lower_bound, res.no_arbitrage_upper_bound)

    def test_contracted_lending_income_lowers_the_ceiling(self):
        # A holder who has actually contracted to lend at 0.5% can defend a lower
        # ceiling, so ceiling and floor coincide when lending income equals the fee.
        spec = make_spec(short_borrow_rate_annual=0.005, lending_income_rate_annual=0.005)
        res = self._value(spec, 2500.0, 2511.0)
        self.assertAlmostEqual(res.no_arbitrage_upper_bound, FLOOR_NO_DIV, places=4)
        self.assertAlmostEqual(res.no_arbitrage_lower_bound, FLOOR_NO_DIV, places=4)

    def test_hard_to_borrow_discount_is_not_an_arbitrage_regression(self):
        # REGRESSION against v1.0.0. With a 20% borrow fee, v1.0.0's single fair value
        # was S*exp((r-0.20)T) = 2471.40, so a market price of 2495.00 read as +0.95%
        # rich and fired CASH_AND_CARRY. 2495.00 is inside the band [2471.40, 2512.36]:
        # the discount is exactly what a 20% borrow fee justifies, not free money.
        spec = make_spec(short_borrow_rate_annual=0.20)
        res = self._value(spec, 2500.0, 2495.0)
        self.assertAlmostEqual(res.no_arbitrage_lower_bound, HTB_FLOOR, places=4)
        self.assertAlmostEqual(res.no_arbitrage_upper_bound, CEILING_NO_DIV, places=4)
        self.assertEqual(res.arbitrage_signal, SSFArbitrageSignal.NEUTRAL)
        self.assertEqual(res.gross_edge_pct, 0.0)
        # The v1.0.0 mispricing figure is still visible for continuity, and is still
        # large -- which is why the band, not this number, drives the signal.
        self.assertLess(res.mispricing_pct, 0.0)


class TestArbitrageSignals(unittest.TestCase):
    def setUp(self):
        self.engine = SingleStockFuturesEngine(arbitrage_cost_threshold_pct=0.3)
        self.spec = make_spec(short_borrow_rate_annual=0.005)

    def _value(self, market, spot=2500.0, spec=None):
        return self.engine.compute_fair_value_and_arbitrage(
            spec=spec or self.spec,
            spot_price=spot,
            market_ssf_price=market,
            ssf_margin_pct=0.15,
            spot_margin_pct=0.50,
        )

    def test_cash_and_carry_above_the_widened_ceiling(self):
        # Ceiling 2512.3592 * 1.003 = 2519.8962942. Take a clearly rich price.
        res = self._value(2530.0)
        self.assertEqual(res.arbitrage_signal, SSFArbitrageSignal.CASH_AND_CARRY)
        # Gross edge measured against the CEILING: 2530/2512.3592168568 - 1 = 0.70217%.
        self.assertAlmostEqual(res.gross_edge_pct, 0.7022, places=3)

    def test_reverse_cash_and_carry_below_the_widened_floor(self):
        # Floor 2511.3269526 * 0.997 = 2503.7929717. 2495 is below it.
        res = self._value(2495.0)
        self.assertEqual(res.arbitrage_signal, SSFArbitrageSignal.REVERSE_CASH_AND_CARRY)
        # 2495/2511.3269525900 - 1 = -0.65014%.
        self.assertAlmostEqual(res.gross_edge_pct, -0.6501, places=3)
        self.assertIn("REVERSE LEG REQUIRES A LOCATED BORROW", res.audit_notes)

    def test_exact_trigger_is_inclusive(self):
        trigger = CEILING_NO_DIV * 1.003  # 2519.89629450739
        self.assertEqual(
            self._value(trigger).arbitrage_signal, SSFArbitrageSignal.CASH_AND_CARRY
        )

    def test_threshold_compared_on_unrounded_values(self):
        # REGRESSION. A gross edge of 0.2996% rounds to 0.30% at two decimal places.
        # v1.0.0 rounded the mispricing percentage before comparing it to the threshold,
        # so an edge that does not cover costs fired a signal anyway.
        just_under = CEILING_NO_DIV * 1.002996
        res = self._value(just_under)
        self.assertEqual(res.arbitrage_signal, SSFArbitrageSignal.NEUTRAL)
        just_over = CEILING_NO_DIV * 1.003001
        self.assertEqual(
            self._value(just_over).arbitrage_signal, SSFArbitrageSignal.CASH_AND_CARRY
        )

    def test_zero_threshold_signals_on_any_departure_from_the_band(self):
        engine = SingleStockFuturesEngine(arbitrage_cost_threshold_pct=0.0)
        res = engine.compute_fair_value_and_arbitrage(
            spec=self.spec,
            spot_price=2500.0,
            market_ssf_price=CEILING_NO_DIV + 0.01,
            ssf_margin_pct=0.15,
            spot_margin_pct=0.50,
        )
        self.assertEqual(res.arbitrage_signal, SSFArbitrageSignal.CASH_AND_CARRY)

    def test_negative_threshold_rejected(self):
        with self.assertRaises(SSFConfigError):
            SingleStockFuturesEngine(arbitrage_cost_threshold_pct=-0.1)


class TestDividends(unittest.TestCase):
    def setUp(self):
        self.engine = SingleStockFuturesEngine(arbitrage_cost_threshold_pct=0.3)
        self.spec = make_spec(short_borrow_rate_annual=0.005)

    def _value(self, dividends, market=2492.0):
        return self.engine.compute_fair_value_and_arbitrage(
            spec=self.spec,
            spot_price=2500.0,
            market_ssf_price=market,
            dividends=dividends,
            ssf_margin_pct=0.15,
            spot_margin_pct=0.50,
        )

    def test_dividend_present_value_and_resulting_band(self):
        # 20.00 at day 15 discounts to 20 * exp(-0.06 * 15/365) = 19.950745681.
        # base = 2480.049254319 -> ceiling 2480.049254319 * 1.004943686742729
        #                                = 2492.3098409389.
        res = self._value([DividendEvent(ex_date_days=15, amount_per_share=20.0)])
        self.assertAlmostEqual(res.dividend_pv, DIV_PV_20_AT_DAY_15, places=4)
        self.assertAlmostEqual(res.no_arbitrage_upper_bound, 2492.3098409389, places=4)
        self.assertAlmostEqual(res.no_arbitrage_lower_bound, 2491.2858144488, places=4)

    def test_dividends_lower_the_whole_band(self):
        without = self._value(None)
        with_div = self._value([DividendEvent(ex_date_days=15, amount_per_share=20.0)])
        self.assertLess(with_div.no_arbitrage_upper_bound, without.no_arbitrage_upper_bound)
        self.assertLess(with_div.no_arbitrage_lower_bound, without.no_arbitrage_lower_bound)

    def test_dividend_after_expiry_is_excluded_and_counted(self):
        res = self._value([DividendEvent(ex_date_days=45, amount_per_share=20.0)])
        self.assertEqual(res.dividend_pv, 0.0)
        self.assertEqual(res.excluded_dividends, 1)
        self.assertAlmostEqual(res.no_arbitrage_upper_bound, CEILING_NO_DIV, places=4)

    def test_dividend_on_expiry_day_is_included(self):
        # Boundary: ex_date_days == days_to_expiry is inside the window.
        res = self._value([DividendEvent(ex_date_days=30, amount_per_share=20.0)])
        self.assertEqual(res.excluded_dividends, 0)
        self.assertAlmostEqual(res.dividend_pv, 20.0 * math.exp(-0.06 * 30 / 365), places=4)

    def test_dividend_larger_than_the_share_price_is_rejected(self):
        with self.assertRaises(SSFInputError):
            self._value([DividendEvent(ex_date_days=15, amount_per_share=3000.0)])

    def test_non_finite_and_negative_dividends_rejected(self):
        for bad in (float("nan"), float("inf"), -5.0):
            with self.subTest(amount=bad):
                with self.assertRaises(SSFInputError):
                    self._value([DividendEvent(ex_date_days=15, amount_per_share=bad)])

    def test_non_integer_ex_date_rejected(self):
        with self.assertRaises(SSFInputError):
            self._value([DividendEvent(ex_date_days=15.5, amount_per_share=20.0)])


class TestInputValidation(unittest.TestCase):
    """Every one of these produced a confident, wrong result in v1.0.0."""

    def setUp(self):
        self.engine = SingleStockFuturesEngine()
        self.spec = make_spec()

    def _value(self, **kwargs):
        params = dict(
            spec=self.spec,
            spot_price=2500.0,
            market_ssf_price=2530.0,
            ssf_margin_pct=0.15,
            spot_margin_pct=0.50,
        )
        params.update(kwargs)
        return self.engine.compute_fair_value_and_arbitrage(**params)

    def test_nan_spot_rejected(self):
        # REGRESSION. v1.0.0 ran max(0.01, nan) -> 0.01, producing a fair value of 0.01
        # and a confident CASH_AND_CARRY on a 2530 market price.
        with self.assertRaises(SSFInputError):
            self._value(spot_price=float("nan"))

    def test_infinite_and_non_positive_prices_rejected(self):
        for bad in (float("inf"), -float("inf"), 0.0, -100.0):
            with self.subTest(price=bad):
                with self.assertRaises(SSFInputError):
                    self._value(spot_price=bad)
                with self.assertRaises(SSFInputError):
                    self._value(market_ssf_price=bad)

    def test_boolean_and_string_prices_rejected(self):
        for bad in (True, "2500", None):
            with self.subTest(price=bad):
                with self.assertRaises(SSFInputError):
                    self._value(spot_price=bad)

    def test_percentage_rate_unit_error_rejected(self):
        # Passing 6 for 6% instead of 0.06 inflates the forward by e^(6T) ~ 1.64x.
        with self.assertRaises(SSFInputError):
            self._value(spec=make_spec(risk_free_rate_annual=6.0))

    def test_negative_borrow_or_lending_rate_rejected(self):
        with self.assertRaises(SSFInputError):
            self._value(spec=make_spec(short_borrow_rate_annual=-0.01))
        with self.assertRaises(SSFInputError):
            self._value(spec=make_spec(lending_income_rate_annual=-0.01))

    def test_lending_income_above_borrow_fee_rejected(self):
        # Would invert the band, making every price both too rich and too cheap.
        with self.assertRaises(SSFInputError):
            self._value(
                spec=make_spec(
                    short_borrow_rate_annual=0.005, lending_income_rate_annual=0.05
                )
            )

    def test_invalid_lot_size_and_expiry_rejected(self):
        for bad_lot in (0, -250, 250.0, True):
            with self.subTest(lot=bad_lot):
                with self.assertRaises(SSFInputError):
                    self._value(spec=make_spec(lot_size=bad_lot))
        for bad_days in (-1, 30.0, 4000):
            with self.subTest(days=bad_days):
                with self.assertRaises(SSFInputError):
                    self._value(spec=make_spec(days_to_expiry=bad_days))

    def test_settlement_type_must_be_the_enum(self):
        with self.assertRaises(SSFInputError):
            self._value(spec=make_spec(settlement_type="CASH_SETTLED"))

    def test_unsupported_day_count_rejected(self):
        with self.assertRaises(SSFInputError):
            self._value(spec=make_spec(day_count_basis=252.0))

    def test_expiry_day_collapses_the_band_to_spot(self):
        res = self._value(spec=make_spec(days_to_expiry=0), market_ssf_price=2500.0)
        self.assertAlmostEqual(res.no_arbitrage_upper_bound, 2500.0, places=6)
        self.assertAlmostEqual(res.no_arbitrage_lower_bound, 2500.0, places=6)
        self.assertEqual(res.arbitrage_signal, SSFArbitrageSignal.NEUTRAL)

    def test_act_360_shortens_the_year(self):
        res = self._value(spec=make_spec(day_count_basis=DAY_COUNT_ACT_360))
        self.assertAlmostEqual(
            res.no_arbitrage_upper_bound, 2500.0 * math.exp(0.06 * 30 / 360), places=4
        )


class TestMarginAndLeverage(unittest.TestCase):
    def setUp(self):
        self.engine = SingleStockFuturesEngine()

    def test_scenario_margined_venue_refuses_to_invent_percentages(self):
        # REGRESSION. v1.0.0 defaulted every venue to 15%/50% and reported a 3.33x
        # leverage figure for NSE, whose stock futures are margined SPAN + 3.5% ELM.
        with self.assertRaises(SSFConfigError):
            self.engine.compute_fair_value_and_arbitrage(
                spec=make_spec(exchange="NSE"),
                spot_price=2500.0,
                market_ssf_price=2530.0,
            )
        with self.assertRaises(SSFConfigError):
            self.engine.compute_fair_value_and_arbitrage(
                spec=make_spec(exchange="EUREX"),
                spot_price=2500.0,
                market_ssf_price=2530.0,
            )

    def test_us_statutory_defaults_apply_only_to_cme(self):
        spec = make_spec(
            exchange="CME",
            symbol="AAPLF",
            underlying_spot_symbol="AAPL",
            settlement_type=SSFSettlementType.CASH_SETTLED,
            lot_size=100,
            currency="USD",
        )
        res = self.engine.compute_fair_value_and_arbitrage(
            spec=spec, spot_price=200.0, market_ssf_price=201.0
        )
        notional = 100 * 200.0
        self.assertAlmostEqual(
            res.initial_margin_ssf, notional * US_SECURITY_FUTURES_MIN_MARGIN_PCT, places=2
        )
        self.assertAlmostEqual(
            res.initial_margin_spot, notional * US_REG_T_INITIAL_MARGIN_PCT, places=2
        )
        # 50% / 15% = 3.3333x
        self.assertAlmostEqual(res.leverage_multiplier, 10.0 / 3.0, places=3)
        self.assertIn("41.45", res.margin_basis)
        self.assertFalse(res.physical_delivery_at_expiry)

    def test_caller_supplied_percentages_are_used_verbatim(self):
        res = self.engine.compute_fair_value_and_arbitrage(
            spec=make_spec(),
            spot_price=2500.0,
            market_ssf_price=2530.0,
            ssf_margin_pct=0.20,
            spot_margin_pct=0.60,
        )
        self.assertAlmostEqual(res.initial_margin_ssf, 250 * 2500.0 * 0.20, places=2)
        self.assertAlmostEqual(res.leverage_multiplier, 3.0, places=6)
        self.assertIn("caller-supplied", res.margin_basis)

    def test_out_of_range_margin_percentages_rejected(self):
        for bad in (0.0, -0.1, 1.5, float("nan")):
            with self.subTest(pct=bad):
                with self.assertRaises((SSFConfigError, SSFInputError)):
                    self.engine.compute_fair_value_and_arbitrage(
                        spec=make_spec(),
                        spot_price=2500.0,
                        market_ssf_price=2530.0,
                        ssf_margin_pct=bad,
                        spot_margin_pct=0.50,
                    )

    def test_physical_delivery_is_reported_not_just_stored(self):
        res = self.engine.compute_fair_value_and_arbitrage(
            spec=make_spec(settlement_type=SSFSettlementType.PHYSICAL_DELIVERY),
            spot_price=2500.0,
            market_ssf_price=2530.0,
            ssf_margin_pct=0.15,
            spot_margin_pct=0.50,
        )
        self.assertTrue(res.physical_delivery_at_expiry)
        self.assertIn("PHYSICAL DELIVERY", res.audit_notes)


class TestExDividendAdjustment(unittest.TestCase):
    """SEBI/NSE gate the adjustment on a 2%-of-market-value test."""

    def setUp(self):
        self.engine = SingleStockFuturesEngine()

    def test_ordinary_dividend_leaves_the_contract_unadjusted(self):
        # REGRESSION. 20 on a 2500 stock is 0.8%: an ordinary dividend, which NSE does
        # not adjust for. v1.0.0 returned 2480.0 -- a base price the exchange never set.
        res = self.engine.calculate_ex_dividend_price_adjustment(
            previous_settlement_price=2500.0,
            dividend_amount=20.0,
            underlying_market_price=2500.0,
        )
        self.assertIsInstance(res, ExDividendAdjustmentResult)
        self.assertFalse(res.is_adjusted)
        self.assertEqual(res.adjusted_base_price, 2500.0)
        self.assertAlmostEqual(res.dividend_pct_of_market_price, 0.8, places=6)
        self.assertIn("Ordinary dividend", res.rationale)

    def test_extraordinary_dividend_adjusts_the_base_price(self):
        # 60 on 2500 is 2.4% >= 2%.
        res = self.engine.calculate_ex_dividend_price_adjustment(
            previous_settlement_price=2500.0,
            dividend_amount=60.0,
            underlying_market_price=2500.0,
        )
        self.assertTrue(res.is_adjusted)
        self.assertEqual(res.adjusted_base_price, 2440.0)
        self.assertAlmostEqual(res.dividend_pct_of_market_price, 2.4, places=6)

    def test_threshold_is_inclusive_at_exactly_two_percent(self):
        # 50 on 2500 is exactly 2.0%. SEBI's wording is "at and above".
        res = self.engine.calculate_ex_dividend_price_adjustment(
            previous_settlement_price=2500.0,
            dividend_amount=50.0,
            underlying_market_price=2500.0,
        )
        self.assertTrue(res.is_adjusted)
        self.assertEqual(res.adjusted_base_price, 2450.0)
        self.assertEqual(
            SEBI_EXTRAORDINARY_DIVIDEND_THRESHOLD_PCT, res.dividend_pct_of_market_price
        )

    def test_adjustment_applies_to_the_futures_settlement_price_not_the_spot(self):
        # SEBI: the reference rate is the contract's own daily MTM settlement price.
        # A futures settlement of 2515 against a 2500 spot adjusts from 2515.
        res = self.engine.calculate_ex_dividend_price_adjustment(
            previous_settlement_price=2515.0,
            dividend_amount=60.0,
            underlying_market_price=2500.0,
        )
        self.assertEqual(res.adjusted_base_price, 2455.0)

    def test_missing_market_price_is_refused_not_assumed_extraordinary(self):
        with self.assertRaises(SSFInputError):
            self.engine.calculate_ex_dividend_price_adjustment(
                previous_settlement_price=2500.0, dividend_amount=60.0
            )

    def test_dividend_exceeding_the_settlement_price_rejected(self):
        with self.assertRaises(SSFInputError):
            self.engine.calculate_ex_dividend_price_adjustment(
                previous_settlement_price=100.0,
                dividend_amount=150.0,
                underlying_market_price=100.0,
            )

    def test_non_finite_inputs_rejected(self):
        for bad in (float("nan"), float("inf"), -1.0):
            with self.subTest(dividend=bad):
                with self.assertRaises(SSFInputError):
                    self.engine.calculate_ex_dividend_price_adjustment(
                        previous_settlement_price=2500.0,
                        dividend_amount=bad,
                        underlying_market_price=2500.0,
                    )

    def test_custom_threshold_is_honoured_for_other_jurisdictions(self):
        # The 2% gate is Indian. A venue with a different rule passes its own.
        res = self.engine.calculate_ex_dividend_price_adjustment(
            previous_settlement_price=2500.0,
            dividend_amount=20.0,
            underlying_market_price=2500.0,
            extraordinary_threshold_pct=0.5,
        )
        self.assertTrue(res.is_adjusted)
        self.assertEqual(res.adjusted_base_price, 2480.0)


class TestResultShape(unittest.TestCase):
    def test_result_records_its_pricing_model_and_currency_context(self):
        engine = SingleStockFuturesEngine()
        res = engine.compute_fair_value_and_arbitrage(
            spec=make_spec(),
            spot_price=2500.0,
            market_ssf_price=2530.0,
            ssf_margin_pct=0.15,
            spot_margin_pct=0.50,
        )
        self.assertIsInstance(res, SSFFairValueResult)
        self.assertEqual(res.pricing_model, "COST_OF_CARRY_BORROW_BAND")
        self.assertEqual(res.exchange, "NSE")
        self.assertIn("INR", res.audit_notes)


if __name__ == "__main__":
    unittest.main()
