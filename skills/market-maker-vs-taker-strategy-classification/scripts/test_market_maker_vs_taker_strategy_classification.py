import unittest
from market_maker_vs_taker_strategy_classification import Config, Engine

class TestMarketMakerVsTakerStrategyClassification(unittest.TestCase):
    def test_init(self):
        config = Config()
        instance = Engine(config)
        self.assertTrue(instance.config.enabled)
        
    def test_execute(self):
        config = Config()
        instance = Engine(config)
        self.assertTrue(instance.execute())

if __name__ == '__main__':
    unittest.main()
