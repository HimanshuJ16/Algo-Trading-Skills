import unittest
import math
from storage_model import CommodityCarryCostModel, CarryCostResult

class TestCommodityCarryCostModel(unittest.TestCase):

    def setUp(self):
        # r = 5% (0.05), c = 2% (0.02) -> Base carry rate = 7%
        self.model = CommodityCarryCostModel(risk_free_rate=0.05, storage_cost_rate=0.02)

    def test_contango_pricing(self):
        # Spot = 100, T = 1.0, y = 0.01 (1%)
        # Net rate = 0.05 + 0.02 - 0.01 = 0.06 (6%)
        # F_theo = 100 * exp(0.06) = 106.1837
        f_theo = self.model.calculate_theoretical_futures_price(
            spot_price=100.0, time_to_maturity_years=1.0, convenience_yield=0.01
        )
        self.assertAlmostEqual(f_theo, 100.0 * math.exp(0.06), places=4)

    def test_implied_convenience_yield_extraction(self):
        # If Spot = 100, F_market = 106.1837, T = 1.0, r=0.05, c=0.02
        # Implied y should be ~0.01
        f_market = 100.0 * math.exp(0.06)
        implied_y = self.model.extract_implied_convenience_yield(
            spot_price=100.0, futures_market_price=f_market, time_to_maturity_years=1.0
        )
        self.assertAlmostEqual(implied_y, 0.01, places=4)

    def test_backwardation_regime(self):
        # High convenience yield (y = 0.12) -> Net rate = 0.05 + 0.02 - 0.12 = -0.05
        # Spot = 100 -> F_market = 95.12 (Backwardation)
        f_market = 100.0 * math.exp(-0.05)
        res = self.model.evaluate_market(
            spot_price=100.0, futures_market_price=f_market, time_to_maturity_years=1.0
        )
        self.assertEqual(res.regime, "BACKWARDATION")
        self.assertGreater(res.basis, 0.0) # Spot > Futures

    def test_cash_and_carry_arbitrage(self):
        # Spot = 100, Fair F = 106.18, Market F = 115.00 (Severely Overpriced Futures)
        res = self.model.evaluate_market(
            spot_price=100.0, futures_market_price=115.00, time_to_maturity_years=1.0
        )
        self.assertTrue(res.is_arbitrage_opportunity)
        self.assertEqual(res.arbitrage_type, "CASH_AND_CARRY")

if __name__ == '__main__':
    unittest.main()
