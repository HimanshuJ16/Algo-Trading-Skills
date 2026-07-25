import unittest
from options_greeks_real_time_portfolio_aggregation import Configuration, OptionsGreeksRealTimePortfolioAggregationEngine

class TestOptionsGreeksRealTimePortfolioAggregation(unittest.TestCase):
    def test_default_execution(self):
        engine = OptionsGreeksRealTimePortfolioAggregationEngine()
        res = engine.execute({"value": 10})
        self.assertEqual(res["status"], "success")
        self.assertEqual(res["value"], 5.0)

    def test_disabled_execution(self):
        engine = OptionsGreeksRealTimePortfolioAggregationEngine(Configuration(enabled=False))
        res = engine.execute({"value": 10})
        self.assertEqual(res["status"], "disabled")
        
    def test_empty_data(self):
        engine = OptionsGreeksRealTimePortfolioAggregationEngine()
        res = engine.execute({})
        self.assertEqual(res["status"], "success")
        self.assertEqual(res["value"], 0.0)

if __name__ == '__main__':
    unittest.main()
