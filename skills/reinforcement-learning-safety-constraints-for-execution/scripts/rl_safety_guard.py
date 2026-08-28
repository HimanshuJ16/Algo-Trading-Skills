"""
reinforcement-learning-safety-constraints-for-execution.

A deterministic *post-posed shield* (Alshiekh et al., AAAI 2018) that sits between a
reinforcement-learning execution policy and the order router. It monitors the action the
policy proposes and corrects it only when that action would violate a hard constraint,
then optionally assigns a punishment so the policy learns that the *proposed* action was
unsafe.

Design rules the rest of this module depends on:

* **Fail closed.** Any input the shield cannot evaluate (non-finite quantity, non-finite
  or crossed market data) produces ``safe_qty = 0.0``. The shield never forwards a
  quantity it could not check.
* **Reduction is always permitted.** A constraint may shrink exposure, never trap it. An
  inventory already outside the cap -- a lowered limit, an external fill, a manual
  position -- must still be reducible.
* **One published precedence.** Guards are applied in a fixed documented order, never
  conditionally on what the policy happened to propose.
* **Every correction is attributable.** ``reason_codes`` records *all* constraints that
  bound the action, not just the last one, so an interception can be reconstructed for
  real-time monitoring and post-trade review.

This module is a strategy-side guardrail. It is **not** a broker's market-access control
and does not satisfy SEC Rule 15c3-5, whose controls must be under the "direct and
exclusive control of the broker or dealer" (17 CFR 240.15c3-5(d)(1)). See
``references/standards.md``.
"""
from __future__ import annotations

import logging
import math
from dataclasses import dataclass
from typing import Final, List, Optional, Tuple

logger = logging.getLogger(__name__)

# --- Reason codes -----------------------------------------------------------------
# Stable identifiers for audit trails. Human-readable text lives in `interception_reason`;
# machine consumers should switch on these codes, which are part of the public API.
REASON_DATA_INTEGRITY: Final[str] = "DATA_INTEGRITY"
REASON_TERMINAL_CLEARANCE: Final[str] = "TERMINAL_CLEARANCE"
REASON_SPREAD_VETO: Final[str] = "SPREAD_VETO"
REASON_MAX_ORDER_SIZE: Final[str] = "MAX_ORDER_SIZE"
REASON_POSITION_CAP: Final[str] = "POSITION_CAP"
REASON_HORIZON_EXPIRED: Final[str] = "HORIZON_EXPIRED"
REASON_CUMULATIVE_BUDGET: Final[str] = "CUMULATIVE_BUDGET"


class RLSafetyError(ValueError):
    """Raised when guard or execution-state *configuration* is invalid.

    Configuration errors are programmer errors and must surface at construction, before
    any order is routed. Bad *market data* is a runtime condition, handled by the
    fail-closed data-integrity gate rather than by raising into the RL step loop.
    """


@dataclass
class ExecutionState:
    """Market and inventory context for a single RL decision step.

    Args:
        current_inventory: Signed position currently held. Positive is long.
        max_inventory: Symmetric absolute position cap; ``|inventory| <= max_inventory``.
        bid: Best bid price.
        ask: Best ask price.
        time_remaining_sec: Seconds left in the parent order's execution window. May be
            zero or negative, meaning the window has already closed.
        max_spread: Widest quoted spread, in price units, at which the policy may trade.

    Raises:
        RLSafetyError: If ``max_inventory`` or ``max_spread`` is negative or non-finite.
            These are configuration values, not observations, so they fail loudly.
    """

    current_inventory: float
    max_inventory: float
    bid: float
    ask: float
    time_remaining_sec: float
    max_spread: float

    def __post_init__(self) -> None:
        if not math.isfinite(self.max_inventory) or self.max_inventory < 0:
            raise RLSafetyError(
                f"max_inventory must be a finite non-negative number, got {self.max_inventory!r}"
            )
        if not math.isfinite(self.max_spread) or self.max_spread < 0:
            raise RLSafetyError(
                f"max_spread must be a finite non-negative number, got {self.max_spread!r}"
            )

    @property
    def spread(self) -> float:
        """Quoted spread. Negative means a crossed book, i.e. unusable market data."""
        return self.ask - self.bid


@dataclass
class SafeAction:
    """Outcome of shielding one proposed action.

    Attributes:
        proposed_qty: The policy's raw output. **This is the action the punishment is
            attributed to.** Store this -- not ``safe_qty`` -- in the replay buffer
            alongside ``shaped_reward``, or the agent learns that the safe action was bad.
        safe_qty: The quantity actually cleared for routing.
        is_intercepted: True when the shield altered the proposed action.
        interception_reason: Human-readable summary of every constraint that bound the
            action, or ``None``.
        shaped_reward: ``base_reward`` less ``penalty_applied``.
        reason_codes: Stable codes for all constraints that bound the action, in the
            order applied.
        is_data_integrity_failure: True when the shield could not evaluate its inputs and
            failed closed. Such steps are caused by the environment, not the policy --
            exclude them from training rather than learning from them.
        penalty_applied: Penalty deducted from ``base_reward``. Zero for data-integrity
            failures.
    """

    proposed_qty: float
    safe_qty: float
    is_intercepted: bool
    interception_reason: Optional[str]
    shaped_reward: float
    reason_codes: Tuple[str, ...] = ()
    is_data_integrity_failure: bool = False
    penalty_applied: float = 0.0


class SafeRLExecutionGuard:
    """Deterministic post-posed safety shield for an RL execution policy.

    Guards are applied in this fixed order; every step passes through all of them:

    0. **Data integrity** -- non-finite action or market data, or a crossed book, vetoes
       to zero with no penalty.
    1. **Terminal inventory clearance** -- inside the terminal horizon with open
       inventory, the policy is overridden with a liquidating order.
    2. **Spread veto** -- a quoted spread wider than ``max_spread`` vetoes the order,
       unless a terminal clearance is in force and
       ``terminal_clearance_overrides_spread_veto`` is set.
    3. **Max order size** -- per-action quantity clip.
    4. **Position cap** -- clamps *projected* inventory into the admissible band, which is
       widened to the current inventory so a reducing order is never blocked.
    5. **Cumulative quantity budget** -- optional per-episode traded-quantity ceiling that
       closes the "slice around the per-order clip" path. Forced terminal clearance is
       exempt so the budget can never strand inventory.
    6. **Reward shaping** -- one ``penalty_lambda`` deduction per intercepted step.

    Args:
        max_order_size: Maximum absolute quantity per action. Must be finite and positive.
        penalty_lambda: Penalty deducted from the step reward on interception. Must be
            finite and non-negative. Set to 0.0 to shield without punishing -- see
            ``references/standards.md`` for the trade-off.
        terminal_horizon_sec: Seconds of remaining window at or below which open inventory
            is force-liquidated. Must be finite and non-negative.
        terminal_clearance_overrides_spread_veto: When True (default), a forced liquidation
            crosses a wide spread rather than carry inventory past the execution deadline.
            Set False to prefer carrying the inventory.
        max_cumulative_qty: Optional ceiling on total absolute quantity routed per episode.
            ``None`` (default) leaves cumulative activity **unconstrained** -- the
            per-action clip alone does not bound what an agent accumulates by slicing
            across steps.

    Raises:
        RLSafetyError: If any argument is outside its documented domain.
    """

    def __init__(
        self,
        max_order_size: float = 100.0,
        penalty_lambda: float = 10.0,
        terminal_horizon_sec: float = 60.0,
        terminal_clearance_overrides_spread_veto: bool = True,
        max_cumulative_qty: Optional[float] = None,
    ) -> None:
        if not math.isfinite(max_order_size) or max_order_size <= 0:
            raise RLSafetyError(
                f"max_order_size must be a finite positive number, got {max_order_size!r}"
            )
        if not math.isfinite(penalty_lambda) or penalty_lambda < 0:
            raise RLSafetyError(
                f"penalty_lambda must be a finite non-negative number, got {penalty_lambda!r}"
            )
        if not math.isfinite(terminal_horizon_sec) or terminal_horizon_sec < 0:
            raise RLSafetyError(
                "terminal_horizon_sec must be a finite non-negative number, "
                f"got {terminal_horizon_sec!r}"
            )
        if max_cumulative_qty is not None and (
            not math.isfinite(max_cumulative_qty) or max_cumulative_qty < 0
        ):
            raise RLSafetyError(
                "max_cumulative_qty must be None or a finite non-negative number, "
                f"got {max_cumulative_qty!r}"
            )

        self.max_order_size = float(max_order_size)
        self.penalty_lambda = float(penalty_lambda)
        self.terminal_horizon_sec = float(terminal_horizon_sec)
        self.terminal_clearance_overrides_spread_veto = bool(
            terminal_clearance_overrides_spread_veto
        )
        self.max_cumulative_qty = (
            None if max_cumulative_qty is None else float(max_cumulative_qty)
        )

        self.total_actions_processed: int = 0
        self.total_actions_intercepted: int = 0
        self.cumulative_qty_routed: float = 0.0

    # -- episode state -------------------------------------------------------------
    def reset_episode(self) -> None:
        """Clear per-episode state. Call between parent orders.

        Resets the cumulative-quantity budget only. Lifetime counters
        (``total_actions_processed`` / ``total_actions_intercepted``) are deliberately
        preserved so interception rates stay measurable across a whole training run.
        """
        self.cumulative_qty_routed = 0.0

    @property
    def interception_rate(self) -> float:
        """Fraction of processed actions that were intercepted. 0.0 before any action."""
        if self.total_actions_processed == 0:
            return 0.0
        return self.total_actions_intercepted / self.total_actions_processed

    # -- internals -----------------------------------------------------------------
    @staticmethod
    def _data_integrity_problems(proposed_qty: float, state: ExecutionState) -> List[str]:
        """Return every reason these inputs cannot be safely evaluated."""
        problems: List[str] = []
        if not math.isfinite(proposed_qty):
            problems.append(f"non-finite proposed quantity {proposed_qty!r}")
        for label, value in (
            ("current_inventory", state.current_inventory),
            ("bid", state.bid),
            ("ask", state.ask),
            ("time_remaining_sec", state.time_remaining_sec),
        ):
            if not math.isfinite(value):
                problems.append(f"non-finite {label} {value!r}")
        # A crossed book (ask < bid) is unusable: the spread test `spread > max_spread`
        # silently passes for a negative spread, disabling the veto exactly when the market
        # data is least trustworthy. Exchange rules must require members to reasonably
        # avoid displaying locked or crossed quotations (17 CFR 242.610(e)), so a crossed
        # top-of-book is treated as bad data rather than as a tradeable spread.
        if math.isfinite(state.bid) and math.isfinite(state.ask) and state.ask < state.bid:
            problems.append(f"crossed book: ask {state.ask!r} < bid {state.bid!r}")
        return problems

    def _target_inventory_band(self, state: ExecutionState) -> Tuple[float, float]:
        """Admissible projected-inventory band ``(lo, hi)`` for this step.

        The band is the symmetric position cap **widened to include the current
        inventory**. Widening is what makes an over-cap position reducible: clamping a
        1200-share position to a 1000-share cap would otherwise reject the very order that
        brings it back inside the limit.

        After the execution deadline (``time_remaining_sec <= 0``) the band tightens to the
        span between flat and the current inventory, so no new exposure can be opened and a
        reducing order cannot overshoot through zero.
        """
        inventory = state.current_inventory
        cap = state.max_inventory
        lo = min(-cap, inventory)
        hi = max(cap, inventory)
        if state.time_remaining_sec <= 0.0:
            lo = max(lo, min(0.0, inventory))
            hi = min(hi, max(0.0, inventory))
        return lo, hi

    def intercept_action(
        self,
        proposed_qty: float,
        state: ExecutionState,
        base_reward: float,
    ) -> SafeAction:
        """Shield one proposed action.

        Args:
            proposed_qty: Signed quantity the RL policy proposes. Positive is a buy.
            state: Market and inventory context for this step.
            base_reward: Unshaped environment reward for this step.

        Returns:
            A :class:`SafeAction`. Route ``safe_qty``; train on ``proposed_qty`` paired
            with ``shaped_reward``.
        """
        self.total_actions_processed += 1
        codes: List[str] = []
        reasons: List[str] = []

        # -- 0. Data integrity: fail closed, and do not punish the policy for it -------
        problems = self._data_integrity_problems(proposed_qty, state)
        if problems:
            reason = "DATA INTEGRITY VETO: " + "; ".join(problems) + "."
            logger.error(
                "RL action vetoed on unusable inputs | proposed=%r | %s", proposed_qty, reason
            )
            self.total_actions_intercepted += 1
            return SafeAction(
                proposed_qty=proposed_qty,
                safe_qty=0.0,
                is_intercepted=True,
                interception_reason=reason,
                shaped_reward=base_reward,
                reason_codes=(REASON_DATA_INTEGRITY,),
                is_data_integrity_failure=True,
                penalty_applied=0.0,
            )

        safe_qty = float(proposed_qty)

        # -- 1. Terminal inventory clearance -------------------------------------------
        # `terminal_clearance_active` is a property of the *state*, never of what the
        # policy happened to propose. Deriving it from "did we have to correct the action"
        # would suspend the spread veto for an agent that proposed nothing while applying
        # it to an agent that proposed the correct liquidation -- exactly backwards, and
        # non-deterministic from the shield's point of view.
        terminal_clearance_active = (
            state.time_remaining_sec <= self.terminal_horizon_sec
            and state.current_inventory != 0.0
        )
        if terminal_clearance_active:
            liquidation = -state.current_inventory
            target = math.copysign(min(abs(liquidation), self.max_order_size), liquidation)
            if not math.isclose(safe_qty, target, rel_tol=1e-9, abs_tol=1e-12):
                safe_qty = target
                codes.append(REASON_TERMINAL_CLEARANCE)
                reasons.append(
                    f"TERMINAL INVENTORY CLEARANCE: {state.time_remaining_sec:.1f}s remaining"
                    f" <= {self.terminal_horizon_sec:.1f}s horizon; forcing liquidation of"
                    f" {state.current_inventory:g} inventory."
                )

        # -- 2. Spread veto -------------------------------------------------------------
        spread_suspended = (
            terminal_clearance_active and self.terminal_clearance_overrides_spread_veto
        )
        if not spread_suspended and state.spread > state.max_spread and safe_qty != 0.0:
            safe_qty = 0.0
            codes.append(REASON_SPREAD_VETO)
            reasons.append(
                f"SPREAD VETO: spread {state.spread:.4f} > max allowed {state.max_spread:.4f}."
            )

        # -- 3. Max order size ----------------------------------------------------------
        if abs(safe_qty) > self.max_order_size:
            safe_qty = math.copysign(self.max_order_size, safe_qty)
            codes.append(REASON_MAX_ORDER_SIZE)
            reasons.append(
                f"MAX ORDER SIZE: proposed {proposed_qty:g} clipped to {safe_qty:g}."
            )

        # -- 4. Position cap ------------------------------------------------------------
        lo, hi = self._target_inventory_band(state)
        projected = state.current_inventory + safe_qty
        if projected < lo or projected > hi:
            clamped = min(max(projected, lo), hi)
            safe_qty = clamped - state.current_inventory
            if state.time_remaining_sec <= 0.0:
                codes.append(REASON_HORIZON_EXPIRED)
                reasons.append(
                    f"HORIZON EXPIRED: {state.time_remaining_sec:.1f}s remaining; only"
                    f" inventory-reducing orders permitted. Projected {projected:g} clamped"
                    f" to {clamped:g}."
                )
            else:
                codes.append(REASON_POSITION_CAP)
                reasons.append(
                    f"POSITION CAP: projected inventory {projected:g} outside [{lo:g},"
                    f" {hi:g}] (cap {state.max_inventory:g}); order reduced to {safe_qty:g}."
                )

        # -- 5. Cumulative quantity budget -----------------------------------------------
        # Forced liquidation is exempt: a spent budget must never strand the inventory that
        # the terminal-clearance rule exists to flatten.
        if (
            self.max_cumulative_qty is not None
            and not terminal_clearance_active
            and safe_qty != 0.0
        ):
            remaining = max(self.max_cumulative_qty - self.cumulative_qty_routed, 0.0)
            if abs(safe_qty) > remaining:
                clipped = math.copysign(remaining, safe_qty)
                codes.append(REASON_CUMULATIVE_BUDGET)
                reasons.append(
                    f"CUMULATIVE BUDGET: {self.cumulative_qty_routed:g} of"
                    f" {self.max_cumulative_qty:g} already routed this episode; order"
                    f" reduced from {safe_qty:g} to {clipped:g}."
                )
                safe_qty = clipped

        if safe_qty == 0.0:
            safe_qty = 0.0  # normalise -0.0, which prints and serialises confusingly
        self.cumulative_qty_routed += abs(safe_qty)

        # -- 6. Reward penalty shaping ----------------------------------------------------
        # One penalty per intercepted step, not one per violated constraint: the agent is
        # being told "the action you proposed was unsafe", which is a single fact.
        is_intercepted = bool(codes)
        penalty = self.penalty_lambda if is_intercepted else 0.0
        if is_intercepted:
            self.total_actions_intercepted += 1
            logger.warning(
                "RL action intercepted %s | proposed=%g -> safe=%g | %s",
                codes,
                proposed_qty,
                safe_qty,
                " ".join(reasons),
            )

        return SafeAction(
            proposed_qty=proposed_qty,
            safe_qty=safe_qty,
            is_intercepted=is_intercepted,
            interception_reason=" ".join(reasons) if reasons else None,
            shaped_reward=base_reward - penalty,
            reason_codes=tuple(codes),
            is_data_integrity_failure=False,
            penalty_applied=penalty,
        )
