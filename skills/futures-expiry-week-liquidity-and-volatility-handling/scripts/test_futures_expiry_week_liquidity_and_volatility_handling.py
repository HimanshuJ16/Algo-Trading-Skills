import unittest
from futures_expiry_week_liquidity_and_volatility_handling import InputData, FuturesExpiryWeekLiquidityAndVolatilityHandlingEngine

class TestFuturesExpiryWeekLiquidityAndVolatilityHandling(unittest.TestCase):
    def test_process(self):
        engine = FuturesExpiryWeekLiquidityAndVolatilityHandlingEngine()
        res = engine.process(InputData(value=10.0))
        self.assertEqual(res, 20.0)

    def test_process_zero(self):
        engine = FuturesExpiryWeekLiquidityAndVolatilityHandlingEngine()
        res = engine.process(InputData(value=0.0))
        self.assertEqual(res, 0.0)

if __name__ == '__main__':
    unittest.main()
