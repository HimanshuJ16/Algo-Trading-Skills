import unittest
from execution_algo_parameter_optimization_via_backtest import Config, Engine

class TestEngine(unittest.TestCase):
    def test_execute_true(self):
        engine = Engine(Config(enabled=True))
        self.assertTrue(engine.execute())

    def test_execute_false(self):
        engine = Engine(Config(enabled=False))
        self.assertFalse(engine.execute())

if __name__ == '__main__':
    unittest.main()
