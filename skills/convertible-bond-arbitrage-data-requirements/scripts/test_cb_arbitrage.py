import unittest
from cb_arbitrage import (
    ConvertibleBondArbitrageEngine, ConvertibleBondData, EquityMarketData
)

class TestConvertibleBondArbitrageEngine(unittest.TestCase):

    def setUp(self):
        self.engine = ConvertibleBondArbitrageEngine(risk_free_rate=0.04, repo_financing_rate=0.045)
        
        self.bond = ConvertibleBondData(
            bond_id="CB_1001", symbol="XYZ_CB", clean_price=99.0, # $990 per bond
            par_value=1000.0, conversion_ratio=20.0, coupon_rate_annual=0.04, years_to_maturity=3.0
        )
        self.equity = EquityMarketData(
            symbol="XYZ", stock_price=45.0, annual_borrow_fee_pct=0.01, historical_volatility=0.35
        )

    def test_parity_and_premium_calculation(self):
        # Conversion ratio 20.0 * Stock $45.0 = Parity $900.0
        parity = self.engine.calculate_parity(20.0, 45.0)
        self.assertEqual(parity, 900.0)
        
        # CB Price = $990.0, Parity = $900.0 -> Premium = (990 - 900) / 900 = 10.0%
        prem = self.engine.calculate_conversion_premium_pct(990.0, parity)
        self.assertEqual(prem, 10.0)

    def test_delta_hedge_sizing(self):
        # 100 CB contracts * 20.0 ratio * 0.60 delta = 1,200 short shares
        short_qty = self.engine.calculate_delta_hedge_quantity(
            cb_quantity=100, conversion_ratio=20.0, delta=0.60
        )
        self.assertEqual(short_qty, 1200)

    def test_evaluate_arbitrage_opportunity(self):
        # Implied Vol = 28% vs Historical Vol = 35% (Cheap volatility!)
        metrics = self.engine.evaluate_arbitrage(
            bond=self.bond,
            equity=self.equity,
            cb_quantity=100,
            implied_volatility=0.28,
            estimated_delta=0.60
        )
        
        self.assertEqual(metrics.parity_conversion_value, 900.0)
        self.assertEqual(metrics.conversion_premium_pct, 10.0)
        self.assertEqual(metrics.optimal_short_stock_shares, 1200)
        self.assertTrue(metrics.is_arbitrage_attractive)

if __name__ == '__main__':
    unittest.main()
