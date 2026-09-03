import unittest

from algo_wheel_broker_execution_quality_comparison import (
    ALLOCATION_TOLERANCE,
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

    def test_four_broker_allocation_sums_to_one_exactly(self):
        # 1.0 - 0.1 * 3 leaves 0.7; naively summing 0.7 + 0.1 + 0.1 + 0.1 gives
        # 0.9999999999999999, so the leader share must absorb the residual.
        executions = [
            BrokerExecution("BROKER_A", "BUY", 100.0, 100.0, 100.0, 0.0),
            BrokerExecution("BROKER_B", "BUY", 100.0, 100.5, 100.0, 0.0),
            BrokerExecution("BROKER_C", "BUY", 100.0, 101.0, 100.0, 0.0),
            BrokerExecution("BROKER_D", "BUY", 100.0, 102.0, 100.0, 0.0),
        ]
        allocations = self.evaluator.evaluate_brokers(executions)
        self.assertEqual(sum(allocations.values()), 1.0)
        self.assertEqual(allocations["BROKER_B"], 0.1)
        self.assertAlmostEqual(allocations["BROKER_A"], 0.7)

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

    def test_overflowing_decision_notional_is_rejected(self):
        # Both factors are finite but their product overflows to +inf, which
        # would otherwise silently collapse the weighted average to nan.
        execution = BrokerExecution("BROKER_A", "BUY", 1e308, 1e308, 1e308, 0.0)
        with self.assertRaises(ValueError):
            self.evaluator.evaluate_brokers([execution])

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

    def test_leader_may_not_receive_less_than_the_canary_floor(self):
        # 1.0 - 0.3 * 3 = 0.1: the best broker would be routed less flow than
        # each of the three brokers it beat, inverting the ranking.
        evaluator = AlgoWheelEvaluator(min_allocation=0.3)
        executions = [
            BrokerExecution("BROKER_A", "BUY", 100.0, 100.0, 100.0, 0.0),
            BrokerExecution("BROKER_B", "BUY", 100.0, 100.5, 100.0, 0.0),
            BrokerExecution("BROKER_C", "BUY", 100.0, 101.0, 100.0, 0.0),
            BrokerExecution("BROKER_D", "BUY", 100.0, 102.0, 100.0, 0.0),
        ]
        with self.assertRaises(ValueError):
            evaluator.evaluate_brokers(executions)

    def test_blank_broker_is_rejected(self):
        execution = BrokerExecution(" ", "BUY", 100.0, 100.0, 100.0, 0.0)
        with self.assertRaises(ValueError):
            self.evaluator.calculate_implementation_shortfall_bps(execution)

    def test_rank_brokers_reports_sample_and_notional_evidence(self):
        executions = [
            BrokerExecution("BROKER_A", "BUY", 100.0, 100.0, 1.0, 0.0),
            BrokerExecution("BROKER_A", "BUY", 100.0, 101.0, 100.0, 0.0),
            BrokerExecution("BROKER_B", "BUY", 100.0, 100.5, 100.0, 0.0),
        ]
        ranked = self.evaluator.rank_brokers(executions)
        self.assertEqual([score.broker_id for score in ranked], ["BROKER_B", "BROKER_A"])

        broker_a = ranked[1]
        self.assertEqual(broker_a.execution_count, 2)
        self.assertEqual(broker_a.decision_notional, 10100.0)
        # 100 bps on 10,000 of notional and 0 bps on 100 => 10,000 / 10,100.
        self.assertAlmostEqual(broker_a.average_shortfall_bps, 100.0 * 10000.0 / 10100.0)
        self.assertAlmostEqual(ranked[0].average_shortfall_bps, 50.0)

    def test_thin_sample_broker_is_not_promoted_to_lead(self):
        evaluator = AlgoWheelEvaluator(min_allocation=0.10, min_observations=2)
        executions = [
            # One lucky 1-share fill: best score, but a single observation.
            BrokerExecution("BROKER_A", "BUY", 100.0, 99.0, 1.0, 0.0),
            BrokerExecution("BROKER_B", "BUY", 100.0, 100.5, 100.0, 0.0),
            BrokerExecution("BROKER_B", "BUY", 100.0, 100.5, 100.0, 0.0),
        ]
        allocations = evaluator.evaluate_brokers(executions)
        self.assertEqual(allocations["BROKER_B"], 0.9)
        self.assertEqual(allocations["BROKER_A"], 0.1)

    def test_notional_floor_also_blocks_promotion(self):
        evaluator = AlgoWheelEvaluator(min_allocation=0.10, min_notional=5000.0)
        executions = [
            BrokerExecution("BROKER_A", "BUY", 100.0, 99.0, 1.0, 0.0),
            BrokerExecution("BROKER_B", "BUY", 100.0, 100.5, 100.0, 0.0),
        ]
        allocations = evaluator.evaluate_brokers(executions)
        self.assertEqual(allocations["BROKER_B"], 0.9)
        self.assertEqual(allocations["BROKER_A"], 0.1)

    def test_no_eligible_broker_falls_back_to_equal_weights(self):
        evaluator = AlgoWheelEvaluator(min_allocation=0.10, min_observations=5)
        executions = [
            BrokerExecution("BROKER_A", "BUY", 100.0, 99.0, 100.0, 0.0),
            BrokerExecution("BROKER_B", "BUY", 100.0, 100.5, 100.0, 0.0),
            BrokerExecution("BROKER_C", "BUY", 100.0, 101.0, 100.0, 0.0),
        ]
        with self.assertLogs(
            "algo_wheel_broker_execution_quality_comparison", level="WARNING"
        ):
            allocations = evaluator.evaluate_brokers(executions)
        self.assertEqual(set(allocations), {"BROKER_A", "BROKER_B", "BROKER_C"})
        self.assertEqual(sum(allocations.values()), 1.0)
        for share in allocations.values():
            self.assertAlmostEqual(share, 1.0 / 3.0)

    def test_invalid_sufficiency_settings_are_rejected(self):
        with self.assertRaises(ValueError):
            AlgoWheelEvaluator(min_observations=0)
        with self.assertRaises(ValueError):
            AlgoWheelEvaluator(min_notional=-1.0)
        with self.assertRaises(TypeError):
            AlgoWheelEvaluator(min_observations=2.5)

    def test_allocations_sum_to_one_within_tolerance_for_many_brokers(self):
        evaluator = AlgoWheelEvaluator(min_allocation=0.05)
        executions = [
            BrokerExecution(f"BROKER_{index:02d}", "BUY", 100.0, 100.0 + index, 100.0, 0.0)
            for index in range(10)
        ]
        allocations = evaluator.evaluate_brokers(executions)
        self.assertEqual(len(allocations), 10)
        self.assertLessEqual(abs(sum(allocations.values()) - 1.0), ALLOCATION_TOLERANCE)
        self.assertAlmostEqual(allocations["BROKER_00"], 1.0 - 0.05 * 9)


if __name__ == "__main__":
    unittest.main()
