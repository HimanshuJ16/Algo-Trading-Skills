import unittest
from strategy_correlation_matrix_live_recomputation import StrategyCorrelationMatrixLiveRecomputation, StrategyCorrelationMatrixLiveRecomputationConfig

class TestStrategyCorrelationMatrixLiveRecomputation(unittest.TestCase):
    def test_execute_true(self):
        config = StrategyCorrelationMatrixLiveRecomputationConfig(enabled=True)
        engine = StrategyCorrelationMatrixLiveRecomputation(config)
        self.assertTrue(engine.execute())

    def test_execute_false(self):
        config = StrategyCorrelationMatrixLiveRecomputationConfig(enabled=False)
        engine = StrategyCorrelationMatrixLiveRecomputation(config)
        self.assertFalse(engine.execute())

if __name__ == '__main__':
    unittest.main()
