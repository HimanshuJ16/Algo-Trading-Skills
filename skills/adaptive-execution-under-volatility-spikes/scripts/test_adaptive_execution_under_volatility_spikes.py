import unittest
from adaptive_execution_under_volatility_spikes import AdaptiveExecutionUnderVolatilitySpikesConfig, AdaptiveExecutionUnderVolatilitySpikesEngine

class TestAdaptiveExecutionUnderVolatilitySpikes(unittest.TestCase):
    def setUp(self):
        self.config = AdaptiveExecutionUnderVolatilitySpikesConfig(enabled=True, threshold=100.0, size=50)
        self.engine = AdaptiveExecutionUnderVolatilitySpikesEngine(self.config)

    def test_evaluate_triggers_order(self):
        market_data = {"symbol": "AAPL", "price": 105.0}
        orders = self.engine.evaluate(market_data)
        self.assertEqual(len(orders), 1)
        self.assertEqual(orders[0]["qty"], 50)
        
    def test_evaluate_no_trigger(self):
        market_data = {"symbol": "AAPL", "price": 95.0}
        orders = self.engine.evaluate(market_data)
        self.assertEqual(len(orders), 0)

    def test_disabled_engine(self):
        self.engine.config.enabled = False
        market_data = {"symbol": "AAPL", "price": 105.0}
        orders = self.engine.evaluate(market_data)
        self.assertEqual(len(orders), 0)

if __name__ == '__main__':
    unittest.main()
