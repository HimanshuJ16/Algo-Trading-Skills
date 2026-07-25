import unittest
from chaos_monkey_trading_simulator import Config, MainEngine

class TestChaosMonkeyTradingSimulator(unittest.TestCase):
    def test_init(self):
        engine = MainEngine(Config(name="test"))
        self.assertIsNotNone(engine)

    def test_run(self):
        engine = MainEngine(Config(name="test"))
        self.assertTrue(engine.run())

if __name__ == '__main__':
    unittest.main()
