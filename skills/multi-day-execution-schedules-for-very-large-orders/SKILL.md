---
name: multi-day-execution-schedules-for-very-large-orders
description: >-
  Almgren-Chriss institutional order execution scheduler for large parent orders (>10% ADV), calculating multi-day horizon slicing, ADV participation caps, and overnight volatility risk tradeoffs.
domain: Execution Algorithms
subdomain: Multi-Day Order Scheduling & Market Impact Optimization
tags: ["multi-day-execution", "almgren-chriss", "adv-limit", "parent-order", "market-impact", "overnight-risk", "optimal-execution"]
brokers_frameworks: ["Almgren-Chriss Framework", "VWAP/TWAP Slicing", "Python Dataclasses"]
version: "1.0.0"
author: algo-trading-skills-contributors
license: Apache-2.0
---

## When to Use

Use this skill when executing massive institutional parent orders where total quantity $Q_{\text{total}}$ represents a large fraction of Average Daily Volume ($\text{ADV}$), e.g. $50\%$ to $200\%$ of $\text{ADV}$. Forcing execution within a single trading session causes severe market impact, order flow leakage, and adverse price movement. Slicing execution across an optimal multi-day horizon $N_{\text{days}}$ balances **Temporary/Permanent Market Impact Costs** against **Overnight Volatility Risk** within strict daily participation caps ($\le 10\% - 15\%$ ADV).

## Prerequisites

- Parent order specifications (`symbol`, `total_parent_quantity`, `current_price`, `adv_shares`).
- Market parameters (`max_daily_participation_pct`: e.g. 0.10, `volatility_daily_pct`: e.g. 0.02).
- Trajectory profile (`EQUAL_DAILY`, `FRONT_LOADED`, `BACK_LOADED`).

## Workflow

1. **Daily Participation Limit & Horizon Calculation**:
   - Compute maximum allowed daily volume:
     $$\text{Daily\_Limit} = \text{ADV} \times \text{max\_daily\_participation\_pct}$$
   - Determine execution horizon:
     $$N_{\text{days}} = \max\left(1, \left\lceil \frac{Q_{\text{total}}}{\text{Daily\_Limit}} \right\rceil\right)$$
2. **Trajectory Allocation Synthesis**:
   - Slices daily quantities $V_1, V_2, \dots, V_N$ subject to $V_d \le \text{Daily\_Limit}$:
     - `EQUAL_DAILY`: Uniform daily slicing ($V_d = Q / N$).
     - `FRONT_LOADED`: Exponential decay trajectory to minimize overnight volatility exposure.
     - `BACK_LOADED`: Exponential growth trajectory.
3. **Market Impact & Overnight Risk Tradeoff Audit**:
   - Compute expected temporary market impact $\text{MI}_{\text{temp}}$ and permanent price movement $\text{MI}_{\text{perm}}$.
   - Quantify overnight price risk variance across unexecuted remaining quantities.
4. **Audit Report Generation**: Output structured `MultiDayScheduleReport`.

> Full procedure: see `references/workflows.md`.
> Standards reference: see `references/standards.md`.
> Printable pre-flight checklist: see `assets/checklist.md`.

## Common Pitfalls

- **Forcing Single-Day Execution**: Attempting to execute $100\%$ ADV in 1 day, causing $> 200\text{ bps}$ market impact and toxic signal leakage.
- **Ignoring Overnight Volatility Risk**: Dragging execution over 10 days to minimize impact while exposing the position to overnight earnings gaps or macro shocks.
- **Breaching Participation Caps**: Pushing daily volume above $15\%$ ADV, alerting predatory high-frequency algorithms.

## Verification

- Instantiate `MultiDayExecutionSchedulerEngine`. Audit 500,000 share parent order on stock with 1,000,000 ADV ($50\%$ ADV) and $10\%$ participation limit $\implies$ verify $N_{\text{days}} = 5$, daily cap $100,000$ shares/day, and expected market impact cost.
- Run `python scripts/test_multi_day_execution_schedules_for_very_large_orders.py`.

## Related Skills

- `iceberg-order-native-broker-support-vs-simulation`
- `execution-slippage-attribution-timing-vs-sizing`
---
