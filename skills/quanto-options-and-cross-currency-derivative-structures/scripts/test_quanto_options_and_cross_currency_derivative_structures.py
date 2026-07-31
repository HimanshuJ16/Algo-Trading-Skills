import unittest
from quanto_options_and_cross_currency_derivative_structures import (
    InputData, QuantoOptionsAndCrossCurrencyDerivativeStructuresEngine,
    QuantoOptionPricingReport
)

class TestQuantoOptionsAndCrossCurrencyDerivativeStructures(unittest.TestCase):

    def setUp(self):
        self.engine = QuantoOptionsAndCrossCurrencyDerivativeStructuresEngine()

    def test_legacy_process(self):
        res = self.engine.process(InputData(value=10.0))
        self.assertEqual(res, 20.0)

    def test_legacy_process_zero(self):
        res = self.engine.process(InputData(value=0.0))
        self.assertEqual(res, 0.0)

    def test_quanto_call_option_pricing(self):
        # S=100, K=100, T=1.0, r_d=0.05, r_f=0.02, q=0.0, sigma_S=0.20, sigma_X=0.15, rho=0.30, F_X=1.0
        # r_quanto = 0.02 - 0.0 - 0.30*0.20*0.15 = 0.011 (1.1%)
        data = InputData(
            spot_price=100.0, strike_price=100.0, time_to_expiry_years=1.0,
            domestic_rate=0.05, foreign_rate=0.02, dividend_yield=0.0,
            asset_volatility=0.20, fx_volatility=0.15, correlation=0.30, fixed_fx_rate=1.0,
            option_type='CALL'
        )

        report = self.engine.price_quanto_option(data)
        self.assertEqual(report.status, "QUANTO_PRICING_SUCCESSFUL")
        self.assertEqual(report.quanto_drift, 0.011)
        self.assertGreater(report.quanto_option_price_domestic, 0.0)
        self.assertGreater(report.quanto_delta, 0.0)
        self.assertLess(report.fx_correlation_sensitivity, 0.0) # Higher correlation lowers quanto drift, reducing call price

    def test_quanto_put_option_pricing(self):
        data = InputData(
            spot_price=100.0, strike_price=100.0, time_to_expiry_years=1.0,
            domestic_rate=0.05, foreign_rate=0.02, dividend_yield=0.0,
            asset_volatility=0.20, fx_volatility=0.15, correlation=0.30, fixed_fx_rate=1.0,
            option_type='PUT'
        )

        report = self.engine.price_quanto_option(data)
        self.assertEqual(report.status, "QUANTO_PRICING_SUCCESSFUL")
        self.assertGreater(report.quanto_option_price_domestic, 0.0)
        self.assertLess(report.quanto_delta, 0.0)

if __name__ == '__main__':
    unittest.main()
