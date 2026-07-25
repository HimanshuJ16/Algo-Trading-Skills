import unittest
from portfolio_level_stop_loss_independent_of_strategy_stops import PortfolioLevelStopLossIndependentOfStrategyStops, PortfolioLevelStopLossIndependentOfStrategyStopsConfig

class TestPortfolioLevelStopLossIndependentOfStrategyStops(unittest.TestCase):
    def test_execute_true(self):
        config = PortfolioLevelStopLossIndependentOfStrategyStopsConfig(enabled=True)
        engine = PortfolioLevelStopLossIndependentOfStrategyStops(config)
        self.assertTrue(engine.execute())

    def test_execute_false(self):
        config = PortfolioLevelStopLossIndependentOfStrategyStopsConfig(enabled=False)
        engine = PortfolioLevelStopLossIndependentOfStrategyStops(config)
        self.assertFalse(engine.execute())

if __name__ == '__main__':
    unittest.main()
