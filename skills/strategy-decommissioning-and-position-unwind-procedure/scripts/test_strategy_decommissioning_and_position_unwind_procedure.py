import unittest
from strategy_decommissioning_and_position_unwind_procedure import Config, Engine

class TestStrategyDecommissioningAndPositionUnwindProcedure(unittest.TestCase):
    def test_init(self):
        engine = Engine(Config())
        self.assertTrue(engine.config.enabled)
        
    def test_run(self):
        engine = Engine(Config())
        self.assertTrue(engine.run())

if __name__ == '__main__':
    unittest.main()
