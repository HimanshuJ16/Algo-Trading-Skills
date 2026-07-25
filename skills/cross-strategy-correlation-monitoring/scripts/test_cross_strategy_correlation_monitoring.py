import unittest
from cross_strategy_correlation_monitoring import CrossStrategyCorrelationMonitoring, CrossStrategyCorrelationMonitoringConfig

class TestCrossStrategyCorrelationMonitoring(unittest.TestCase):
    def test_execute_true(self):
        config = CrossStrategyCorrelationMonitoringConfig(enabled=True)
        engine = CrossStrategyCorrelationMonitoring(config)
        self.assertTrue(engine.execute())

    def test_execute_false(self):
        config = CrossStrategyCorrelationMonitoringConfig(enabled=False)
        engine = CrossStrategyCorrelationMonitoring(config)
        self.assertFalse(engine.execute())

if __name__ == '__main__':
    unittest.main()
