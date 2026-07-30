import unittest
from execution_algorithm_kill_switch_integration import (
    ExecutionAlgoKillSwitchEngine, FirmRiskLimits, ActiveStrategyOrder, NewOrderRequest, KillSwitchAuditReport
)

class TestExecutionAlgoKillSwitchEngine(unittest.TestCase):

    def setUp(self):
        limits = FirmRiskLimits(
            max_daily_loss_usd=10_000.0,
            max_order_rate_per_sec=100,
            max_net_exposure_usd=1_000_000.0
        )
        self.engine = ExecutionAlgoKillSwitchEngine(limits)
        
        # Register 2 active strategy orders
        self.engine.register_active_order(ActiveStrategyOrder("ORD_01", "STRAT_ALPHA", "AAPL", "BUY", 100, 185.0, "NEW"))
        self.engine.register_active_order(ActiveStrategyOrder("ORD_02", "STRAT_ALPHA", "MSFT", "BUY", 50, 400.0, "NEW"))

    def test_global_kill_switch_cancels_all_orders_and_blocks(self):
        # Trigger Global Kill Switch
        report = self.engine.trigger_kill_switch("GLOBAL", "MANUAL_OPERATOR_OVERRIDE")
        
        self.assertTrue(report.is_kill_switch_active)
        self.assertEqual(report.status, "KILL_SWITCH_ENGAGED")
        self.assertEqual(report.cancelled_orders_count, 2)
        self.assertEqual(report.fix_mass_cancel_tag_530, "7_CANCEL_ALL_ORDERS")
        self.assertTrue(self.engine.is_global_kill_switch_engaged)

        # Submit new order -> REJECTED!
        req = NewOrderRequest("ORD_NEW_01", "STRAT_ALPHA", "AAPL", "BUY", 100, 185.0)
        audit_rep = self.engine.audit_and_validate_new_order(req, current_daily_loss_usd=0.0, current_order_rate_sec=5)
        self.assertEqual(audit_rep.status, "REJECTED_KILL_SWITCH_ACTIVE")
        self.assertTrue(audit_rep.is_new_order_blocked)

    def test_max_daily_loss_breach_triggers_kill_switch(self):
        # Daily loss = $12,500 >= $10,000 limit -> KILL SWITCH ENGAGED!
        req = NewOrderRequest("ORD_NEW_02", "STRAT_BETA", "GOOGL", "BUY", 100, 150.0)
        report = self.engine.audit_and_validate_new_order(req, current_daily_loss_usd=12_500.0, current_order_rate_sec=10)
        
        self.assertTrue(report.is_kill_switch_active)
        self.assertEqual(report.status, "KILL_SWITCH_ENGAGED")
        self.assertIn("MAX DAILY LOSS BREACH", report.trigger_reason)

if __name__ == '__main__':
    unittest.main()
