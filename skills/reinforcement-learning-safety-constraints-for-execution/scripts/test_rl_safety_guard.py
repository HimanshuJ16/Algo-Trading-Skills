"""
Unit tests for reinforcement-learning-safety-constraints-for-execution.

Expected values are derived by hand in each test's comments rather than by re-running the
implementation's own arithmetic. Tests marked "REGRESSION" fail against the pre-audit
implementation and pass against the current one; the behaviour each one pins is named in
its docstring.

Run from this directory:  python -m unittest discover -s . -v
"""
import logging
import math
import unittest

from rl_safety_guard import (
    REASON_CUMULATIVE_BUDGET,
    REASON_DATA_INTEGRITY,
    REASON_HORIZON_EXPIRED,
    REASON_MAX_ORDER_SIZE,
    REASON_POSITION_CAP,
    REASON_SPREAD_VETO,
    REASON_TERMINAL_CLEARANCE,
    ExecutionState,
    RLSafetyError,
    SafeRLExecutionGuard,
)

# The guard logs a warning on every interception; most tests here intercept deliberately.
logging.getLogger("rl_safety_guard").setLevel(logging.CRITICAL)


def make_state(
    current_inventory=0.0,
    max_inventory=1000.0,
    bid=100.0,
    ask=100.10,
    time_remaining_sec=300.0,
    max_spread=0.50,
):
    """Tight spread (0.10), far from the terminal horizon, unless overridden."""
    return ExecutionState(
        current_inventory=current_inventory,
        max_inventory=max_inventory,
        bid=bid,
        ask=ask,
        time_remaining_sec=time_remaining_sec,
        max_spread=max_spread,
    )


class TestPassThrough(unittest.TestCase):
    """A compliant action must reach the router untouched and unpunished."""

    def setUp(self):
        self.guard = SafeRLExecutionGuard(max_order_size=100.0, penalty_lambda=10.0)

    def test_safe_action_passes_unmodified(self):
        action = self.guard.intercept_action(50.0, make_state(current_inventory=100.0), 10.0)

        self.assertFalse(action.is_intercepted)
        self.assertEqual(action.safe_qty, 50.0)
        self.assertEqual(action.shaped_reward, 10.0)
        self.assertEqual(action.penalty_applied, 0.0)
        self.assertEqual(action.reason_codes, ())
        self.assertIsNone(action.interception_reason)

    def test_exactly_at_max_order_size_is_not_intercepted(self):
        # 100.0 is not *greater than* max_order_size 100.0, so the clip must not fire.
        action = self.guard.intercept_action(100.0, make_state(), 0.0)
        self.assertFalse(action.is_intercepted)
        self.assertEqual(action.safe_qty, 100.0)

    def test_spread_exactly_at_limit_is_not_vetoed(self):
        # ask 100.50 - bid 100.00 = 0.50, exactly max_spread. Not "> max_spread".
        action = self.guard.intercept_action(10.0, make_state(ask=100.50), 0.0)
        self.assertFalse(action.is_intercepted)
        self.assertEqual(action.safe_qty, 10.0)

    def test_projected_inventory_exactly_at_cap_is_not_intercepted(self):
        # 950 + 50 = 1000, exactly the cap. Allowed.
        action = self.guard.intercept_action(
            50.0, make_state(current_inventory=950.0, max_inventory=1000.0), 0.0
        )
        self.assertFalse(action.is_intercepted)
        self.assertEqual(action.safe_qty, 50.0)


class TestMaxOrderSize(unittest.TestCase):
    def setUp(self):
        self.guard = SafeRLExecutionGuard(max_order_size=100.0, penalty_lambda=10.0)

    def test_oversized_buy_is_clipped(self):
        # Inventory 0, cap 1000: only the size clip can bind. 500 -> 100.
        action = self.guard.intercept_action(500.0, make_state(), 5.0)
        self.assertEqual(action.safe_qty, 100.0)
        self.assertEqual(action.reason_codes, (REASON_MAX_ORDER_SIZE,))
        self.assertEqual(action.shaped_reward, -5.0)  # 5.0 - 10.0

    def test_oversized_sell_keeps_its_sign(self):
        action = self.guard.intercept_action(-500.0, make_state(), 0.0)
        self.assertEqual(action.safe_qty, -100.0)
        self.assertEqual(action.reason_codes, (REASON_MAX_ORDER_SIZE,))


class TestPositionCap(unittest.TestCase):
    """REGRESSION: the pre-audit suite named a test for the position cap that in fact only
    exercised the max-order-size clip, so none of this behaviour was covered."""

    def test_cap_binds_only_when_it_actually_binds(self):
        # max_order_size 5000 keeps the size clip out of the way so the cap is isolated.
        # Inventory 800, cap 1000 -> at most +200 may be bought. Proposed +500.
        guard = SafeRLExecutionGuard(max_order_size=5000.0, penalty_lambda=10.0)
        action = guard.intercept_action(500.0, make_state(current_inventory=800.0), 5.0)

        self.assertEqual(action.safe_qty, 200.0)
        self.assertEqual(action.reason_codes, (REASON_POSITION_CAP,))
        self.assertEqual(action.shaped_reward, -5.0)

    def test_size_clip_and_cap_are_both_recorded(self):
        """REGRESSION: the old guard kept only the last reason string, losing the fact that
        two separate hard limits bound the same action."""
        # Inventory 990, cap 1000, max_order_size 100, proposed 4000.
        # Size clip: 4000 -> 100. Cap: 990 + 100 = 1090 > 1000, so 1000 - 990 = 10.
        guard = SafeRLExecutionGuard(max_order_size=100.0, penalty_lambda=10.0)
        action = guard.intercept_action(4000.0, make_state(current_inventory=990.0), 0.0)

        self.assertEqual(action.safe_qty, 10.0)
        self.assertEqual(action.reason_codes, (REASON_MAX_ORDER_SIZE, REASON_POSITION_CAP))
        # One penalty per intercepted step, not one per violated constraint.
        self.assertEqual(action.penalty_applied, 10.0)

    def test_short_side_cap_is_symmetric(self):
        # Inventory -800, cap 1000 -> at most -200 more may be sold.
        guard = SafeRLExecutionGuard(max_order_size=5000.0)
        action = guard.intercept_action(-500.0, make_state(current_inventory=-800.0), 0.0)
        self.assertEqual(action.safe_qty, -200.0)
        self.assertEqual(action.reason_codes, (REASON_POSITION_CAP,))

    def test_reducing_order_from_over_cap_position_is_allowed(self):
        """REGRESSION: the old cap formula computed `max_inventory - |inventory|`, clamped
        it at zero, and so returned 0 for *any* order once the position was already outside
        the cap -- including the order that would bring it back inside. A lowered limit, an
        external fill or a manual position could trap exposure the shield exists to shed."""
        guard = SafeRLExecutionGuard(max_order_size=5000.0)
        action = guard.intercept_action(
            -100.0, make_state(current_inventory=1200.0, max_inventory=1000.0), 0.0
        )
        # Strictly reduces 1200 -> 1100. Must pass untouched.
        self.assertEqual(action.safe_qty, -100.0)
        self.assertFalse(action.is_intercepted)

    def test_increasing_an_over_cap_position_is_still_blocked(self):
        guard = SafeRLExecutionGuard(max_order_size=5000.0)
        action = guard.intercept_action(
            50.0, make_state(current_inventory=1200.0, max_inventory=1000.0), 0.0
        )
        self.assertEqual(action.safe_qty, 0.0)
        self.assertEqual(action.reason_codes, (REASON_POSITION_CAP,))

    def test_sign_crossing_sell_is_clamped_to_the_band_not_to_the_headroom(self):
        """REGRESSION: the old formula sized a crossing order from the *same-side* headroom
        (`cap - |inventory|`), so a sell from +900 with a 1000 cap was cut to -100 when
        -1900 (down to the -1000 limit) was fully admissible -- a 95% under-execution."""
        guard = SafeRLExecutionGuard(max_order_size=5000.0)
        action = guard.intercept_action(-1950.0, make_state(current_inventory=900.0), 0.0)
        # Target 900 - 1950 = -1050, clamped to the -1000 floor -> order = -1000 - 900.
        self.assertEqual(action.safe_qty, -1900.0)
        self.assertEqual(action.reason_codes, (REASON_POSITION_CAP,))


class TestSpreadVeto(unittest.TestCase):
    def setUp(self):
        self.guard = SafeRLExecutionGuard(max_order_size=100.0, penalty_lambda=10.0)

    def test_wide_spread_vetoes_order(self):
        # Spread 2.50 > max_spread 1.00.
        action = self.guard.intercept_action(
            50.0, make_state(ask=102.50, max_spread=1.0), 2.0
        )
        self.assertEqual(action.safe_qty, 0.0)
        self.assertEqual(action.reason_codes, (REASON_SPREAD_VETO,))
        self.assertEqual(action.shaped_reward, -8.0)  # 2.0 - 10.0

    def test_wide_spread_with_zero_proposal_is_not_an_interception(self):
        # Nothing to veto: the policy already proposed no trade.
        action = self.guard.intercept_action(
            0.0, make_state(ask=102.50, max_spread=1.0), 2.0
        )
        self.assertFalse(action.is_intercepted)
        self.assertEqual(action.shaped_reward, 2.0)


class TestTerminalClearance(unittest.TestCase):
    """REGRESSION: the terminal-liquidation path -- the most safety-critical branch in the
    guard -- had no test at all before this audit."""

    def setUp(self):
        self.guard = SafeRLExecutionGuard(
            max_order_size=5000.0, penalty_lambda=10.0, terminal_horizon_sec=60.0
        )

    def test_inventory_is_force_liquidated_inside_the_horizon(self):
        action = self.guard.intercept_action(
            300.0, make_state(current_inventory=500.0, time_remaining_sec=30.0), 1.0
        )
        # The proposed +300 would *add* to a position that must be flat in 30s.
        self.assertEqual(action.safe_qty, -500.0)
        self.assertEqual(action.reason_codes, (REASON_TERMINAL_CLEARANCE,))
        self.assertEqual(action.shaped_reward, -9.0)

    def test_liquidation_is_clipped_to_max_order_size(self):
        guard = SafeRLExecutionGuard(max_order_size=100.0, terminal_horizon_sec=60.0)
        action = guard.intercept_action(
            0.0, make_state(current_inventory=500.0, time_remaining_sec=30.0), 0.0
        )
        # A single slice may not exceed the per-order limit even to meet the deadline.
        self.assertEqual(action.safe_qty, -100.0)

    def test_short_inventory_is_covered(self):
        action = self.guard.intercept_action(
            0.0, make_state(current_inventory=-250.0, time_remaining_sec=10.0), 0.0
        )
        self.assertEqual(action.safe_qty, 250.0)

    def test_horizon_boundary_is_inclusive(self):
        # Exactly at the 60s horizon the clearance fires; a hair above it does not.
        at = self.guard.intercept_action(
            0.0, make_state(current_inventory=500.0, time_remaining_sec=60.0), 0.0
        )
        self.assertEqual(at.safe_qty, -500.0)

        above = self.guard.intercept_action(
            0.0, make_state(current_inventory=500.0, time_remaining_sec=60.001), 0.0
        )
        self.assertFalse(above.is_intercepted)
        self.assertEqual(above.safe_qty, 0.0)

    def test_agent_proposing_the_correct_liquidation_is_not_penalised(self):
        action = self.guard.intercept_action(
            -500.0, make_state(current_inventory=500.0, time_remaining_sec=30.0), 1.0
        )
        self.assertFalse(action.is_intercepted)
        self.assertEqual(action.safe_qty, -500.0)
        self.assertEqual(action.shaped_reward, 1.0)

    def test_horizon_too_short_to_flatten_strands_inventory(self):
        """The guard liquidates at most `max_order_size` per step and never checks that the
        horizon leaves enough steps to finish. 800 units at 100/slice needs 8 slices; a 60s
        horizon polled every 30s offers 4 (t = 60, 30, 0, -30), so 400 units survive the
        deadline. Documented in `references/standards.md`, pinned here so the shortfall
        stays visible rather than being mistaken for a flat-at-deadline guarantee."""
        guard = SafeRLExecutionGuard(max_order_size=100.0, terminal_horizon_sec=60.0)
        inventory, t = 800.0, 600.0
        while t > -60.0:
            action = guard.intercept_action(
                0.0,
                make_state(current_inventory=inventory, time_remaining_sec=t),
                0.0,
            )
            inventory += action.safe_qty
            t -= 30.0
        self.assertEqual(inventory, 400.0)

    def test_flat_inventory_inside_horizon_does_not_trigger_clearance(self):
        action = self.guard.intercept_action(
            0.0, make_state(current_inventory=0.0, time_remaining_sec=10.0), 0.0
        )
        self.assertFalse(action.is_intercepted)


class TestGuardPrecedence(unittest.TestCase):
    """The routed quantity must depend on the state alone, never on what the policy
    happened to propose."""

    def test_terminal_clearance_overrides_spread_veto_by_default(self):
        guard = SafeRLExecutionGuard(max_order_size=5000.0)
        action = guard.intercept_action(
            0.0,
            make_state(
                current_inventory=500.0,
                time_remaining_sec=30.0,
                ask=102.50,
                max_spread=1.0,
            ),
            0.0,
        )
        # Carrying inventory past the deadline is the larger, unbounded risk.
        self.assertEqual(action.safe_qty, -500.0)
        self.assertEqual(action.reason_codes, (REASON_TERMINAL_CLEARANCE,))

    def test_spread_veto_wins_when_the_override_is_disabled(self):
        guard = SafeRLExecutionGuard(
            max_order_size=5000.0, terminal_clearance_overrides_spread_veto=False
        )
        action = guard.intercept_action(
            0.0,
            make_state(
                current_inventory=500.0,
                time_remaining_sec=30.0,
                ask=102.50,
                max_spread=1.0,
            ),
            0.0,
        )
        self.assertEqual(action.safe_qty, 0.0)
        self.assertEqual(
            action.reason_codes, (REASON_TERMINAL_CLEARANCE, REASON_SPREAD_VETO)
        )

    def test_routed_quantity_is_independent_of_the_proposed_quantity(self):
        """REGRESSION: in the old guard the spread veto and the terminal clearance were
        mutually exclusive branches, and which one ran depended on whether the policy
        proposed a non-zero quantity. Proposing 0 in a wide spread produced a forced
        500-share liquidation; proposing 50 in the identical state produced 0."""
        for overrides in (True, False):
            guard = SafeRLExecutionGuard(
                max_order_size=5000.0,
                terminal_clearance_overrides_spread_veto=overrides,
            )
            routed = set()
            for proposal in (0.0, 50.0, -500.0, -250.0, 900.0):
                state = make_state(
                    current_inventory=500.0,
                    time_remaining_sec=30.0,
                    ask=102.50,
                    max_spread=1.0,
                )
                routed.add(guard.intercept_action(proposal, state, 0.0).safe_qty)
            self.assertEqual(
                len(routed), 1, f"override={overrides}: routed quantity varied: {routed}"
            )


class TestExpiredHorizon(unittest.TestCase):
    """REGRESSION: with the window already closed and no inventory to clear, the old guard
    fell through to the ordinary branch and happily opened brand-new exposure after the
    parent order's deadline."""

    def setUp(self):
        self.guard = SafeRLExecutionGuard(max_order_size=5000.0, terminal_horizon_sec=60.0)

    def test_no_new_exposure_after_the_deadline(self):
        action = self.guard.intercept_action(
            500.0, make_state(current_inventory=0.0, time_remaining_sec=-10.0), 0.0
        )
        self.assertEqual(action.safe_qty, 0.0)
        self.assertEqual(action.reason_codes, (REASON_HORIZON_EXPIRED,))

    def test_reducing_order_after_the_deadline_may_not_overshoot_through_flat(self):
        # Inventory 500, proposed -600 would leave a fresh -100 short after the deadline.
        guard = SafeRLExecutionGuard(max_order_size=5000.0, terminal_horizon_sec=0.0)
        action = guard.intercept_action(
            -600.0, make_state(current_inventory=500.0, time_remaining_sec=-5.0), 0.0
        )
        self.assertEqual(action.safe_qty, -500.0)


class TestDataIntegrity(unittest.TestCase):
    """REGRESSION: every case here previously slipped through the guard untouched, because
    each comparison it relies on (`x > limit`) is False when x is NaN."""

    def setUp(self):
        self.guard = SafeRLExecutionGuard(max_order_size=100.0, penalty_lambda=10.0)

    def test_nan_proposal_is_vetoed_rather_than_routed(self):
        action = self.guard.intercept_action(float("nan"), make_state(), 5.0)
        self.assertEqual(action.safe_qty, 0.0)
        self.assertFalse(math.isnan(action.safe_qty))
        self.assertTrue(action.is_data_integrity_failure)
        self.assertEqual(action.reason_codes, (REASON_DATA_INTEGRITY,))

    def test_infinite_proposal_is_vetoed(self):
        action = self.guard.intercept_action(float("inf"), make_state(), 0.0)
        self.assertEqual(action.safe_qty, 0.0)
        self.assertTrue(action.is_data_integrity_failure)

    def test_nan_quote_does_not_silently_disable_the_spread_veto(self):
        action = self.guard.intercept_action(50.0, make_state(bid=float("nan")), 0.0)
        self.assertEqual(action.safe_qty, 0.0)
        self.assertEqual(action.reason_codes, (REASON_DATA_INTEGRITY,))

    def test_nan_inventory_is_vetoed(self):
        action = self.guard.intercept_action(
            50.0, make_state(current_inventory=float("nan")), 0.0
        )
        self.assertEqual(action.safe_qty, 0.0)
        self.assertTrue(action.is_data_integrity_failure)

    def test_crossed_book_is_treated_as_bad_data(self):
        # ask 100.00 < bid 100.50 -> spread -0.50, which is never "> max_spread".
        action = self.guard.intercept_action(50.0, make_state(bid=100.50, ask=100.0), 0.0)
        self.assertEqual(action.safe_qty, 0.0)
        self.assertEqual(action.reason_codes, (REASON_DATA_INTEGRITY,))

    def test_locked_book_is_tradeable(self):
        # ask == bid is a zero spread, not a crossed one: it passes the integrity gate.
        action = self.guard.intercept_action(50.0, make_state(bid=100.0, ask=100.0), 0.0)
        self.assertFalse(action.is_intercepted)
        self.assertEqual(action.safe_qty, 50.0)

    def test_data_integrity_veto_does_not_punish_the_policy(self):
        """The policy cannot control feed quality; charging it penalty_lambda for a broken
        quote poisons credit assignment for an action it never got to take."""
        action = self.guard.intercept_action(50.0, make_state(bid=float("nan")), 7.0)
        self.assertEqual(action.shaped_reward, 7.0)
        self.assertEqual(action.penalty_applied, 0.0)

    def test_all_problems_are_reported_together(self):
        action = self.guard.intercept_action(
            float("nan"), make_state(current_inventory=float("nan")), 0.0
        )
        self.assertIn("proposed quantity", action.interception_reason)
        self.assertIn("current_inventory", action.interception_reason)


class TestCumulativeBudget(unittest.TestCase):
    """Without a cumulative ceiling the per-order clip bounds one action but not what the
    policy accumulates by re-proposing it every step."""

    def test_slicing_around_the_per_order_clip_is_bounded(self):
        guard = SafeRLExecutionGuard(max_order_size=100.0, max_cumulative_qty=250.0)
        routed = [
            guard.intercept_action(100.0, make_state(max_inventory=1e9), 0.0).safe_qty
            for _ in range(5)
        ]
        self.assertEqual(routed, [100.0, 100.0, 50.0, 0.0, 0.0])
        self.assertEqual(sum(routed), 250.0)

    def test_budget_is_unconstrained_by_default(self):
        guard = SafeRLExecutionGuard(max_order_size=100.0)
        routed = [
            guard.intercept_action(100.0, make_state(max_inventory=1e9), 0.0).safe_qty
            for _ in range(5)
        ]
        self.assertEqual(sum(routed), 500.0)

    def test_budget_counts_both_directions(self):
        # Churn: buy 100, sell 100. Both consume the turnover budget.
        guard = SafeRLExecutionGuard(max_order_size=100.0, max_cumulative_qty=150.0)
        first = guard.intercept_action(100.0, make_state(max_inventory=1e9), 0.0)
        second = guard.intercept_action(-100.0, make_state(max_inventory=1e9), 0.0)
        self.assertEqual(first.safe_qty, 100.0)
        self.assertEqual(second.safe_qty, -50.0)
        self.assertEqual(second.reason_codes, (REASON_CUMULATIVE_BUDGET,))

    def test_exhausted_budget_never_strands_inventory_at_the_horizon(self):
        guard = SafeRLExecutionGuard(
            max_order_size=5000.0, max_cumulative_qty=0.0, terminal_horizon_sec=60.0
        )
        action = guard.intercept_action(
            0.0, make_state(current_inventory=500.0, time_remaining_sec=30.0), 0.0
        )
        self.assertEqual(action.safe_qty, -500.0)

    def test_reset_episode_restores_the_budget_but_keeps_lifetime_counters(self):
        guard = SafeRLExecutionGuard(max_order_size=100.0, max_cumulative_qty=100.0)
        guard.intercept_action(100.0, make_state(max_inventory=1e9), 0.0)
        self.assertEqual(guard.cumulative_qty_routed, 100.0)

        guard.reset_episode()
        self.assertEqual(guard.cumulative_qty_routed, 0.0)
        self.assertEqual(guard.total_actions_processed, 1)

        action = guard.intercept_action(100.0, make_state(max_inventory=1e9), 0.0)
        self.assertEqual(action.safe_qty, 100.0)
        self.assertEqual(guard.total_actions_processed, 2)


class TestConfigurationValidation(unittest.TestCase):
    """REGRESSION: none of these were checked, so a mis-typed limit produced a silently
    permissive shield rather than a startup failure."""

    def test_invalid_guard_arguments_raise(self):
        for kwargs in (
            {"max_order_size": 0.0},
            {"max_order_size": -100.0},
            {"max_order_size": float("nan")},
            {"max_order_size": float("inf")},
            {"penalty_lambda": -1.0},
            {"penalty_lambda": float("nan")},
            {"terminal_horizon_sec": -1.0},
            {"max_cumulative_qty": -1.0},
        ):
            with self.subTest(**kwargs):
                with self.assertRaises(RLSafetyError):
                    SafeRLExecutionGuard(**kwargs)

    def test_zero_penalty_lambda_is_allowed(self):
        # Shielding without punishing is a legitimate configuration.
        guard = SafeRLExecutionGuard(max_order_size=100.0, penalty_lambda=0.0)
        action = guard.intercept_action(500.0, make_state(), 5.0)
        self.assertTrue(action.is_intercepted)
        self.assertEqual(action.shaped_reward, 5.0)

    def test_invalid_execution_state_raises(self):
        for kwargs in (
            {"max_inventory": -1000.0},
            {"max_inventory": float("nan")},
            {"max_spread": -0.5},
            {"max_spread": float("inf")},
        ):
            with self.subTest(**kwargs):
                with self.assertRaises(RLSafetyError):
                    make_state(**kwargs)


class TestCountersAndTrainingContract(unittest.TestCase):
    def test_counters_and_interception_rate(self):
        guard = SafeRLExecutionGuard(max_order_size=100.0)
        self.assertEqual(guard.interception_rate, 0.0)

        guard.intercept_action(50.0, make_state(), 0.0)      # clean
        guard.intercept_action(500.0, make_state(), 0.0)     # clipped
        guard.intercept_action(500.0, make_state(), 0.0)     # clipped

        self.assertEqual(guard.total_actions_processed, 3)
        self.assertEqual(guard.total_actions_intercepted, 2)
        self.assertAlmostEqual(guard.interception_rate, 2 / 3)

    def test_proposed_qty_is_preserved_for_credit_assignment(self):
        """The punishment is attributed to the action the policy proposed, so that action
        must survive in the returned record for the replay buffer."""
        guard = SafeRLExecutionGuard(max_order_size=100.0, penalty_lambda=10.0)
        action = guard.intercept_action(750.0, make_state(), 3.0)

        self.assertEqual(action.proposed_qty, 750.0)
        self.assertEqual(action.safe_qty, 100.0)
        self.assertEqual(action.shaped_reward, 3.0 - 10.0)

    def test_no_negative_zero_is_emitted(self):
        """REGRESSION: a fully blocked *sell* used to return -0.0, which compares equal to
        0.0 but serialises as "-0.0" and reverses downstream sign checks such as
        `math.copysign(1, qty)` or `qty < 0` guards in a router's side mapping."""
        cases = {
            "position cap": (
                SafeRLExecutionGuard(max_order_size=5000.0),
                make_state(current_inventory=-1000.0),
            ),
            "cumulative budget": (
                SafeRLExecutionGuard(max_order_size=100.0, max_cumulative_qty=0.0),
                make_state(max_inventory=1e9),
            ),
            "expired horizon": (
                SafeRLExecutionGuard(max_order_size=5000.0),
                make_state(current_inventory=0.0, time_remaining_sec=-1.0),
            ),
        }
        for label, (guard, state) in cases.items():
            with self.subTest(label):
                action = guard.intercept_action(-50.0, state, 0.0)
                self.assertEqual(action.safe_qty, 0.0)
                self.assertEqual(math.copysign(1.0, action.safe_qty), 1.0)


if __name__ == "__main__":
    unittest.main()
