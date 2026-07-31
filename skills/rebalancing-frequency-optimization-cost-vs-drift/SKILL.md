---
name: rebalancing-frequency-optimization-cost-vs-drift
description: >-
  Quantitative portfolio rebalancing optimizer evaluating the economic tradeoff between tracking error drift penalty and transaction costs using Leland no-trade bands.
domain: Portfolio Multi-Strategy
subdomain: Rebalancing Optimization & Execution Timing
tags: ["rebalancing-optimization", "cost-vs-drift", "no-trade-band", "tracking-error", "transaction-costs", "portfolio-governance"]
brokers_frameworks: ["Leland No-Trade Band Model", "Python Dataclasses"]
version: "1.0.0"
author: algo-trading-skills-contributors
license: Apache-2.0
---

## When to Use

Use this skill when managing multi-asset portfolios subject to price drift and transaction cost friction. Rebalancing on a fixed calendar schedule (e.g. daily/weekly) incurs unnecessary transaction fees and market impact. Conversely, ignoring drift causes portfolio weights to deviate from target risk mandates. This engine applies the Leland No-Trade Band model, comparing the drift tracking error penalty against total rebalancing transaction costs to determine optimal rebalance timing.

## Prerequisites

- Portfolio asset weights (`symbol`, `target_weight`, `current_weight`, `asset_value_usd`, `fee_rate_bps`, `estimated_slippage_bps`).
- Config parameters (`drift_penalty_lambda`: default 100.0, `max_drift_threshold_pct`: default 0.05 / 5.0%).

## Workflow

1. **Portfolio Drift & Transaction Cost Calculation**:
   - Compute Drift Cost: $\text{DriftCost} = \lambda_{\text{drift}} \cdot \sum (w_{i, \text{current}} - w_{i, \text{target}})^2 \cdot V_{\text{portfolio}}$.
   - Compute Transaction Cost: $\text{TxCost} = \sum |w_{i, \text{current}} - w_{i, \text{target}}| \cdot V_{\text{portfolio}} \cdot \left(\frac{\text{FeeBps}_i + \text{SlippageBps}_i}{10000}\right)$.
2. **Net Benefit & No-Trade Band Evaluation**:
   - Compute $\text{NetBenefit} = \text{DriftCost} - \text{TxCost}$.
   - Check if any asset weight drift $|w_{i, \text{current}} - w_{i, \text{target}}| > \text{MaxDriftThreshold}$.
3. **Trade Order Generation**:
   - If rebalancing is triggered, calculate exact USD buy/sell trade amounts needed to restore target weights.
4. **Audit Report Generation**: Output structured `RebalanceOptimizationReport`.

> Full procedure: see `references/workflows.md`.
> Standards reference: see `references/standards.md`.
> Printable pre-flight checklist: see `assets/checklist.md`.

## Common Pitfalls

- **Over-Rebalancing on Micro-Drifts**: Rebalancing for small weight shifts ($< 1\%$), where transaction fees wipe out any tracking error reduction.
- **Ignoring Slippage in Fee Calculations**: Accounting for broker commissions while ignoring market impact/slippage during rebalancing trades.
- **Whipsaw in Trending Markets**: Rebalancing to target weights in strongly trending markets, prematurely cutting winning positions.

## Verification

- Instantiate `RebalancingFrequencyOptimizerEngine`. Input portfolio ($V=\$1,000,000$, Target $50/50$, Current $60/40 \implies 10\%$ drift breach) $\implies$ verify `REBALANCE_TRIGGERED_MAX_DRIFT` status, Net Benefit calculated, and $\$100,000$ rebalance order generated. Input small drift ($50.5/49.5 \implies 0.5\%$ drift) $\implies$ verify `NO_REBALANCE_WITHIN_BAND` status.
- Run `python scripts/test_rebalancing_frequency_optimization_cost_vs_drift.py`.

## Related Skills

- `portfolio-construction-with-transaction-cost-awareness`
- `capital-efficiency-across-cross-margined-strategies`
---
