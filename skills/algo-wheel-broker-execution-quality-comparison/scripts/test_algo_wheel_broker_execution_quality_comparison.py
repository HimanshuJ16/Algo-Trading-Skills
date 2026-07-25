import unittest
from algo_wheel_broker_execution_quality_comparison import (
    BrokerExecution, 
    AlgoWheelEvaluator
)

class TestAlgoWheelEvaluator(unittest.TestCase):
    def setUp(self):
        self.evaluator = AlgoWheelEvaluator(min_allocation=0.10)
        
    def test_implementation_shortfall_math_buy(self):
        # Buy: Decision = 100, Fill = 101. Slippage = +1%. Fees = $10 on $10k = 10 bps.
        # Total IS = 100 bps + 10 bps = 110 bps.
        exec_buy = BrokerExecution("BRK_A", "BUY", 100.0, 101.0, 100.0, 10.0)
        is_bps = self.evaluator.calculate_implementation_shortfall_bps(exec_buy)
        self.assertAlmostEqual(is_bps, 110.0)

    def test_implementation_shortfall_math_sell(self):
        # Sell: Decision = 100, Fill = 99. Slippage = +1% (price dropped before sell).
        # Total IS = 101.01 bps + 0 fees = 101.01 bps.
        exec_sell = BrokerExecution("BRK_A", "SELL", 100.0, 99.0, 100.0, 0.0)
        is_bps = self.evaluator.calculate_implementation_shortfall_bps(exec_sell)
        self.assertAlmostEqual(is_bps, 101.01, places=2)

    def test_wheel_allocation_ranking(self):
        executions = [
            # Broker A: Great execution, 0 slippage
            BrokerExecution("BROKER_A", "BUY", 100.0, 100.0, 100.0, 0.0),
            # Broker B: Mediocre execution, 50 bps slippage
            BrokerExecution("BROKER_B", "BUY", 100.0, 100.5, 100.0, 0.0),
            # Broker C: Terrible execution, 200 bps slippage
            BrokerExecution("BROKER_C", "BUY", 100.0, 102.0, 100.0, 0.0),
        ]
        
        allocations = self.evaluator.evaluate_brokers(executions)
        
        # Expected Rankings: A (1st), B (2nd), C (3rd)
        # Allocations based on logic: C gets 0.10, B gets 0.45, A gets 0.45
        self.assertEqual(allocations["BROKER_A"], 0.45)
        self.assertEqual(allocations["BROKER_B"], 0.45)
        self.assertEqual(allocations["BROKER_C"], 0.10)

if __name__ == '__main__':
    unittest.main()
