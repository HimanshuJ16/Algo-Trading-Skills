---
name: capital-reallocation-based-on-live-performance
description: >-
  Quantitative capital allocation engine that dynamically re-weights funding across multiple active strategies based on real-time performance metrics (Sharpe, Kelly).
domain: Portfolio Management
subdomain: Capital Allocation
tags: ["capital-allocation", "dynamic-weighting", "kelly-criterion", "sharpe-ratio", "portfolio"]
brokers_frameworks: ["Generic Portfolio Management"]
version: "1.0.0"
author: algo-trading-skills-contributors
license: Apache-2.0
---

## When to Use

Use this skill when managing a multi-strategy fund or portfolio where capital is limited and needs to be dynamically distributed among competing algorithms. Instead of static, equal-weight allocations, this engine evaluates recent live performance (e.g., trailing 30-day Sharpe ratio or rolling Kelly fraction) to organically scale up outperforming strategies and scale down underperforming ones.

## Prerequisites

- Multiple independent trading strategies reporting daily or real-time PnL.
- A centralized fund/portfolio controller capable of adjusting strategy buying power.
- Historical baseline metrics (expected win rate, average win/loss) for each strategy to seed the allocation algorithm.

## Workflow

1. **Strategy Registration**: Register each strategy with the `CapitalReallocationEngine` along with its initial base capital allocation.
2. **Performance Ingestion**: Continuously feed the engine with live PnL updates from each strategy.
3. **Metric Calculation**: The engine calculates trailing performance metrics (e.g., rolling Sharpe Ratio, Fractional Kelly).
4. **Reallocation**: On a defined schedule (e.g., daily or weekly), the engine recomputes the target capital weights for each strategy.
5. **Execution**: The engine generates "Capital Adjustment" signals (e.g., `-10k to Strategy A, +10k to Strategy B`), which the central OMS enacts by adjusting strategy position limits.

> Full procedure: see `references/workflows.md`.
> Standards reference: see `references/standards.md`.
> Printable pre-flight checklist: see `assets/checklist.md`.

## Common Pitfalls

- **Chasing Noise**: Re-allocating capital too frequently (e.g., intraday based on tick-by-tick PnL) causes "whipsawing," where capital is shifted to a strategy exactly when it peaks, leading to mean-reverting losses.
- **Full Kelly Recklessness**: Using the unadjusted Kelly formula, which mathematically guarantees optimal long-term growth but practically ensures massive short-term drawdowns due to parameter estimation error. Always use Half-Kelly or Quarter-Kelly.
- **Ignoring Capacity**: Allocating \$100M to a micro-cap strategy just because its Sharpe ratio is high, ignoring the fact that it cannot absorb more than \$5M without destroying its edge via market impact.

## Verification

- Simulate two strategies: one with steady wins and one on a losing streak. Run the engine and verify capital is smoothly re-weighted towards the winning strategy, bounded by max allocation constraints.
- Run `python scripts/test_capital_reallocation_engine.py`.

## Related Skills

- `multi-strategy-capital-allocation-limits`
- `incremental-capital-deployment-for-new-strategies`
