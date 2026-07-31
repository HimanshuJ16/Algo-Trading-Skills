---
name: liquidity-seeking-algorithm-across-lit-and-dark-venues
description: >-
  Institutional Smart Order Routing (SOR) engine executing parent orders across Lit exchanges and Dark ATS venues, sweeping NBBO midpoint dark liquidity prior to lit book routing.
domain: Execution Algorithms
subdomain: Smart Order Routing (SOR) & Dark Pools
tags: ["liquidity-seeking", "dark-pools", "lit-venues", "nbbo-midpoint", "sor", "smart-order-router", "signal-leakage"]
brokers_frameworks: ["SEC Rule 611 Order Protection", "SEC Rule 612 Sub-Penny", "Python Dataclasses"]
version: "1.0.0"
author: algo-trading-skills-contributors
license: Apache-2.0
---

## When to Use

Use this skill when executing large institutional parent orders (e.g. 50,000 shares) across fragmented equity markets containing both displayed **Lit Exchanges** (NASDAQ, NYSE, Cboe) and non-displayed **Dark Venues (ATS - Alternative Trading Systems)**. Routing large orders directly to Lit exchanges causes immediate adverse market impact and signaling leakage. This module sweeps non-displayed dark pools at the **NBBO Midpoint** using Immediate-Or-Cancel (IOC) pings before routing remaining balance to Lit exchanges.

## Prerequisites

- Parent order payload (`symbol`, `side`: `BUY`/`SELL`, `target_quantity`, `limit_price`, `min_dark_fill_qty`).
- List of venue order books (`venue_id`, `venue_type`: `DARK`/`LIT`, `bid_price`, `ask_price`, `bid_qty`, `ask_qty`, `fill_rate_history`).

## Workflow

1. **NBBO Midpoint & Price Limit Calculation**:
   - Compute National Best Bid ($P_{\text{NBB}}$) and National Best Offer ($P_{\text{NBO}}$).
   - Compute NBBO Midpoint price: $P_{\text{mid}} = \frac{P_{\text{NBB}} + P_{\text{NBO}}}{2.0}$.
2. **Stage 1 - Dark Venue Midpoint Sweep**:
   - Sort Dark ATS venues by historical fill rate.
   - Allocate IOC dark pings at $P_{\text{mid}}$ to dark venues where venue available liquidity $\ge Q_{\text{min\_dark}}$.
   - Update executed dark volume and calculate remaining unfilled quantity $Q_{\text{rem}}$.
3. **Stage 2 - Lit Venue Fallback Routing**:
   - Route remaining balance $Q_{\text{rem}}$ to Lit venues at NBBO, allocating proportional to displayed book depth.
4. **Audit Report Generation**: Output structured `LiquiditySeekingReport`.

> Full procedure: see `references/workflows.md`.
> Standards reference: see `references/standards.md`.
> Printable pre-flight checklist: see `assets/checklist.md`.

## Common Pitfalls

- **Pinging Without Minimum Quantity Limits**: Sending small 100-share pings to dark pools, exposing institutional order intent to High-Frequency Trading (HFT) latency arbitrageurs.
- **Violating SEC Rule 611 (Trade-Through Rule)**: Routing lit child orders at prices inferior to the NBBO.
- **Ignoring Dark Pool Fill Rate Decay**: Continuing to allocate dark pings to low-fill-rate ATS venues, delaying order completion.

## Verification

- Instantiate `LiquiditySeekingEngine`. Execute 20,000 share BUY order across 2 Dark ATS venues and 2 Lit exchanges (NBBO $100.00 \times 100.02$, Midpoint $100.01$). Dark Venue 1 fills 12,000 shares at $100.01$ midpoint $\implies$ verify Lit exchanges execute remaining 8,000 shares at $100.02$, saving $\$0.01$/share ($100$ USD price improvement) and approves `LIQUIDITY_SEEKING_COMPLETE`.
- Run `python scripts/test_liquidity_seeking_algorithm_across_lit_and_dark_venues.py`.

## Related Skills

- `cross-venue-latency-arbitrage-defensive-design`
- `smart-order-router-failover-on-venue-outage`
---
