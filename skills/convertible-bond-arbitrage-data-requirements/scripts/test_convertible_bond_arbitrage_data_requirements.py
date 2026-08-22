import math
import unittest

from convertible_bond_arbitrage_data_requirements import (
    CarryBreakdown,
    ConvertibleBondArbitrageEngine,
    ConvertibleBondData,
    EquityMarketData,
    ScreenThresholds,
)

MODULE_LOGGER = "convertible_bond_arbitrage_data_requirements"


def _bond(**overrides):
    base = dict(
        bond_id="CB_1001",
        symbol="XYZ_CB",
        clean_price=99.0,          # -> 990.00 per 1,000 par
        par_value=1000.0,
        conversion_ratio=20.0,
        coupon_rate_annual=0.04,
        years_to_maturity=3.0,
        accrued_interest=10.0,     # -> full price 1,000.00
        credit_spread_bps=300.0,
        coupon_frequency=2,
    )
    base.update(overrides)
    return ConvertibleBondData(**base)


def _equity(**overrides):
    base = dict(
        symbol="XYZ",
        stock_price=45.0,          # -> parity 900.00
        annual_borrow_fee_pct=0.01,
        historical_volatility=0.35,
        annual_dividend_yield=0.02,
    )
    base.update(overrides)
    return EquityMarketData(**base)


class TestParityAndPremium(unittest.TestCase):
    def setUp(self):
        self.engine = ConvertibleBondArbitrageEngine()

    def test_parity_is_conversion_ratio_times_spot(self):
        self.assertEqual(self.engine.calculate_parity(20.0, 45.0), 900.0)

    def test_conversion_premium_matches_market_convention(self):
        # Independent derivation via the market conversion price convention:
        # market conversion price = 990 / 20 = 49.50; premium per share = 49.50 - 45.00
        # = 4.50; premium ratio = 4.50 / 45.00 = 10.0%.
        self.assertAlmostEqual(
            self.engine.calculate_conversion_premium_pct(990.0, 900.0), 10.0, places=10
        )

    def test_premium_basis_clean_vs_full_differ_by_accrued(self):
        bond = _bond()
        clean_engine = ConvertibleBondArbitrageEngine(premium_basis="clean")
        full_engine = ConvertibleBondArbitrageEngine(premium_basis="full")
        self.assertEqual(clean_engine.cb_market_price(bond), 990.0)
        self.assertEqual(full_engine.cb_market_price(bond), 1000.0)
        # 990/900 - 1 = 10.0% clean; 1000/900 - 1 = 11.111...% full.
        self.assertAlmostEqual(
            clean_engine.calculate_conversion_premium_pct(990.0, 900.0), 10.0, places=10
        )
        self.assertAlmostEqual(
            full_engine.calculate_conversion_premium_pct(1000.0, 900.0), 100.0 / 9.0, places=10
        )

    def test_non_finite_and_non_positive_inputs_are_rejected(self):
        for ratio, spot in ((0.0, 45.0), (-1.0, 45.0), (20.0, 0.0), (float("nan"), 45.0),
                            (20.0, float("inf"))):
            with self.assertRaises(ValueError):
                self.engine.calculate_parity(ratio, spot)
        with self.assertRaises(ValueError):
            self.engine.calculate_conversion_premium_pct(990.0, 0.0)
        with self.assertRaises(ValueError):
            self.engine.calculate_conversion_premium_pct(float("nan"), 900.0)


class TestDeltaHedgeSizing(unittest.TestCase):
    def setUp(self):
        self.engine = ConvertibleBondArbitrageEngine()

    def test_hedge_quantity(self):
        # 100 bonds * 20.0 shares/bond * 0.60 = 1,200 shares.
        self.assertEqual(
            self.engine.calculate_delta_hedge_quantity(100, 20.0, 0.60), 1200
        )

    def test_shares_per_bond_delta_is_rejected(self):
        # A desk quoting delta as shares-per-bond would pass 12.0 (= 0.60 * 20) here;
        # accepting it would short 24,000 shares instead of 1,200.
        with self.assertRaises(ValueError) as ctx:
            self.engine.calculate_delta_hedge_quantity(100, 20.0, 12.0)
        self.assertIn("per-share delta", str(ctx.exception))

    def test_half_share_rounds_half_up_deterministically(self):
        # 1 bond * 5.0 * 0.5 = 2.5 -> 3 (half-up), not 2 (banker's rounding).
        self.assertEqual(self.engine.calculate_delta_hedge_quantity(1, 5.0, 0.5), 3)

    def test_lot_rounding_and_residual_exposure(self):
        # 7 bonds * 20 * 0.62 = 86.8 shares -> 100-share lots -> 100 shares short.
        qty = self.engine.calculate_delta_hedge_quantity(7, 20.0, 0.62, lot_size=100)
        self.assertEqual(qty, 100)
        residual = self.engine.residual_delta_shares(7, 20.0, 0.62, qty)
        self.assertAlmostEqual(residual, -13.2, places=9)

    def test_zero_delta_and_zero_quantity_are_valid(self):
        self.assertEqual(self.engine.calculate_delta_hedge_quantity(100, 20.0, 0.0), 0)
        self.assertEqual(self.engine.calculate_delta_hedge_quantity(0, 20.0, 0.6), 0)

    def test_invalid_sizing_inputs(self):
        with self.assertRaises(ValueError):
            self.engine.calculate_delta_hedge_quantity(-1, 20.0, 0.6)
        with self.assertRaises(ValueError):
            self.engine.calculate_delta_hedge_quantity(100, 20.0, -0.1)
        with self.assertRaises(ValueError):
            self.engine.calculate_delta_hedge_quantity(100, 20.0, float("nan"))
        with self.assertRaises(ValueError):
            self.engine.calculate_delta_hedge_quantity(100, 20.0, 0.6, lot_size=0)


class TestBondFloor(unittest.TestCase):
    def setUp(self):
        self.engine = ConvertibleBondArbitrageEngine(risk_free_rate=0.04)

    def test_bond_floor_matches_closed_form_annuity(self):
        # y = 4% + 300bp = 7%, semi-annual: 6 periods at 3.5%, coupon 20 per period.
        # PV = 20 * (1 - 1.035^-6)/0.035 + 1000 * 1.035^-6
        periods, rate, coupon = 6, 0.035, 20.0
        expected = coupon * (1 - (1 + rate) ** -periods) / rate + 1000.0 * (1 + rate) ** -periods
        self.assertAlmostEqual(self.engine.calculate_bond_floor(_bond()), expected, places=8)
        self.assertAlmostEqual(expected, 920.07, places=2)

    def test_zero_coupon_floor_is_discounted_principal(self):
        bond = _bond(coupon_rate_annual=0.0, years_to_maturity=5.0, coupon_frequency=1,
                     credit_spread_bps=100.0)
        self.assertAlmostEqual(
            self.engine.calculate_bond_floor(bond), 1000.0 / (1.05 ** 5), places=8
        )

    def test_stub_period_is_priced_at_actual_remaining_time(self):
        # 1.25y at annual frequency: coupons at t = 1.25 and t = 0.25.
        bond = _bond(years_to_maturity=1.25, coupon_frequency=1, coupon_rate_annual=0.04,
                     credit_spread_bps=100.0)
        y = 1.05
        expected = 40.0 / y ** 1.25 + 40.0 / y ** 0.25 + 1000.0 / y ** 1.25
        self.assertAlmostEqual(self.engine.calculate_bond_floor(bond), expected, places=8)

    def test_wider_spread_lowers_the_floor(self):
        tight = self.engine.calculate_bond_floor(_bond(credit_spread_bps=100.0))
        wide = self.engine.calculate_bond_floor(_bond(credit_spread_bps=900.0))
        self.assertLess(wide, tight)

    def test_missing_credit_spread_raises_rather_than_defaulting_to_zero(self):
        with self.assertRaises(ValueError):
            self.engine.calculate_bond_floor(_bond(credit_spread_bps=None))


class TestCarry(unittest.TestCase):
    def setUp(self):
        self.engine = ConvertibleBondArbitrageEngine(
            risk_free_rate=0.04, repo_financing_rate=0.045
        )

    def test_carry_components_are_independently_derivable(self):
        carry = self.engine.calculate_carry(_bond(), _equity(), delta=0.60)
        # Short leg MV = 0.60 * (20 * 45) = 540.00 per bond.
        self.assertAlmostEqual(carry.short_leg_market_value, 540.0, places=9)
        self.assertAlmostEqual(carry.coupon_income, 40.0, places=9)          # 4% * 1,000
        self.assertAlmostEqual(carry.short_proceeds_interest, 21.6, places=9)  # 4% * 540
        self.assertAlmostEqual(carry.bond_financing_cost, 45.0, places=9)    # 4.5% * 1,000 full
        self.assertAlmostEqual(carry.stock_borrow_cost, 5.4, places=9)       # 1% * 540
        self.assertAlmostEqual(carry.dividends_in_lieu_cost, 10.8, places=9)  # 2% * 540
        # 40 + 21.6 - 45 - 5.4 - 10.8 = 0.40 per bond on 1,000 of capital = 4bp.
        self.assertAlmostEqual(carry.net_carry_currency, 0.40, places=9)
        self.assertAlmostEqual(carry.net_carry_rate, 0.0004, places=12)

    def test_hedge_leg_costs_scale_with_delta_not_bond_notional(self):
        # Regression guard: the pre-fix model charged the borrow fee as a flat rate on
        # bond notional, so halving delta left the borrow drag unchanged.
        full = self.engine.calculate_carry(_bond(), _equity(), delta=0.60)
        half = self.engine.calculate_carry(_bond(), _equity(), delta=0.30)
        self.assertAlmostEqual(half.stock_borrow_cost, full.stock_borrow_cost / 2.0, places=9)
        self.assertAlmostEqual(half.dividends_in_lieu_cost,
                               full.dividends_in_lieu_cost / 2.0, places=9)
        self.assertAlmostEqual(half.bond_financing_cost, full.bond_financing_cost, places=9)

    def test_dividend_on_the_short_is_a_cost(self):
        no_div = self.engine.calculate_carry(
            _bond(), _equity(annual_dividend_yield=0.0), delta=0.60
        )
        with_div = self.engine.calculate_carry(
            _bond(), _equity(annual_dividend_yield=0.03), delta=0.60
        )
        self.assertLess(with_div.net_carry_currency, no_div.net_carry_currency)
        self.assertAlmostEqual(
            no_div.net_carry_currency - with_div.net_carry_currency, 0.03 * 540.0, places=9
        )

    def test_hard_to_borrow_name_destroys_carry(self):
        htb = self.engine.calculate_carry(
            _bond(), _equity(annual_borrow_fee_pct=0.15), delta=0.60
        )
        self.assertAlmostEqual(htb.stock_borrow_cost, 0.15 * 540.0, places=9)
        self.assertLess(htb.net_carry_rate, 0.0)

    def test_short_proceeds_haircut_reduces_interest_credit(self):
        engine = ConvertibleBondArbitrageEngine(short_proceeds_credit_ratio=0.5)
        carry = engine.calculate_carry(_bond(), _equity(), delta=0.60)
        self.assertAlmostEqual(carry.short_proceeds_interest, 0.04 * 0.5 * 540.0, places=9)

    def test_carry_rate_denominator_is_the_full_price(self):
        carry = self.engine.calculate_carry(_bond(), _equity(), delta=0.60)
        self.assertAlmostEqual(
            carry.net_carry_rate, carry.net_carry_currency / 1000.0, places=12
        )


class TestEvaluateArbitrage(unittest.TestCase):
    def setUp(self):
        self.engine = ConvertibleBondArbitrageEngine()

    def test_attractive_package(self):
        metrics = self.engine.evaluate_arbitrage(
            bond=_bond(), equity=_equity(), cb_quantity=100,
            implied_volatility=0.28, estimated_delta=0.60,
        )
        self.assertEqual(metrics.parity_conversion_value, 900.0)
        self.assertEqual(metrics.conversion_premium_pct, 10.0)
        self.assertEqual(metrics.optimal_short_stock_shares, 1200)
        self.assertEqual(metrics.net_carry_rate_pct, 0.04)
        self.assertAlmostEqual(metrics.bond_floor, 920.07, places=2)
        self.assertFalse(metrics.is_busted_convert)
        self.assertTrue(metrics.is_arbitrage_attractive)
        self.assertIsInstance(metrics.carry, CarryBreakdown)

    def test_rich_implied_vol_fails_the_screen(self):
        metrics = self.engine.evaluate_arbitrage(
            bond=_bond(), equity=_equity(), cb_quantity=100,
            implied_volatility=0.34, estimated_delta=0.60,
        )
        self.assertFalse(metrics.is_arbitrage_attractive)

    def test_hard_to_borrow_fails_the_screen_and_warns(self):
        metrics = self.engine.evaluate_arbitrage(
            bond=_bond(), equity=_equity(annual_borrow_fee_pct=0.15), cb_quantity=100,
            implied_volatility=0.28, estimated_delta=0.60,
        )
        self.assertFalse(metrics.is_arbitrage_attractive)
        self.assertTrue(any("Hard-to-borrow" in w for w in metrics.warnings))

    def test_busted_convert_is_flagged_and_screened_out(self):
        # Stock collapses to $10 -> parity 200 vs a ~920 floor: credit trade, not vol arb.
        metrics = self.engine.evaluate_arbitrage(
            bond=_bond(clean_price=70.0), equity=_equity(stock_price=10.0), cb_quantity=100,
            implied_volatility=0.10, estimated_delta=0.05,
        )
        self.assertTrue(metrics.is_busted_convert)
        self.assertFalse(metrics.is_arbitrage_attractive)
        self.assertTrue(any("busted" in w for w in metrics.warnings))

    def test_missing_credit_spread_degrades_gracefully_with_a_warning(self):
        metrics = self.engine.evaluate_arbitrage(
            bond=_bond(credit_spread_bps=None), equity=_equity(), cb_quantity=100,
            implied_volatility=0.28, estimated_delta=0.60,
        )
        self.assertIsNone(metrics.bond_floor)
        self.assertIsNone(metrics.is_busted_convert)
        self.assertTrue(any("credit_spread_bps" in w for w in metrics.warnings))

    def test_thresholds_are_configurable_not_hard_coded(self):
        strict = ConvertibleBondArbitrageEngine(
            thresholds=ScreenThresholds(min_vol_discount=0.20)
        )
        metrics = strict.evaluate_arbitrage(
            bond=_bond(), equity=_equity(), cb_quantity=100,
            implied_volatility=0.28, estimated_delta=0.60,
        )
        self.assertFalse(metrics.is_arbitrage_attractive)

    def test_incomplete_data_raises_instead_of_propagating_nan(self):
        with self.assertLogs(MODULE_LOGGER, level="WARNING") as logs:
            with self.assertRaises(ValueError) as ctx:
                self.engine.evaluate_arbitrage(
                    bond=_bond(), equity=_equity(stock_price=float("nan")), cb_quantity=100,
                    implied_volatility=0.28, estimated_delta=0.60,
                )
        self.assertIn("equity.stock_price", str(ctx.exception))
        self.assertTrue(any("data audit failed" in line for line in logs.output))

    def test_audit_reports_missing_and_invalid_fields_separately(self):
        with self.assertLogs(MODULE_LOGGER, level="WARNING"):
            report = self.engine.audit_data_completeness(
                _bond(clean_price=-5.0), _equity(historical_volatility=None),
                implied_volatility=0.28, estimated_delta=1.5,
            )
        self.assertFalse(report.is_complete)
        self.assertIn("bond.clean_price", report.invalid_fields)
        self.assertIn("estimated_delta", report.invalid_fields)
        self.assertIn("equity.historical_volatility", report.missing_fields)

    def test_complete_data_passes_the_audit(self):
        report = self.engine.audit_data_completeness(_bond(), _equity(), 0.28, 0.60)
        self.assertTrue(report.is_complete)
        self.assertEqual(report.missing_fields, ())
        self.assertEqual(report.invalid_fields, ())

    def test_metrics_are_finite(self):
        metrics = self.engine.evaluate_arbitrage(
            bond=_bond(), equity=_equity(), cb_quantity=100,
            implied_volatility=0.28, estimated_delta=0.60,
        )
        for value in (metrics.parity_conversion_value, metrics.conversion_premium_pct,
                      metrics.net_carry_rate_pct, metrics.carry.net_carry_rate):
            self.assertTrue(math.isfinite(value))


class TestEngineConfiguration(unittest.TestCase):
    def test_invalid_configuration_is_rejected(self):
        with self.assertRaises(ValueError):
            ConvertibleBondArbitrageEngine(risk_free_rate=float("nan"))
        with self.assertRaises(ValueError):
            ConvertibleBondArbitrageEngine(short_proceeds_credit_ratio=1.5)
        with self.assertRaises(ValueError):
            ConvertibleBondArbitrageEngine(premium_basis="dirty")

    def test_short_proceeds_rate_defaults_to_risk_free_rate(self):
        engine = ConvertibleBondArbitrageEngine(risk_free_rate=0.052)
        self.assertEqual(engine.short_proceeds_rate, 0.052)


if __name__ == "__main__":
    unittest.main()
