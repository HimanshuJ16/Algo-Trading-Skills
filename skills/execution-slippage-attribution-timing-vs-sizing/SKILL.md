---
name: execution-slippage-attribution-timing-vs-sizing
description: >-
  Quantitative post-trade TCA engine for decomposing Implementation Shortfall (IS) into timing/delay slippage vs sizing/market impact slippage, identifying primary slippage drivers, and recommending execution strategy adjustments.
domain: Execution Algorithms
subdomain: Post-Trade Transaction Cost Analysis (TCA)
tags: ["tca", "implementation-shortfall", "slippage-attribution", "timing-slippage", "sizing-slippage", "market-impact", "execution-benchmarking"]
brokers_frameworks: ["Almgren-Chriss TCA", "IS Decomposition", "Python Dataclasses"]
version: "1.0.0"
author: algo-trading-skills-contributors
license: Apache-2.0
---

## When to Use

Use this skill in post-trade Transaction Cost Analysis (TCA), algorithmic execution reviews, and portfolio management. Total Implementation Shortfall ($\text{IS}_{\text{total}}$) measures the total cost of executing a trade relative to the decision price ($P_{\text{decision}}$). This module mathematically decomposes total slippage into **Timing/Delay Slippage** (price movement between decision time and order arrival at the venue) and **Sizing/Market Impact Slippage** (impact caused by order size during execution).

## Prerequisites

- Trade execution details (`side`: `'BUY'` or `'SELL'`, `order_qty`, `decision_price`, `arrival_price`, `average_execution_price`).
- Timestamps (`decision_time`, `arrival_time`, `completion_time`).

## Workflow

1. **Total Implementation Shortfall (IS) Calculation**:
   - $\text{IS}_{\text{total}} = \text{Side} \times \frac{\bar{P}_{\text{exec}} - P_{\text{decision}}}{P_{\text{decision}}} \times 10,000 \text{ (bps)}$.
2. **Timing / Delay Slippage Calculation**:
   - $\text{IS}_{\text{timing}} = \text{Side} \times \frac{P_{\text{arrival}} - P_{\text{decision}}}{P_{\text{decision}}} \times 10,000 \text{ (bps)}$.
3. **Sizing / Market Impact Slippage Calculation**:
   - $\text{IS}_{\text{sizing}} = \text{Side} \times \frac{\bar{P}_{\text{exec}} - P_{\text{arrival}}}{P_{\text{decision}}} \times 10,000 \text{ (bps)}$.
   - Verify identity: $\text{IS}_{\text{total}} \equiv \text{IS}_{\text{timing}} + \text{IS}_{\text{sizing}}$.
4. **Primary Driver & Strategy Recommendation**:
   - If $|\text{IS}_{\text{timing}}| > |\text{IS}_{\text{sizing}}| \implies$ Flag `TIMING_DRIVEN_SLIPPAGE` (Recommend `ACCELERATE_ORDER_DISPATCH`).
   - Else $\implies$ Flag `SIZING_DRIVEN_SLIPPAGE` (Recommend `REDUCE_PARTICIPATION_RATE_CEILING`).
5. **Audit Report Generation**: Output structured `SlippageAttributionAuditReport`.

> Full procedure: see `references/workflows.md`.
> Standards reference: see `references/standards.md`.
> Printable pre-flight checklist: see `assets/checklist.md`.

## Common Pitfalls

- **Conflating Delay Slippage with Market Impact**: Blaming execution algorithms for high slippage when 80% of the cost occurred during decision-to-routing delays before the order reached the venue.
- **Failing to Handle Trade Side Multipliers**: Reversing sign conventions for short/sell orders, resulting in negative slippage reported as positive cost.
- **Ignoring Benchmarks Other Than Arrival Price**: Evaluating IS without isolating broad market beta movement from stock-specific execution impact.

## Verification

- Instantiate `ExecutionSlippageAttributionEngine`. Input BUY order: $P_{\text{decision}} = \$100.00$, $P_{\text{arrival}} = \$100.50$, $\bar{P}_{\text{exec}} = \$100.70$. Verify engine computes total IS = $+70.0\text{ bps}$, timing slippage = $+50.0\text{ bps}$, sizing slippage = $+20.0\text{ bps}$, classifies primary driver as `TIMING_DRIVEN_SLIPPAGE`, and recommends `ACCELERATE_ORDER_DISPATCH`.
- Run `python scripts/test_execution_slippage_attribution_timing_vs_sizing.py`.

## Related Skills

- `execution-algo-parameter-optimization-via-backtest`
- `post-trade-execution-quality-scorecard`
---
