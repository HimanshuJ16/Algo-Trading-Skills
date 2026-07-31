import unittest
from options_implied_volatility_surface import (
    OptionsIVSurfaceConstructionEngine, IVSurfaceConfig, IVSurfaceConstructionReport
)

class TestOptionsIVSurfaceConstructionEngine(unittest.TestCase):

    def setUp(self):
        config = IVSurfaceConfig(
            spot_price=100.0,
            risk_free_rate=0.05,
            atm_vol=0.20,
            skew_alpha=-0.30,
            smile_beta=0.50
        )
        self.engine = OptionsIVSurfaceConstructionEngine(config)

    def test_parametric_volatility_smile(self):
        # ATM strike 100 -> IV = 0.20
        iv_atm = self.engine.evaluate_strike_iv(100.0, 0.25)
        self.assertEqual(iv_atm, 0.20)

        # OTM Put strike 90 -> Moneyness 0.90 -> Skew elevates IV > 0.20
        iv_put = self.engine.evaluate_strike_iv(90.0, 0.25)
        self.assertGreater(iv_put, 0.20)

    def test_surface_grid_construction_and_arbitrage_check(self):
        strikes = [90.0, 100.0, 110.0]
        expirations = [0.25, 0.50, 1.0]

        report = self.engine.construct_surface_grid(strikes, expirations)

        self.assertEqual(report.status, "ARBITRAGE_FREE_SURFACE")
        self.assertEqual(report.total_surface_points, 9)

        # Verify total variance w(1.0) > w(0.25)
        pt_short = [p for p in report.grid_points if p.strike == 100.0 and p.tte_years == 0.25][0]
        pt_long = [p for p in report.grid_points if p.strike == 100.0 and p.tte_years == 1.0][0]
        self.assertGreater(pt_long.total_variance, pt_short.total_variance)

    def test_black_scholes_pricing_helper(self):
        price_call = self.engine.black_scholes_price("CALL", spot=100.0, strike=100.0, tte=1.0, vol=0.20, r=0.05)
        self.assertGreater(price_call, 8.0) # ATM 1-yr Call ~$10.45

if __name__ == '__main__':
    unittest.main()
