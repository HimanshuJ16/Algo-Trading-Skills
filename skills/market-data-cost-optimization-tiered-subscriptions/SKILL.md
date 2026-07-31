---
name: market-data-cost-optimization-tiered-subscriptions
description: >-
  Quantitative market data feed cost optimization engine, dynamically promoting active trading symbols to high-frequency L3 direct feeds while demoting inactive universe symbols to low-cost EOD/SIP tiers.
domain: Data Management Global
subdomain: Market Data Cost & Entitlement Governance
tags: ["market-data", "cost-optimization", "tiered-subscriptions", "bloomberg-bpipe", "refinitiv-dacs", "data-entitlements", "sip-vs-direct"]
brokers_frameworks: ["Bloomberg EMRS", "Refinitiv DACS", "TRG Screen", "Python Dataclasses"]
version: "1.0.0"
author: algo-trading-skills-contributors
license: Apache-2.0
---

## When to Use

Use this skill when managing institutional market data subscription budgets across large asset universes (Bloomberg B-PIPE, Refinitiv DACS, Direct Exchange Feeds). Subscribing to high-frequency direct L3 feeds for thousands of inactive universe symbols incurs massive monthly data fees ($1,000$+ per symbol/month). This module implements **Tiered Data Subscriptions** (Tier 1 Direct L3, Tier 2 SIP L1, Tier 3 Delayed/EOD), dynamically promoting active portfolio names and demoting stale inactive symbols to optimize data spend while preserving execution quality.

## Prerequisites

- Symbol subscription payload (`symbol`, `current_tier`: `TIER1`/`TIER2`/`TIER3`, `has_active_position`, `has_active_signal`, `days_since_last_trade`).
- Tier cost schedule (`TIER1_DIRECT_L3`: $1000/mo, `TIER2_SIP_L1`: $150/mo, `TIER3_DELAYED_EOD`: $5/mo).

## Workflow

1. **Symbol Activity Audit**:
   - Audit `has_active_position`, `has_active_signal`, and `days_since_last_trade`.
2. **Tier Promotion & Demotion Decision**:
   - Promote to `TIER1_DIRECT_L3` if symbol has active position AND high trading signal.
   - Promote to `TIER2_SIP_L1` if symbol has active position OR recent trading activity ($\le 30$ days).
   - Demote to `TIER3_DELAYED_EOD` if no active position AND zero trades for $> 30$ days.
3. **Monthly Spend & Savings Audit**:
   - Calculate baseline monthly spend vs optimized spend.
   - Compute total monthly cost savings $S_{\text{savings\_usd}}$.
4. **Audit Report Generation**: Output structured `MarketDataCostReport`.

> Full procedure: see `references/workflows.md`.
> Standards reference: see `references/standards.md`.
> Printable pre-flight checklist: see `assets/checklist.md`.

## Common Pitfalls

- **Paying Tier 1 Direct Fees for Inactive Symbols**: Maintaining $1,000/month co-location direct feeds for symbols with zero position or trading activity for months.
- **Demoting Active Trading Names**: Accidentally demoting an open position symbol to 15-minute delayed data, causing execution slippage and pricing crashes.
- **Ignoring Exchange Professional Licensing Fees**: Failing to account for fixed user professional exchange fees when adding direct feeds.

## Verification

- Instantiate `MarketDataCostOptimizerEngine`. Audit 100 symbols (10 Active positions on Tier 1, 90 Inactive stale symbols on Tier 1). Verify engine demotes 90 inactive symbols to Tier 3 EOD, reducing monthly spend from $\$100,000$ to $\$10,450$ ($\$89,550$ monthly savings, $89.5\%$ cost reduction) and approves `COST_OPTIMIZATION_SUCCESS`.
- Run `python scripts/test_market_data_cost_optimization_tiered_subscriptions.py`.

## Related Skills

- `historical-data-backfill-rate-limit-management`
- `real-time-vs-delayed-data-entitlement-handling`
---
