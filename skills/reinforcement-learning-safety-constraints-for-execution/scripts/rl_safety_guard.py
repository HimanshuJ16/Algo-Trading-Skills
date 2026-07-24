"""
reinforcement-learning-safety-constraints-for-execution: Production-grade RL safety shield,
action-space clipper, spread veto guard, terminal inventory enforcement, and penalty reward shaper.
"""
from dataclasses import dataclass, field
import logging
import math
from typing import Any, Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)


class RLSafetyError(ValueError):
    """Raised when execution parameters are invalid."""
    pass


@dataclass
class ExecutionState:
    current_inventory: float
    max_inventory: float
    bid: float
    ask: float
    time_remaining_sec: float
    max_spread: float

    @property
    def spread(self) -> float:
        return self.ask - self.bid


@dataclass
class SafeAction:
    proposed_qty: float
    safe_qty: float
    is_intercepted: bool
    interception_reason: Optional[str]
    shaped_reward: float


class SafeRLExecutionGuard:
    """
    Action-space safety shield for Reinforcement Learning execution agents.
    Enforces deterministic risk limits, position caps, spread vetoes, and shapes penalty rewards.
    """

    def __init__(
        self,
        max_order_size: float = 100.0,
        penalty_lambda: float = 10.0,
        terminal_horizon_sec: float = 60.0,
    ):
        self.max_order_size = max_order_size
        self.penalty_lambda = penalty_lambda
        self.terminal_horizon_sec = terminal_horizon_sec
        self.total_actions_processed: int = 0
        self.total_actions_intercepted: int = 0

    def intercept_action(self, proposed_qty: float, state: ExecutionState, base_reward: float) -> SafeAction:
        """
        Intercepts raw RL action proposal and applies deterministic safety constraints.
        """
        self.total_actions_processed += 1
        is_intercepted = False
        reason = None
        safe_qty = proposed_qty

        # 1. Spread Veto Guard: Veto market orders during wide spreads
        if state.spread > state.max_spread and abs(proposed_qty) > 0:
            safe_qty = 0.0
            is_intercepted = True
            reason = f"SPREAD VETO: Spread ${state.spread:.2f} > max allowed ${state.max_spread:.2f}."

        # 2. Terminal Inventory Horizon Enforcement: Force liquidation if time running out
        elif state.time_remaining_sec <= self.terminal_horizon_sec and abs(state.current_inventory) > 0:
            # Override policy to liquidate remaining inventory
            required_liquidation = -state.current_inventory
            clipped_liquidation = math.copysign(min(abs(required_liquidation), self.max_order_size), required_liquidation)
            if safe_qty != clipped_liquidation:
                safe_qty = clipped_liquidation
                is_intercepted = True
                reason = f"TERMINAL INVENTORY CLEARANCE: Time remaining {state.time_remaining_sec:.1f}s <= {self.terminal_horizon_sec}s."

        else:
            # 3. Max Order Size Constraint
            if abs(safe_qty) > self.max_order_size:
                safe_qty = math.copysign(self.max_order_size, safe_qty)
                is_intercepted = True
                reason = f"MAX ORDER SIZE EXCEEDED: Proposed {proposed_qty} clipped to max order size {self.max_order_size}."

            # 4. Position Cap Limit Constraint
            projected_inventory = state.current_inventory + safe_qty
            if abs(projected_inventory) > state.max_inventory:
                allowed_space = state.max_inventory - abs(state.current_inventory)
                allowed_space = max(allowed_space, 0.0)
                safe_qty = math.copysign(allowed_space, safe_qty) if safe_qty != 0 else 0.0
                is_intercepted = True
                reason = f"POSITION CAP BREACH: Projected inventory {projected_inventory} > max cap {state.max_inventory}."

        # 5. Reward Penalty Shaping
        shaped_reward = base_reward
        if is_intercepted:
            self.total_actions_intercepted += 1
            shaped_reward -= self.penalty_lambda
            logger.warning(f"RL Action Intercepted ({reason}) | Proposed: {proposed_qty} -> Safe: {safe_qty}")

        return SafeAction(
            proposed_qty=proposed_qty,
            safe_qty=safe_qty,
            is_intercepted=is_intercepted,
            interception_reason=reason,
            shaped_reward=shaped_reward,
        )
