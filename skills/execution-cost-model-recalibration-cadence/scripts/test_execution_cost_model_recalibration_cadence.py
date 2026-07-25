import unittest
from execution_cost_model_recalibration_cadence import Config, Engine

class TestEngine(unittest.TestCase):
    def test_execute_true(self):
        engine = Engine(Config(enabled=True))
        self.assertTrue(engine.execute())

    def test_execute_false(self):
        engine = Engine(Config(enabled=False))
        self.assertFalse(engine.execute())

if __name__ == '__main__':
    unittest.main()
