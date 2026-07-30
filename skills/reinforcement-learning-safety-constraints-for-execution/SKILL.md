---
name: reinforcement-learning-safety-constraints-for-execution
description: Use when training or deploying RL execution agents to implement hard
  action masking, position limits, spread veto guards, and reward penalty shaping
  to prevent unsafe policy exploration
domain: algorithmic-trading
subdomain: financial-ml
tags:
- financial-ml
- reinforcement-learning
- action-masking
- safety-constraints
- rl-execution
brokers_frameworks:
- Gymnasium
- Stable-Baselines3
- Ray RLLib
- Custom RL Environments
version: '1.0'
author: algo-trading-skills-contributors
license: Apache-2.0
---

## When to Use

Invoke this whenever deploying Reinforcement Learning (RL) agents for optimal trade execution, inventory management, or dynamic portfolio rebalancing. Pure RL agents optimize reward functions through trial-and-error exploration and can learn unsafe behaviors, such as placing orders that breach position limits, submitting market orders into wide bid-ask spreads, or failing to clear inventory before market close. Wrapping the RL agent's action space with a deterministic Safety Shield (`SafeRLExecutionGuard`) that clips unsafe actions, applies action masking, and shapes penalty rewards is mandatory.

## Prerequisites

- Base RL agent output action vector (e.g. proposed order quantity $\Delta Q$ and order type).
- Current environment state (current inventory $Q$, bid-ask spread, time remaining $T$).
- Hard risk limits ($Q_{\text{max}}$, $\text{MaxOrderSize}$, $\text{MaxSpread}$).

## Workflow

1. **Intercept Raw Policy Action**:
   - Receive raw action proposal $a_{\text{raw}} = \Delta Q_{\text{proposed}}$ from RL policy network.

2. **Apply Deterministic Safety Masking & Clipping**:
   - **Order Size Constraint**: Clip $|\Delta Q| \le \text{MaxOrderSize}$.
   - **Position Limit Constraint**: Ensure $|Q_{\text{current}} + \Delta Q_{\text{clipped}}| \le Q_{\text{max}}$.
   - **Spread Veto Constraint**: If $\text{Spread} > \text{MaxSpread}$, force limit order or cancel action.
   - **Terminal Inventory Schedule**: If remaining time $T_{\text{rem}} \to 0$, override policy to force inventory clearance.

3. **Shape Penalty Reward Signal**:
   - Compute reward penalty for policy optimization:
     $$R_{\text{safe}} = R_{\text{PnL}} - \lambda_{\text{penalty}} \cdot \mathbb{I}(\text{Action Intercepted})$$

4. **Audit Intercepted Actions**:
   - Record safety override count and ratio $P_{\text{override}} = \frac{N_{\text{intercepted}}}{N_{\text{total}}}$ to evaluate policy safety convergence.

> Full step-by-step procedure with broker-specific detail: see `references/workflows.md`.
> Broker/framework coverage table for this skill: see `references/standards.md`.
> Printable pre-flight checklist: see `assets/checklist.md`.

## Common Pitfalls

- **Unshielded Production RL Execution**: Deploying raw neural network actions directly to broker APIs without a deterministic hard-constraint wrapper.
- **Soft Penalties Only**: Relying solely on reward penalties without hard action clipping, allowing RL agents to occasionally breach risk limits during out-of-distribution market conditions.
- **Overshooting Inventory Horizons**: Failing to force inventory clearance near market close, holding unwanted overnight risk.

## Verification

- Propose unsafe RL order ($\Delta Q = 500$, $Q_{\text{current}} = 800$, $Q_{\text{max}} = 1000$) and verify `SafeRLExecutionGuard` clips action to $\Delta Q = 200$.
- Propose market order during wide spread ($\text{Spread} = 2.50 > 1.00$) and verify action is vetoed.
- Verify reward penalty is subtracted when action interception occurs.
- Run unit test suite `python scripts/test_rl_safety_guard.py` and confirm 100% pass rate.

## Related Skills

- `feature-store-for-live-and-backtest-parity`
- `kill-switch-and-drawdown-circuit-breakers`
- `regime-detection-for-strategy-switching`
---
