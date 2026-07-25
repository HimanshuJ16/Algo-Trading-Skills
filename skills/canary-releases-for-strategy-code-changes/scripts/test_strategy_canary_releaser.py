import unittest
from strategy_canary_releaser import Config, MainEngine

class TestStrategyCanaryReleaser(unittest.TestCase):
    def test_init(self):
        engine = MainEngine(Config(name="test"))
        self.assertIsNotNone(engine)

    def test_run(self):
        engine = MainEngine(Config(name="test"))
        self.assertTrue(engine.run())

if __name__ == '__main__':
    unittest.main()
