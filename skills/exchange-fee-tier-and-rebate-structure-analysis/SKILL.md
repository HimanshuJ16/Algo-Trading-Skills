---
name: exchange-fee-tier-and-rebate-structure-analysis
description: >-
  Quantitative market microstructure engine for analyzing exchange maker-taker vs inverted fee schedules, calculating rolling volume-tiered rebates, and evaluating next-tier volume jump opportunity costs.
domain: Venue Integration & Microstructure
subdomain: Exchange Pricing & Order Routing
tags: ["exchange-fees", "maker-taker", "taker-maker", "rebate-analysis", "volume-tiers", "order-routing", "market-microstructure"]
brokers_frameworks: ["Nasdaq Fee Schedule", "Cboe EDGX/EDGA", "Python Dataclasses"]
version: "1.0.0"
author: algo-trading-skills-contributors
license: Apache-2.0
---

## When to Use

Use this skill in quantitative market making, Smart Order Routing (SOR), and venue fee optimization engines. Exchanges utilize complex volume-tiered pricing models (**Maker-Taker** where makers earn rebates, and **Inverted / Taker-Maker** where takers earn rebates). This module tracks rolling 30-day trading volume, assigns active fee tiers, calculates net transaction execution costs, and quantifies the volume gap and fee savings required to jump to higher VIP volume tiers.

## Prerequisites

- Venue fee tier schedule definitions (Tier thresholds, maker rebate rates, taker fee rates).
- Historical rolling 30-day trading volume (maker shares, taker shares).
- Pricing model type (`MAKER_TAKER` or `TAKER_MAKER`).

## Workflow

1. **Volume Tier Classification**:
   - Compare rolling 30-day total volume against venue tier thresholds ($V_{\text{tier1}} < V_{\text{tier2}} < V_{\text{tier3}}$).
   - Assign current active volume tier.
2. **Net Transaction Execution Cost Calculation**:
   - $\text{Gross Taker Cost} = \text{Taker Shares} \times F_{\text{taker}}$.
   - $\text{Gross Maker Rebate} = \text{Maker Shares} \times R_{\text{maker}}$.
   - $\text{Net Transaction Cost} = \text{Gross Taker Cost} - \text{Gross Maker Rebate}$.
3. **Tier Jump Opportunity Cost Analysis**:
   - Compute remaining volume gap to next tier: $\Delta V = V_{\text{next\_tier\_threshold}} - V_{\text{current\_total}}$.
   - Estimate monthly fee savings if next tier is achieved.
4. **Audit Report Generation**: Output structured `FeeTierAnalysisReport`.

> Full procedure: see `references/workflows.md`.
> Standards reference: see `references/standards.md`.
> Printable pre-flight checklist: see `assets/checklist.md`.

## Common Pitfalls

- **Ignoring Inverted Venue Economics**: Routing passive maker orders to inverted venues (e.g. Cboe EDGA) expecting rebates, while incurring maker fees instead.
- **Failing to Track End-of-Month Tier Jumps**: Pushing extra volume at month-end without calculating if the tier jump savings exceed the adverse selection costs of forced trading.
- **Conflating Gross Fees with Net Capture**: Evaluating gross taker fees without accounting for maker rebates earned on passive fills.

## Verification

- Instantiate `ExchangeFeeTierAnalyzerEngine`. Define Maker-Taker venue (Tier 1: 0-10M shares, Taker $0.0030$/sh, Maker rebate $-0.0020$/sh; Tier 2: >10M shares, Taker $0.0025$/sh, Maker rebate $-0.0024$/sh). Submit 8,000,000 shares total (5M maker / 3M taker). Verify engine assigns Tier 1, computes net cost (\$9,000 taker cost - \$10,000 maker rebate = -\$1,000 net capture), and calculates 2,000,000 shares gap to Tier 2.
- Run `python scripts/test_exchange_fee_tier_and_rebate_structure_analysis.py`.

## Related Skills

- `execution-venue-fee-tier-optimization`
- `order-to-trade-ratio-fee-penalty-avoidance`
---
