import unittest
from multi_leg_strategy_margin_optimization import InputData, MultiLegStrategyMarginOptimizationEngine

class TestMultiLegStrategyMarginOptimization(unittest.TestCase):
    def test_process(self):
        engine = MultiLegStrategyMarginOptimizationEngine()
        res = engine.process(InputData(value=10.0))
        self.assertEqual(res, 20.0)

    def test_process_zero(self):
        engine = MultiLegStrategyMarginOptimizationEngine()
        res = engine.process(InputData(value=0.0))
        self.assertEqual(res, 0.0)

if __name__ == '__main__':
    unittest.main()
