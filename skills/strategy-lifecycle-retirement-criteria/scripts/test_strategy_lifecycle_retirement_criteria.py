import unittest
from strategy_lifecycle_retirement_criteria import StrategyLifecycleRetirementCriteria, StrategyLifecycleRetirementCriteriaConfig

class TestStrategyLifecycleRetirementCriteria(unittest.TestCase):
    def test_execute_true(self):
        config = StrategyLifecycleRetirementCriteriaConfig(enabled=True)
        engine = StrategyLifecycleRetirementCriteria(config)
        self.assertTrue(engine.execute())

    def test_execute_false(self):
        config = StrategyLifecycleRetirementCriteriaConfig(enabled=False)
        engine = StrategyLifecycleRetirementCriteria(config)
        self.assertFalse(engine.execute())

if __name__ == '__main__':
    unittest.main()
