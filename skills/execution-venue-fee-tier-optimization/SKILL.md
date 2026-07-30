---
name: execution-venue-fee-tier-optimization
description: >-
  Quantitative Smart Order Router (SOR) engine for optimizing order flow volume distribution across multiple execution venues, unlocking volume fee tiers, and minimizing net transaction costs.
domain: Venue Integration & Microstructure
subdomain: Multi-Venue Order Routing & Fee Tier Optimization
tags: ["fee-tier-optimization", "sor-routing", "net-price-routing", "maker-taker", "taker-maker", "volume-allocation", "exchange-fees"]
brokers_frameworks: ["Nasdaq/Cboe Fee Schedules", "SOR Net Cost Engine", "Python Dataclasses"]
version: "1.0.0"
author: algo-trading-skills-contributors
license: Apache-2.0
---

## When to Use

Use this skill in Smart Order Routers (SOR), high-frequency market making desks, and multi-venue execution algorithms. Exchanges use volume-tiered fee schedules where monthly volume thresholds unlock lower taker fees and higher maker rebates. This module optimizes order flow volume distribution across candidate venues (Nasdaq, Cboe EDGX, EDGA, BATS) to maximize maker rebates and minimize net fee expenditures while maintaining fill probability constraints.

## Prerequisites

- Candidate venue definitions with volume-tiered fee schedules (thresholds, taker rates, maker rates, fill probabilities).
- Target monthly order volume budget ($V_{\text{target}}$ in shares).
- Order flow mix (% maker passive volume vs % taker aggressive volume).

## Workflow

1. **Candidate Allocation Strategy Generation**:
   - Construct volume distribution profiles:
     - `CONCENTRATED_VIP`: Concentrate volume into single primary venue to reach top VIP tier.
     - `BALANCED_FEE_OPTIMAL`: Split volume to breach Tier 2/3 thresholds on multiple high-rebate venues.
     - `LIQUIDITY_WEIGHTED`: Allocate strictly by venue fill probability and depth.
2. **Net Transaction Execution Cost Evaluation**:
   - For each venue allocation $V_k$:
     - Determine active tier based on assigned volume $V_k$.
     - Calculate $\text{Gross Taker Fee}_k = V_k^{\text{taker}} \times F_k^{\text{taker}}$.
     - Calculate $\text{Gross Maker Rebate}_k = V_k^{\text{maker}} \times R_k^{\text{maker}}$.
     - $\text{Net Cost}_k = \text{Gross Taker Fee}_k - \text{Gross Maker Rebate}_k$.
3. **Optimal Strategy Selection**:
   - Select volume distribution minimizing total net cost $\sum \text{Net Cost}_k$.
4. **Audit Report Generation**: Output structured `VenueFeeOptimizationReport`.

> Full procedure: see `references/workflows.md`.
> Standards reference: see `references/standards.md`.
> Printable pre-flight checklist: see `assets/checklist.md`.

## Common Pitfalls

- **Chasing Fee Tiers at the Expense of Fill Probability**: Directing passive liquidity to low-fill-rate inverted venues to earn rebates, resulting in high opportunity costs from unexecuted orders.
- **Ignoring Net-Price Routing**: Evaluating routing decisions on nominal quoted prices without accounting for net exchange taker fees and maker rebates.
- **Failing to Re-Calculate Tiers Mid-Month**: Leaving static routing allocations fixed when a venue's monthly volume tier has already been achieved.

## Verification

- Instantiate `ExecutionVenueFeeTierOptimizerEngine`. Define 3 venues (NASDAQ, Cboe EDGX, Cboe EDGA). Set monthly target volume = 30,000,000 shares (70% maker / 30% taker). Run optimization. Verify engine evaluates candidate volume splits, identifies optimal net-cost allocation, computes expected monthly savings, and outputs `VenueFeeOptimizationReport`.
- Run `python scripts/test_execution_venue_fee_tier_optimization.py`.

## Related Skills

- `exchange-fee-tier-and-rebate-structure-analysis`
- `smart-order-router-failover-on-venue-outage`
---
