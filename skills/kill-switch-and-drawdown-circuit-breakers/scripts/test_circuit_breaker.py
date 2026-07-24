"""
Unit tests for kill-switch-and-drawdown-circuit-breakers skill.

Tests:
1. Position limit breach order veto.
2. Daily loss limit breach & automatic halt with force-flatten callback.
3. Max drawdown peak-equity high-water mark breach.
4. Broker position reconciliation desync detection.
5. Manual emergency kill switch trigger.
6. Mandatory human re-enable gate.
7. Backward compatibility with CircuitBreaker wrapper class.
"""
import unittest
from unittest.mock import Mock
from circuit_breaker import (
    CircuitBreaker,
    CircuitBreakerStatus,
    KillSwitchCircuitBreaker,
)


class TestKillSwitchCircuitBreaker(unittest.TestCase):

    def setUp(self):
        self.alerts = []
        self.flatten_calls = 0

        def mock_alert(msg):
            self.alerts.append(msg)

        def mock_flatten():
            self.flatten_calls += 1

        self.mock_alert = mock_alert
        self.mock_flatten = mock_flatten

        self.engine = KillSwitchCircuitBreaker(
            max_position=100.0,
            max_daily_loss=5000.0,
            max_drawdown_pct=0.10,  # 10% max drawdown
            alert_fn=self.mock_alert,
            flatten_fn=self.mock_flatten,
        )

    def test_position_limit_veto(self):
        # Current = 80, proposed = 30 -> 110 > 100 -> Veto!
        ok, reason = self.engine.check_proposed_order(
            proposed_position_size=30.0, current_position_size=80.0
        )
        self.assertFalse(ok)
        self.assertIn("exceeds max limit", reason)

    def test_daily_loss_breach_and_flatten(self):
        is_halted, status = self.engine.check_pnl_and_drawdown(
            daily_pnl=-5500.0, current_equity=100_000.0
        )
        self.assertTrue(is_halted)
        self.assertTrue(self.engine.halted)
        self.assertEqual(self.engine.status, CircuitBreakerStatus.HALTED_DAILY_LOSS)
        self.assertEqual(self.flatten_calls, 1)
        self.assertEqual(len(self.alerts), 1)

    def test_max_drawdown_high_watermark(self):
        # Establish peak equity at 100,000
        self.engine.check_pnl_and_drawdown(daily_pnl=0.0, current_equity=100_000.0)

        # Equity drops to 88,000 (12% drawdown > 10% limit)
        is_halted, status = self.engine.check_pnl_and_drawdown(
            daily_pnl=-1000.0, current_equity=88_000.0
        )
        self.assertTrue(is_halted)
        self.assertEqual(self.engine.status, CircuitBreakerStatus.HALTED_DRAWDOWN)
        self.assertEqual(self.flatten_calls, 1)

    def test_broker_position_reconciliation_desync(self):
        internal_pos = {"NIFTY": 50.0}
        broker_pos = {"NIFTY": 0.0}  # Fill status desync!

        is_reconciled = self.engine.reconcile_broker_positions(internal_pos, broker_pos)
        self.assertFalse(is_reconciled)
        self.assertTrue(self.engine.halted)
        self.assertEqual(self.engine.status, CircuitBreakerStatus.HALTED_DESYNC)

    def test_manual_kill_switch_and_human_reenable(self):
        self.engine.trigger_emergency_kill_switch("Unusual latency spike observed")
        self.assertTrue(self.engine.halted)

        # Attempt order while halted
        ok, reason = self.engine.check_proposed_order(10.0, 0.0)
        self.assertFalse(ok)

        # Human re-enable gate
        self.engine.human_re_enable(authorized_user="risk_mgr_alice", reason="Latency issue resolved")
        self.assertFalse(self.engine.halted)

        # Order now passes
        ok2, reason2 = self.engine.check_proposed_order(10.0, 0.0)
        self.assertTrue(ok2)

    def test_backward_compatibility(self):
        cb = CircuitBreaker(
            max_position=100,
            max_daily_loss=1000,
            max_drawdown_pct=0.05,
            alert_fn=self.mock_alert,
        )
        ok, r = cb.check_order(50, 60)
        self.assertFalse(ok)
        self.assertEqual(r, "position_limit")

        halted = cb.check_pnl(-1200, 10000)
        self.assertTrue(halted)


if __name__ == "__main__":
    unittest.main()
