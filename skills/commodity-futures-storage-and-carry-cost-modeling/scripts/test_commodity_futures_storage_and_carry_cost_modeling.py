import logging
import math
import unittest

from commodity_futures_storage_and_carry_cost_modeling import (
    CommodityCarryCostModel,
)


class TestCommodityCarryCostModel(unittest.TestCase):

    def setUp(self):
        # r = 5% (0.05), c = 2% (0.02) -> full carry rate = 7%
        self.model = CommodityCarryCostModel(risk_free_rate=0.05, storage_cost_rate=0.02)
        logging.disable(logging.CRITICAL)

    def tearDown(self):
        logging.disable(logging.NOTSET)

    # --- Pricing -----------------------------------------------------------

    def test_contango_pricing(self):
        # Spot = 100, T = 1.0, y = 0.01 -> net rate 0.05 + 0.02 - 0.01 = 0.06.
        # Expected value derived independently: 100 * e^0.06 = 106.18365...
        f_theo = self.model.calculate_theoretical_futures_price(
            spot_price=100.0, time_to_maturity_years=1.0, convenience_yield=0.01
        )
        self.assertAlmostEqual(f_theo, 106.183654654535, places=9)

    def test_full_carry_price_is_zero_yield_price(self):
        # 100 * e^0.07 = 107.25081...
        self.assertAlmostEqual(
            self.model.calculate_full_carry_price(100.0, 1.0), 107.250818125421, places=9
        )
        self.assertAlmostEqual(
            self.model.calculate_theoretical_futures_price(100.0, 1.0, 0.0),
            self.model.calculate_full_carry_price(100.0, 1.0),
            places=12,
        )

    def test_implied_convenience_yield_extraction(self):
        f_market = 100.0 * math.exp(0.06)
        implied_y = self.model.extract_implied_convenience_yield(
            spot_price=100.0, futures_market_price=f_market, time_to_maturity_years=1.0
        )
        self.assertAlmostEqual(implied_y, 0.01, places=12)

    def test_implied_yield_round_trips_through_pricing(self):
        for y in (-0.02, 0.0, 0.01, 0.25):
            with self.subTest(y=y):
                f = self.model.calculate_theoretical_futures_price(87.5, 0.75, y)
                self.assertAlmostEqual(
                    self.model.extract_implied_convenience_yield(87.5, f, 0.75), y, places=10
                )

    # --- Fixed (per-unit) storage costs ------------------------------------

    def test_fixed_storage_cost_raises_full_carry(self):
        # 0.60 USD/bbl/year storage, r = 5%, T = 1: U = 0.6 * (1 - e^-0.05) / 0.05
        model = CommodityCarryCostModel(
            risk_free_rate=0.05, storage_cost_rate=0.0, storage_cost_per_unit_per_year=0.60
        )
        expected_u = 0.60 * (1.0 - math.exp(-0.05)) / 0.05  # 0.585269...
        self.assertAlmostEqual(model.present_value_of_fixed_storage(1.0), expected_u, places=12)
        self.assertAlmostEqual(
            model.calculate_full_carry_price(80.0, 1.0),
            (80.0 + expected_u) * math.exp(0.05),
            places=10,
        )

    def test_fixed_storage_pv_limit_at_zero_rate(self):
        model = CommodityCarryCostModel(
            risk_free_rate=0.0, storage_cost_rate=0.0, storage_cost_per_unit_per_year=0.60
        )
        self.assertAlmostEqual(model.present_value_of_fixed_storage(2.0), 1.20, places=12)

    def test_zero_fixed_storage_reduces_to_proportional_model(self):
        self.assertEqual(self.model.present_value_of_fixed_storage(1.0), 0.0)
        self.assertAlmostEqual(
            self.model.calculate_full_carry_price(100.0, 2.0),
            100.0 * math.exp(0.07 * 2.0),
            places=10,
        )

    # --- Regime classification ---------------------------------------------

    def test_backwardation_regime(self):
        f_market = 100.0 * math.exp(-0.05)
        res = self.model.evaluate_market(
            spot_price=100.0, futures_market_price=f_market, time_to_maturity_years=1.0
        )
        self.assertEqual(res.regime, "BACKWARDATION")
        self.assertGreater(res.basis, 0.0)

    def test_flat_curve_is_not_labelled_backwardation(self):
        # Regression: F == S was previously classified BACKWARDATION.
        res = self.model.evaluate_market(
            spot_price=100.0, futures_market_price=100.0, time_to_maturity_years=1.0
        )
        self.assertEqual(res.regime, "FLAT")
        self.assertEqual(res.basis, 0.0)

    # --- Arbitrage auditing -------------------------------------------------

    def test_cash_and_carry_arbitrage(self):
        # Full carry = 107.25; market 115.00 is rich beyond the 0.5% cost band.
        res = self.model.evaluate_market(
            spot_price=100.0, futures_market_price=115.00, time_to_maturity_years=1.0
        )
        self.assertTrue(res.is_arbitrage_opportunity)
        self.assertEqual(res.arbitrage_type, "CASH_AND_CARRY")
        self.assertTrue(res.convenience_yield_bound_violated)
        self.assertLess(res.implied_convenience_yield, 0.0)

    def test_futures_above_spot_but_below_full_carry_is_not_arbitrage(self):
        # Contango that is still inside full carry: positive convenience yield.
        res = self.model.evaluate_market(
            spot_price=100.0, futures_market_price=104.0, time_to_maturity_years=1.0
        )
        self.assertEqual(res.regime, "CONTANGO")
        self.assertFalse(res.is_arbitrage_opportunity)
        self.assertFalse(res.convenience_yield_bound_violated)

    def test_ordinary_backwardation_is_not_reported_as_arbitrage(self):
        # Regression: a WTI-like curve (spot 80, 6M future 76) implies a ~17%
        # convenience yield -- an entirely normal tight-inventory market. The
        # previous implementation flagged it REVERSE_CASH_AND_CARRY arbitrage.
        res = self.model.evaluate_market(
            spot_price=80.0, futures_market_price=76.0, time_to_maturity_years=0.5
        )
        self.assertEqual(res.regime, "BACKWARDATION")
        self.assertFalse(res.is_arbitrage_opportunity)
        self.assertIsNone(res.arbitrage_type)
        self.assertFalse(res.convenience_yield_bound_violated)
        self.assertGreater(res.implied_convenience_yield, 0.15)
        # It is still surfaced as a view-relative candidate, not an arbitrage.
        self.assertTrue(res.reverse_carry_candidate)

    def test_arbitrage_requires_clearing_transaction_costs(self):
        full_carry = self.model.calculate_full_carry_price(100.0, 1.0)
        just_inside = full_carry * 1.004
        just_outside = full_carry * 1.006
        self.assertFalse(
            self.model.evaluate_market(100.0, just_inside, 1.0).is_arbitrage_opportunity
        )
        self.assertTrue(
            self.model.evaluate_market(100.0, just_outside, 1.0).is_arbitrage_opportunity
        )

    def test_exact_full_carry_price_is_not_an_arbitrage(self):
        full_carry = self.model.calculate_full_carry_price(100.0, 1.0)
        res = self.model.evaluate_market(
            100.0, full_carry, 1.0, transaction_cost_pct=0.0
        )
        self.assertFalse(res.is_arbitrage_opportunity)
        self.assertFalse(res.convenience_yield_bound_violated)
        self.assertAlmostEqual(res.implied_convenience_yield, 0.0, places=6)

    # --- Input validation ---------------------------------------------------

    def test_nan_inputs_are_rejected(self):
        # Regression: NaN passed the `<= 0` guard and produced NaN prices with a
        # confidently wrong regime label.
        nan = float("nan")
        with self.assertRaises(ValueError):
            self.model.evaluate_market(nan, 100.0, 1.0)
        with self.assertRaises(ValueError):
            self.model.evaluate_market(100.0, nan, 1.0)
        with self.assertRaises(ValueError):
            self.model.evaluate_market(100.0, 100.0, nan)
        with self.assertRaises(ValueError):
            self.model.calculate_theoretical_futures_price(100.0, 1.0, nan)

    def test_infinite_inputs_are_rejected(self):
        with self.assertRaises(ValueError):
            self.model.evaluate_market(float("inf"), 100.0, 1.0)
        with self.assertRaises(ValueError):
            CommodityCarryCostModel(risk_free_rate=float("inf"))

    def test_non_positive_inputs_are_rejected(self):
        with self.assertRaises(ValueError):
            self.model.calculate_theoretical_futures_price(0.0, 1.0, 0.01)
        with self.assertRaises(ValueError):
            self.model.calculate_theoretical_futures_price(100.0, 0.0, 0.01)
        with self.assertRaises(ValueError):
            self.model.extract_implied_convenience_yield(100.0, -1.0, 1.0)
        with self.assertRaises(ValueError):
            self.model.evaluate_market(100.0, 100.0, 1.0, transaction_cost_pct=-0.01)
        with self.assertRaises(ValueError):
            CommodityCarryCostModel(storage_cost_per_unit_per_year=-0.1)

    def test_sub_daily_maturity_warns(self):
        logging.disable(logging.NOTSET)
        with self.assertLogs(
            "commodity_futures_storage_and_carry_cost_modeling", level="WARNING"
        ) as cm:
            self.model.extract_implied_convenience_yield(100.0, 100.1, 1.0 / 1440.0)
        self.assertTrue(any("below one day" in line for line in cm.output))


if __name__ == "__main__":
    unittest.main()
