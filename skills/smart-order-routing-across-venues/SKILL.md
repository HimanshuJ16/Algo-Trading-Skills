---
name: smart-order-routing-across-venues
description: >-
  Production-grade Smart Order Routing (SOR) consolidation engine enforcing US Reg NMS Rule 611 Order Protection NBBO compliance, fee-aware maker-taker routing, multi-venue order book sweeping, and market impact minimization across lit exchanges.
domain: Execution & Smart Order Routing
subdomain: Reg NMS & Multi-Venue Order Routing
tags: ["smart-order-routing", "sor", "reg-nms", "nbbo-consolidation", "maker-taker-fee", "order-book-sweep"]
brokers_frameworks: ["Reg NMS Rule 611", "US Equities Market Microstructure", "Python Dataclasses"]
version: "1.0.0"
author: algo-trading-skills-contributors
license: Apache-2.0
---

## When to Use

Use this skill when routing parent equity or options orders across multiple fragmented lit exchanges (NASDAQ, NYSE, Cboe BATS, EDGX, IEX). Under US Reg NMS Rule 611 (Order Protection Rule), brokers must prevent trade-throughs by executing orders at prices equal to or better than the National Best Bid and Offer (NBBO). This engine consolidates top-of-book quotes across venues, scores routing targets based on price, taker fees / maker rebates, and latency, and slices child orders across venues to sweep available liquidity cleanly.

## Prerequisites

- Real-time venue quote feed (`VenueQuote`: `venue_id`, `bid_price`, `bid_qty`, `ask_price`, `ask_qty`, `taker_fee_per_share`, `maker_rebate_per_share`, `latency_ms`).
- Parent order specification (`parent_order_id`, `symbol`, `side`, `quantity`).

## Workflow

1. **NBBO Consolidation**:
   - Consolidate real-time quotes across venues to determine National Best Bid and Offer.
2. **Fee-Aware Venue Scoring**:
   - Calculate effective net execution price ($\text{Price} \pm \text{Taker Fee/Rebate}$) and rank eligible venues.
3. **Liquidity Slicing & Order Book Sweeping**:
   - Slice parent order into child orders targeting top-of-book depth at NBBO venues.
   - Prioritize lower fee venues (e.g. BATS $0.0020/share taker fee vs NASDAQ $0.0030/share).
4. **Execution Output**: Output structured `SORRoutingPlan`.

> Full procedure: see `references/workflows.md`.
> Standards reference: see `references/standards.md`.
> Printable pre-flight checklist: see `assets/checklist.md`.

## Common Pitfalls

- **Trade-Through Violations (Reg NMS Rule 611 Breach)**: Routing orders to a venue at a price inferior to the NBBO, incurring regulatory fines.
- **Ignoring Exchange Taker Fees**: Routing solely on displayed price without factoring in exchange taker fees ($0.0030/share), eroding profit margins.
- **Uncoordinated Sweeps**: Sending child orders sequentially instead of concurrently, allowing high-frequency traders to front-run remaining child slices on secondary venues.

## Verification

- Instantiate `SmartOrderRoutingAcrossVenuesEngine`. Route 600-share BUY order across NASDAQ (300 @ 150.00, fee 0.0030), BATS (400 @ 150.00, fee 0.0020), and NYSE (1000 @ 150.05) $\implies$ verify NBBO price $150.00, BATS prioritized first (400 shares) due to lower fee, and NASDAQ second (200 shares). Route 2,000 shares $\implies$ verify 1,300 shares marked unrouted at NBBO level.
- Run `python scripts/test_smart_order_routing_across_venues.py`.

## Related Skills

- `smart-order-router-failover-on-venue-outage`
- `cross-venue-latency-arbitrage-defensive-design`
---
