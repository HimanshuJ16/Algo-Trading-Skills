import unittest
import time
from capital_preservation_engine import (
    CapitalPreservationEngine,
    PreservationLimits,
    EngineState
)

class TestCapitalPreservationEngine(unittest.TestCase):
    
    def setUp(self):
        self.limits = PreservationLimits(
            max_daily_drawdown_usd=10000.0,
            max_orders_per_minute=5,
            max_consecutive_errors=3
        )
        self.engine = CapitalPreservationEngine(self.limits)

    def test_normal_operation(self):
        self.assertTrue(self.engine.check_order_allowed())
        self.engine.update_pnl(5000.0, -1000.0) # Net +4000
        self.assertTrue(self.engine.check_order_allowed())
        self.assertEqual(self.engine.state, EngineState.ACTIVE)

    def test_drawdown_kill_switch(self):
        # Massive unrealized loss
        self.engine.update_pnl(-2000.0, -9000.0) # Net -11000
        
        self.assertEqual(self.engine.state, EngineState.HALTED)
        self.assertFalse(self.engine.check_order_allowed())
        self.assertIn("Daily drawdown", self.engine.halt_reason)

    def test_runaway_algo_protection(self):
        # Limit is 5 orders per minute
        for _ in range(5):
            self.assertTrue(self.engine.check_order_allowed())
            
        # 6th order should trigger halt
        self.assertFalse(self.engine.check_order_allowed())
        self.assertEqual(self.engine.state, EngineState.HALTED)
        self.assertIn("Runaway Algo", self.engine.halt_reason)

    def test_consecutive_errors_degradation(self):
        self.engine.register_error()
        self.engine.register_error()
        self.assertEqual(self.engine.state, EngineState.ACTIVE)
        
        # 3rd error triggers halt
        self.engine.register_error()
        self.assertEqual(self.engine.state, EngineState.HALTED)
        self.assertFalse(self.engine.check_order_allowed())
        
    def test_error_reset_on_success(self):
        self.engine.register_error()
        self.engine.register_error()
        self.engine.register_success() # Resets counter to 0
        self.engine.register_error()
        
        self.assertEqual(self.engine.state, EngineState.ACTIVE)

    def test_manual_reset_auth(self):
        self.engine.register_error()
        self.engine.register_error()
        self.engine.register_error()
        self.assertEqual(self.engine.state, EngineState.HALTED)
        
        with self.assertRaises(PermissionError):
            self.engine.manual_reset("INVALID_TOKEN")
            
        self.engine.manual_reset("SECURE_ADMIN_TOKEN")
        self.assertEqual(self.engine.state, EngineState.ACTIVE)

if __name__ == '__main__':
    unittest.main()
