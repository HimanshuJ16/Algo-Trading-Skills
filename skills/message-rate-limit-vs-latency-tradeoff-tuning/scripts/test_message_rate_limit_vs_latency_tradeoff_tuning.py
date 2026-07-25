import unittest
from message_rate_limit_vs_latency_tradeoff_tuning import Engine, Config

class TestEngine(unittest.TestCase):
    def test_run(self):
        engine = Engine(Config())
        self.assertTrue(engine.run())
        
    def test_config(self):
        config = Config(param1=2)
        self.assertEqual(config.param1, 2)

if __name__ == '__main__':
    unittest.main()
