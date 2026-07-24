---
name: multi-strategy-capital-allocation-limits
description: >-
  Use when multiple concurrent strategies share a single trading account to allocate
  and cap capital per strategy, preventing any single strategy from consuming disproportionate
  capital and ensuring total allocated capital never exceeds available account equity.
domain: algorithmic-trading
subdomain: risk-management
tags: ["risk-management", "capital-allocation", "multi-strategy", "portfolio-management", "position-limits"]
brokers_frameworks: ["Custom Portfolio Engine", "NumPy"]
version: "1.0"
author: algo-trading-skills-contributors
license: Apache-2.0
---

## When to Use

Invoke this skill whenever multiple algorithmic strategies run concurrently on a single brokerage
account. Without explicit capital allocation limits, a single aggressive strategy can consume
all available margin, starving other strategies and creating concentrated risk. This skill
enforces per-strategy capital caps, tracks real-time utilization, and blocks new orders when
a strategy exceeds its allocation.

## Prerequisites

- Defined strategy roster with target capital allocation percentages.
- Real-time account equity / NAV feed.
- Per-strategy position tracking (current exposure in dollars).

## Workflow

1. **Define Strategy Allocation Table**:
   - Assign each strategy a maximum capital allocation as percentage of total NAV.
   - Validate $\sum_s \text{alloc}_s \le 100\%$ (may be < 100% to maintain cash reserve).

2. **Track Real-Time Utilization**:
   - For each strategy $s$, compute $\text{utilization}_s = \text{exposure}_s / \text{NAV}$.

3. **Pre-Trade Validation**:
   - Before placing an order for strategy $s$, verify:
     $$\text{exposure}_s + \text{order\_value} \le \text{alloc}_s \cdot \text{NAV}$$

4. **Rebalance on NAV Changes**:
   - When NAV changes significantly, recalculate effective dollar caps for each strategy.

> Full procedure: see `references/workflows.md`.
> Standards: see `references/standards.md`.
> Checklist: see `assets/checklist.md`.

## Common Pitfalls

- **Over-Allocation**: Sum of strategy allocations exceeding 100% creates implicit leverage.
- **Stale NAV**: Using yesterday's NAV for today's allocation caps during volatile markets.
- **Ignoring Unrealized P&L**: Measuring utilization by cost basis instead of mark-to-market value.

## Verification

- Create 3 strategies with 40%, 30%, 20% allocations and verify cap enforcement.
- Attempt to exceed a strategy's allocation and confirm order rejection.
- Run `python scripts/test_capital_allocator.py` and confirm 100% pass rate.

## Related Skills

- `kill-switch-and-drawdown-circuit-breakers`
- `value-at-risk-var-live-monitoring`
- `correlation-aware-exposure-limits`
---
