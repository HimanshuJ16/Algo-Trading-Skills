---
name: reinforcement-learning-safety-constraints-for-execution
description: >-
  Action-space safety shield for reinforcement learning execution agents enforcing max order sizing, position cap limits, spread veto guards, terminal horizon inventory clearance, and reward penalty shaping.
domain: Execution Algorithms & Machine Learning
subdomain: Safe Reinforcement Learning & Risk Guardrails
tags: ["reinforcement-learning", "safety-constraints", "action-shield", "execution-algo", "spread-veto", "reward-shaping"]
brokers_frameworks: ["Safe RL (Shielding Architecture)", "Action-Space Clipping", "Reward Penalty Shaping", "Python Dataclasses"]
version: "1.0.0"
author: algo-trading-skills-contributors
license: Apache-2.0
---

## When to Use

Use this skill when deploying Reinforcement Learning (RL) agents for optimal trade execution (TWAP/VWAP optimal liquidation, market making). Unconstrained RL agents can propose erratic or dangerous actions during out-of-distribution market conditions (e.g., massive order sizes, trading into illiquid spreads, holding inventory past execution horizons). This safety shield intercepts raw RL policy action proposals, applies deterministic risk rules (clipping order size, position caps, spread vetoes, terminal liquidation), and shapes penalty rewards to guide model training.

## Prerequisites

- Execution state (`current_inventory`, `max_inventory`, `bid`, `ask`, `time_remaining_sec`, `max_spread`).
- Guard configuration (`max_order_size`: default 100, `penalty_lambda`: default 10, `terminal_horizon_sec`: default 60).

## Workflow

1. **Spread Veto Check**:
   - Veto market order proposal (`safe_qty = 0`) if bid-ask spread exceeds `max_spread`.
2. **Terminal Inventory Clearance**:
   - Force liquidation order if `time_remaining_sec` $\le$ `terminal_horizon_sec` and inventory is non-zero.
3. **Max Order Size Clipping**:
   - Clip action quantity to `max_order_size`.
4. **Position Cap Limit Enforcement**:
   - Cap order quantity so `current_inventory + safe_qty` $\le$ `max_inventory`.
5. **Reward Penalty Shaping**:
   - Deduct `penalty_lambda` penalty from base reward whenever action is intercepted.

> Full procedure: see `references/workflows.md`.
> Standards reference: see `references/standards.md`.
> Printable pre-flight checklist: see `assets/checklist.md`.

## Common Pitfalls

- **Unshielded Live RL Deployment**: Allowing raw RL policy outputs to route directly to broker APIs without a deterministic safety layer.
- **No Penalty Reward Shaping**: Intercepting RL actions without penalizing the agent, leading to persistent unsafe policy proposals.
- **Ignoring Terminal Horizon**: Failing to force inventory clearance near execution end, leaving unexecuted parent order shares.

## Verification

- Instantiate `SafeRLExecutionGuard`. Propose +500 order when inventory is 800 (max 1000) $\implies$ verify clipped to +100 and reward penalized by -10. Propose order during wide spread $\implies$ verify vetoed to 0. Propose normal order $\implies$ passes unmodified.
- Run `python scripts/test_rl_safety_guard.py`.

## Related Skills

- `execution-algorithm-kill-switch-integration`
- `algo-parameter-defaults-by-instrument-liquidity-tier`
---
