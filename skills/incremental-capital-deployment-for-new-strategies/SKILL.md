---
name: incremental-capital-deployment-for-new-strategies
description: >-
  Portfolio risk management engine implementing 4-tier stage-gated capital ramp-up (Paper -> 10% Seed -> 50% Scale -> 100% Full) with realized Sharpe and drawdown promotion gates.
domain: Portfolio Multi-Strategy
subdomain: Strategy Onboarding & Stage-Gated Scaling
tags: ["capital-deployment", "stage-gated-scaling", "strategy-onboarding", "portfolio-risk", "drawdown-limits", "sharpe-ratio-gate"]
brokers_frameworks: ["Portfolio Multi-Strategy Engine", "Python Dataclasses"]
version: "1.0.0"
author: algo-trading-skills-contributors
license: Apache-2.0
---

## When to Use

Use this skill when onboarding newly researched quantitative trading strategies into live production. Allocating 100% target capital to an unproven strategy risks severe drawdowns from unexpected execution slippage, regime shifts, or overfitting. This module enforces a **4-Tier Stage-Gated Ramp-up Framework** (Tier 0 Sandbox 0% -> Tier 1 Seed 10% -> Tier 2 Scale 50% -> Tier 3 Full 100%), evaluating realized live Sharpe ratios and max drawdowns to govern promotion decisions.

## Prerequisites

- Strategy state (`strategy_id`, `current_tier`: 0, 1, 2, 3, `days_in_tier`, `realized_sharpe`, `realized_max_drawdown_pct`, `slippage_ratio`).
- Target full production capital USD (e.g. $1,000,000$).
- Stage-gated promotion rules (`tier1_min_days = 30`, `tier1_min_sharpe = 1.0`, `max_allowed_dd = 12.0%`).

## Workflow

1. **Emergency Demotion & Drawdown Audit**:
   - If $\text{Realized Max DD} \ge 12.0\% \implies$ Action `EMERGENCY_DEACTIVATED` (demote to Tier 0 and freeze trading).
2. **Stage-Gated Promotion Evaluation**:
   - **Tier 0 -> Tier 1**: Requires $\ge 14$ paper trading days with 0 execution crashes. Allocates 10% capital.
   - **Tier 1 -> Tier 2**: Requires $\ge 30$ live days, Realized Sharpe $\ge 1.0$, Max DD $\le 5.0\%$, Slippage $\le 1.5\times$. Allocates 50% capital.
   - **Tier 2 -> Tier 3**: Requires $\ge 60$ live days, Realized Sharpe $\ge 1.2$, Max DD $\le 8.0\%$. Allocates 100% capital.
3. **Allocated Capital Calculation**:
   - $\text{Allocated USD} = \text{Target Full USD} \times \text{Tier Allocation Pct}$.
4. **Audit Report Generation**: Output structured `IncrementalDeploymentReport`.

> Full procedure: see `references/workflows.md`.
> Standards reference: see `references/standards.md`.
> Printable pre-flight checklist: see `assets/checklist.md`.

## Common Pitfalls

- **Immediate 100% Capital Allocation**: Allocating 100% target capital on Day 1 of live trading, suffering immediate drawdown during unexpected execution anomalies.
- **Ignoring Live Slippage Discrepancies**: Promoting strategies from Seed to Scale despite live slippage exceeding backtest estimates by $3\times$.
- **Lacking Automated Demotion Gates**: Failing to demote strategies back to Tier 0 when live performance breaches maximum drawdown thresholds.

## Verification

- Instantiate `IncrementalCapitalDeploymentEngine`. Test Tier 1 Strategy (35 days in Tier 1, Realized Sharpe 1.4, Max DD 3.2%, Slippage 1.1x, Target $1M) $\implies$ verify engine promotes to `TIER_2_SCALE` allocating $500,000 (50%). Test Drawdown Breach (Max DD 14.5% > 12.0%) $\implies$ verify engine triggers `EMERGENCY_DEACTIVATED` to Tier 0 ($0 allocation).
- Run `python scripts/test_incremental_capital_deployment_for_new_strategies.py`.

## Related Skills

- `new-strategy-onboarding-checklist`
- `strategy-capacity-estimation-before-scaling-capital`
---
