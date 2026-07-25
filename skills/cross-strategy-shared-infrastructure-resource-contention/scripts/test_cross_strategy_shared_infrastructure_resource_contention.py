import unittest
from cross_strategy_shared_infrastructure_resource_contention import CrossStrategySharedInfrastructureResourceContention, CrossStrategySharedInfrastructureResourceContentionConfig

class TestCrossStrategySharedInfrastructureResourceContention(unittest.TestCase):
    def test_execute_true(self):
        config = CrossStrategySharedInfrastructureResourceContentionConfig(enabled=True)
        engine = CrossStrategySharedInfrastructureResourceContention(config)
        self.assertTrue(engine.execute())

    def test_execute_false(self):
        config = CrossStrategySharedInfrastructureResourceContentionConfig(enabled=False)
        engine = CrossStrategySharedInfrastructureResourceContention(config)
        self.assertFalse(engine.execute())

if __name__ == '__main__':
    unittest.main()
