---
name: opportunity-cost-tracking-for-idle-capital
description: >-
  Opportunity cost tracking engine calculating idle capital return drag against SOFR benchmark rates, evaluating net yield gains after sweep transaction costs, and auditing cash sweep thresholds.
domain: Treasury Management & Multi-Strategy
subdomain: Capital Allocation & Cash Sweep Optimization
tags: ["opportunity-cost", "idle-capital", "treasury-management", "cash-sweep", "sofr-benchmark", "return-drag", "capital-allocation"]
brokers_frameworks: ["SOFR / US Treasury Benchmark", "Python Dataclasses"]
version: "1.0.0"
author: algo-trading-skills-contributors
license: Apache-2.0
---

## When to Use

Use this skill when managing multi-strategy portfolios, fund treasuries, or crypto trading accounts where unallocated cash balances sit idle without generating yield. Idle capital creates a hidden drag on overall portfolio Sharpe ratios and total returns. This engine computes the idle capital ratio ($\text{IdleCash} / \text{TotalCapital}$), measures annualized opportunity cost drag against risk-free benchmark rates (e.g. SOFR $5.25\%$), and calculates net yield gains after transaction sweep fees to trigger automated cash sweeps.

## Prerequisites

- Portfolio capital state (`total_capital`, `allocated_capital`, `unallocated_cash`, `benchmark_rate_pct`, `holding_period_days`).
- Sweep policy config (`min_sweep_threshold_usd`: e.g. $100,000, `sweep_transaction_cost_usd`: $50, `target_idle_ratio_max`: 0.05).

## Workflow

1. **Idle Capital Ratio & Drag Calculation**:
   - Compute Idle Capital Ratio: $\text{IdleRatio} = \text{UnallocatedCash} / \text{TotalCapital}$.
   - Compute Gross Opportunity Cost Drag ($USD$):
     $$\text{GrossDrag}_{\text{USD}} = \text{UnallocatedCash} \times \frac{r_{\text{benchmark}}}{100} \times \frac{\text{Days}}{365}$$
   - Compute Drag in Basis Points: $\text{Drag}_{\text{bps}} = (\text{GrossDrag}_{\text{USD}} / \text{TotalCapital}) \times 10,000$.
2. **Net Yield & Cash Sweep Optimization**:
   - Compute Net Yield Gain: $\text{NetYieldGain} = \text{GrossDrag}_{\text{USD}} - \text{SweepCost}_{\text{USD}}$.
   - If $\text{UnallocatedCash} \ge \text{MinSweepThreshold}$ and $\text{NetYieldGain} > 0 \implies$ Generate `SWEEP_TO_YIELD_BENCHMARK`.
3. **Threshold Compliance Audit**:
   - Flag alert if $\text{IdleRatio} > \text{TargetIdleRatioMax}$ ($5\%$).
4. **Audit Report Generation**: Output structured `OpportunityCostReport`.

> Full procedure: see `references/workflows.md`.
> Standards reference: see `references/standards.md`.
> Printable pre-flight checklist: see `assets/checklist.md`.

## Common Pitfalls

- **Ignoring Transaction Sweep Fees**: Sweeping small idle cash amounts where transaction fees exceed potential benchmark yield gain.
- **Over-Investing Operational Buffer Cash**: Sweeping $100\%$ of cash without reserving an operational liquidity buffer for margin calls or execution settlements.
- **Using Outdated Benchmark Rates**: Hardcoding static risk-free rates instead of pulling live SOFR or US T-bill rates.

## Verification

- Instantiate `OpportunityCostTrackerEngine`. Input $10M total capital with $2M idle cash ($20\%$ idle ratio) over 30 days @ 5.25% SOFR $\implies$ verify gross drag calculation ($\sim \$8,630$), drag in bps ($\sim 8.63$ bps), and recommendation `SWEEP_TO_YIELD_BENCHMARK`. Input $10K idle cash below $100K threshold $\implies$ verify recommendation `MAINTAIN_IDLE_CASH`.
- Run `python scripts/test_opportunity_cost_tracking_for_idle_capital.py`.

## Related Skills

- `capital-reallocation-based-on-live-performance`
- `margin-utilization-circuit-breaker`
---
