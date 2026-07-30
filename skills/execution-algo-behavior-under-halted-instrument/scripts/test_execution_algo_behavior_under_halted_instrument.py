import unittest
from execution_algo_behavior_under_halted_instrument import (
    ExecutionAlgoHaltEngine, ActiveChildOrder, ParentAlgoInstanceState, AlgoHaltAuditReport
)

class TestExecutionAlgoHaltEngine(unittest.TestCase):

    def setUp(self):
        self.engine = ExecutionAlgoHaltEngine()

    def test_halt_event_cancels_children_and_pauses_algo(self):
        # TWAP Parent Algo (10,000 shares total, 4,000 executed) with 2 resting child orders
        algo = ParentAlgoInstanceState(
            parent_algo_id="PARENT_TWAP_01", symbol="TSLA", algo_type="TWAP",
            total_target_qty=10_000, executed_qty=4_000, algo_state="RUNNING",
            active_child_orders=[
                ActiveChildOrder("CHILD_01", "NASDAQ", "BUY", 250.0, 500, "RESTING"),
                ActiveChildOrder("CHILD_02", "EDGX", "BUY", 249.9, 500, "RESTING")
            ]
        )

        report = self.engine.handle_trading_status_change(algo, "HALTED_LULD")
        
        self.assertEqual(report.previous_algo_state, "RUNNING")
        self.assertEqual(report.new_algo_state, "PAUSED_HALTED")
        self.assertEqual(report.cancelled_child_orders_count, 2)
        self.assertFalse(report.is_slicing_active)
        self.assertEqual(algo.active_child_orders[0].status, "CANCELLED")

    def test_resumption_event_resumes_slicing(self):
        algo = ParentAlgoInstanceState(
            parent_algo_id="PARENT_TWAP_01", symbol="TSLA", algo_type="TWAP",
            total_target_qty=10_000, executed_qty=4_000, algo_state="PAUSED_HALTED",
            active_child_orders=[]
        )

        report = self.engine.handle_trading_status_change(algo, "TRADING_CONTINUOUS")
        
        self.assertEqual(report.previous_algo_state, "PAUSED_HALTED")
        self.assertEqual(report.new_algo_state, "RUNNING")
        self.assertTrue(report.is_slicing_active)
        self.assertEqual(report.remaining_qty, 6_000)

if __name__ == '__main__':
    unittest.main()
