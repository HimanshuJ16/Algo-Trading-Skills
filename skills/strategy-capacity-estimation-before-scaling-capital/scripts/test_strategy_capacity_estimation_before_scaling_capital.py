import unittest
from strategy_capacity_estimation_before_scaling_capital import StrategyCapacityEstimationBeforeScalingCapital, StrategyCapacityEstimationBeforeScalingCapitalConfig

class TestStrategyCapacityEstimationBeforeScalingCapital(unittest.TestCase):
    def test_execute_true(self):
        config = StrategyCapacityEstimationBeforeScalingCapitalConfig(enabled=True)
        engine = StrategyCapacityEstimationBeforeScalingCapital(config)
        self.assertTrue(engine.execute())

    def test_execute_false(self):
        config = StrategyCapacityEstimationBeforeScalingCapitalConfig(enabled=False)
        engine = StrategyCapacityEstimationBeforeScalingCapital(config)
        self.assertFalse(engine.execute())

if __name__ == '__main__':
    unittest.main()
