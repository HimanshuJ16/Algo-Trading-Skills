import unittest

from algo_wheel_broker_execution_quality_comparison import (
    AlgoWheelEvaluator,
    BrokerExecution,
)


class TestAlgoWheelEvaluator(unittest.TestCase):
    def setUp(self):
        self.evaluator = AlgoWheelEvaluator(min_allocation=0.10)

    def test_implementation_shortfall_math_buy(self):
        execution = BrokerExecution("BRK_A", "BUY", 100.0, 101.0, 100.0, 10.0)
        self.assertAlmostEqual(
            self.evaluator.calculate_implementation_shortfall_bps(execution), 110.0
        )

    def test_implementation_shortfall_math_sell(self):
        execution = BrokerExecution("BRK_A", "SELL", 100.0, 99.0, 100.0, 0.0)
        self.assertAlmostEqual(
            self.evaluator.calculate_implementation_shortfall_bps(execution), 100.0
        )

    def test_favorable_execution_has_negative_shortfall(self):
        execution = BrokerExecution("BRK_A", "BUY", 100.0, 99.0, 100.0, 0.0)
        self.assertAlmostEqual(
            self.evaluator.calculate_implementation_shortfall_bps(execution), -100.0
        )

    def test_broker_scores_are_notional_weighted(self):
        executions = [
            BrokerExecution("BROKER_A", "BUY", 100.0, 100.0, 1.0, 0.0),
            BrokerExecution("BROKER_A", "BUY", 100.0, 101.0, 100.0, 0.0),
            BrokerExecution("BROKER_B", "BUY", 100.0, 100.5, 100.0, 0.0),
        ]
        allocations = self.evaluator.evaluate_brokers(executions)
        self.assertEqual(allocations["BROKER_B"], 0.9)
        self.assertEqual(allocations["BROKER_A"], 0.1)

    def test_wheel_allocation_keeps_canary_flow(self):
        executions = [
            BrokerExecution("BROKER_A", "BUY", 100.0, 100.0, 100.0, 0.0),
            BrokerExecution("BROKER_B", "BUY", 100.0, 100.5, 100.0, 0.0),
            BrokerExecution("BROKER_C", "BUY", 100.0, 102.0, 100.0, 0.0),
        ]
        allocations = self.evaluator.evaluate_brokers(executions)
        self.assertEqual(allocations["BROKER_A"], 0.8)
        self.assertEqual(allocations["BROKER_B"], 0.1)
        self.assertEqual(allocations["BROKER_C"], 0.1)
        self.assertAlmostEqual(sum(allocations.values()), 1.0)

    def test_ties_are_ranked_deterministically_by_broker_id(self):
        executions = [
            BrokerExecution("BROKER_B", "BUY", 100.0, 100.0, 100.0, 0.0),
            BrokerExecution("BROKER_A", "BUY", 100.0, 100.0, 100.0, 0.0),
        ]
        allocations = self.evaluator.evaluate_brokers(executions)
        self.assertEqual(allocations, {"BROKER_A": 0.9, "BROKER_B": 0.1})

    def test_single_broker_receives_all_flow(self):
        executions = [
            BrokerExecution("BROKER_A", "BUY", 100.0, 100.0, 100.0, 0.0)
        ]
        self.assertEqual(self.evaluator.evaluate_brokers(executions), {"BROKER_A": 1.0})

    def test_empty_execution_set_returns_empty_allocations(self):
        self.assertEqual(self.evaluator.evaluate_brokers([]), {})

    def test_invalid_prices_and_quantity_are_rejected(self):
        invalid_executions = [
            BrokerExecution("BROKER_A", "BUY", 0.0, 100.0, 100.0, 0.0),
            BrokerExecution("BROKER_A", "BUY", 100.0, 0.0, 100.0, 0.0),
            BrokerExecution("BROKER_A", "BUY", 100.0, 100.0, 0.0, 0.0),
        ]
        for execution in invalid_executions:
            with self.assertRaises(ValueError):
                self.evaluator.calculate_implementation_shortfall_bps(execution)

    def test_invalid_side_is_rejected(self):
        execution = BrokerExecution("BROKER_A", "HOLD", 100.0, 100.0, 100.0, 0.0)
        with self.assertRaises(ValueError):
            self.evaluator.calculate_implementation_shortfall_bps(execution)

    def test_non_finite_data_is_rejected(self):
        execution = BrokerExecution("BROKER_A", "BUY", float("nan"), 100.0, 100.0, 0.0)
        with self.assertRaises(ValueError):
            self.evaluator.calculate_implementation_shortfall_bps(execution)

    def test_invalid_minimum_allocation_is_rejected(self):
        with self.assertRaises(ValueError):
            AlgoWheelEvaluator(min_allocation=0.0)
        with self.assertRaises(ValueError):
            AlgoWheelEvaluator(min_allocation=1.0)

    def test_minimum_allocation_must_leave_flow_for_leader(self):
        evaluator = AlgoWheelEvaluator(min_allocation=0.5)
        executions = [
            BrokerExecution("BROKER_A", "BUY", 100.0, 100.0, 100.0, 0.0),
            BrokerExecution("BROKER_B", "BUY", 100.0, 100.5, 100.0, 0.0),
            BrokerExecution("BROKER_C", "BUY", 100.0, 101.0, 100.0, 0.0),
        ]
        with self.assertRaises(ValueError):
            evaluator.evaluate_brokers(executions)

    def test_blank_broker_is_rejected(self):
        execution = BrokerExecution(" ", "BUY", 100.0, 100.0, 100.0, 0.0)
        with self.assertRaises(ValueError):
            self.evaluator.calculate_implementation_shortfall_bps(execution)


if __name__ == "__main__":
    unittest.main()
