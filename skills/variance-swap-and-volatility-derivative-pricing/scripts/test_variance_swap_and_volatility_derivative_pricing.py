import unittest
from variance_swap_and_volatility_derivative_pricing import Configuration, VarianceSwapAndVolatilityDerivativePricingEngine

class TestVarianceSwapAndVolatilityDerivativePricing(unittest.TestCase):
    def test_default_execution(self):
        engine = VarianceSwapAndVolatilityDerivativePricingEngine()
        res = engine.execute({"value": 10})
        self.assertEqual(res["status"], "success")
        self.assertEqual(res["value"], 5.0)

    def test_disabled_execution(self):
        engine = VarianceSwapAndVolatilityDerivativePricingEngine(Configuration(enabled=False))
        res = engine.execute({"value": 10})
        self.assertEqual(res["status"], "disabled")
        
    def test_empty_data(self):
        engine = VarianceSwapAndVolatilityDerivativePricingEngine()
        res = engine.execute({})
        self.assertEqual(res["status"], "success")
        self.assertEqual(res["value"], 0.0)

if __name__ == '__main__':
    unittest.main()
