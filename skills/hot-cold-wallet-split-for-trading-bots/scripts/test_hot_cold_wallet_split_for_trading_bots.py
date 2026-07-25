import unittest
from hot_cold_wallet_split_for_trading_bots import Config, Engine

class TestEngine(unittest.TestCase):
    def test_init(self):
        engine = Engine(Config())
        self.assertEqual(engine.config.name, "hot-cold-wallet-split-for-trading-bots")

    def test_run(self):
        engine = Engine(Config())
        self.assertTrue(engine.run())

if __name__ == '__main__':
    unittest.main()
