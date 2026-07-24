"""
Unit tests for reinforcement-learning-safety-constraints-for-execution skill.

Tests:
1. Max order size clipping.
2. Position cap limit enforcement.
3. Wide spread veto guard.
4. Reward penalty shaping upon interception.
"""
import unittest
from rl_safety_guard import ExecutionState, SafeAction, SafeRLExecutionGuard


class TestSafeRLExecutionGuard(unittest.TestCase):

    def setUp(self):
        self.guard = SafeRLExecutionGuard(max_order_size=100.0, penalty_lambda=10.0)

    def test_position_cap_clipping(self):
        state = ExecutionState(
            current_inventory=800.0,
            max_inventory=1000.0,
            bid=100.0,
            ask=100.1,
            time_remaining_sec=300.0,
            max_spread=0.5,
        )
        # Proposed order of +500 would push inventory to 1300 (> 1000 cap)
        action = self.guard.intercept_action(proposed_qty=500.0, state=state, base_reward=5.0)

        self.assertTrue(action.is_intercepted)
        self.assertEqual(action.safe_qty, 100.0)  # Clipped to max order size 100 (which fits in 200 space)
        self.assertEqual(action.shaped_reward, -5.0)  # 5.0 - 10.0 = -5.0

    def test_spread_veto(self):
        state = ExecutionState(
            current_inventory=0.0,
            max_inventory=1000.0,
            bid=100.0,
            ask=102.5,  # Spread 2.5 > max 1.0
            time_remaining_sec=300.0,
            max_spread=1.0,
        )
        action = self.guard.intercept_action(proposed_qty=50.0, state=state, base_reward=2.0)

        self.assertTrue(action.is_intercepted)
        self.assertEqual(action.safe_qty, 0.0)
        self.assertIn("SPREAD VETO", action.interception_reason)

    def test_safe_action_passes_unmodified(self):
        state = ExecutionState(
            current_inventory=100.0,
            max_inventory=1000.0,
            bid=100.0,
            ask=100.1,
            time_remaining_sec=300.0,
            max_spread=0.5,
        )
        action = self.guard.intercept_action(proposed_qty=50.0, state=state, base_reward=10.0)

        self.assertFalse(action.is_intercepted)
        self.assertEqual(action.safe_qty, 50.0)
        self.assertEqual(action.shaped_reward, 10.0)


if __name__ == "__main__":
    unittest.main()
