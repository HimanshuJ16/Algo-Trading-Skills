"""
Unit tests for options-backtesting-with-realistic-iv-surface skill.
"""
import unittest
from options_iv_backtester import OptionsIVSurfaceEngine


class TestOptionsIVSurfaceEngine(unittest.TestCase):

    def setUp(self):
        self.engine = OptionsIVSurfaceEngine(
            risk_free_rate=0.05,
            skew_alpha=-0.30,
            smile_beta=0.50,
        )

    def test_strike_iv_skew_and_smile(self):
        spot = 100.0
        atm_vol = 0.20

        # ATM strike (100) -> IV = 0.20
        iv_atm = self.engine.get_strike_iv(spot, 100.0, 0.25, atm_vol)
        self.assertEqual(iv_atm, 0.20)

        # OTM Put strike (90) -> Moneyness 0.9 -> Skew elevates IV > 0.20
        iv_otm_put = self.engine.get_strike_iv(spot, 90.0, 0.25, atm_vol)
        self.assertGreater(iv_otm_put, 0.20)

    def test_black_scholes_pricing_and_greeks(self):
        # 30-day Call at $100 strike on $105 spot
        call_res = self.engine.price_option("CALL", spot=105.0, strike=100.0, tte_years=30/365.0, atm_vol=0.20)

        self.assertEqual(call_res.option_type, "CALL")
        self.assertGreater(call_res.option_price, 5.0)  # Intrinsic value = $5.0
        self.assertGreater(call_res.greeks.delta, 0.50)  # ITM call delta > 0.5
        self.assertGreater(call_res.greeks.gamma, 0.0)


if __name__ == "__main__":
    unittest.main()
