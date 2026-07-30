---
name: dark-pool-routing-logic
description: >-
  Quantitative Smart Order Router (SOR) module for routing block orders across dark pools / ATS, enforcing anti-pinging MinQty thresholds, and filtering toxic venues.
domain: Execution Algorithms
subdomain: Smart Order Routing & ATS
tags: ["dark-pool", "ats-routing", "smart-order-router", "min-qty", "adverse-selection", "toxic-flow-filtering", "midpoint-execution"]
brokers_frameworks: ["FIX Protocol", "Python Dataclasses"]
version: "1.0.0"
author: algo-trading-skills-contributors
license: Apache-2.0
---

## When to Use

Use this skill when designing institutional Smart Order Routers (SOR) or execution algorithms slicing parent block orders into non-displayed Alternative Trading Systems (ATS / Dark Pools). Dark pools offer midpoint executions without pre-trade market impact. However, predatory high-frequency traders use small 1-share "pinging" orders to detect block presence, subjecting institutional orders to adverse selection. This module scores dark venues by fill rate vs post-trade toxicity, filters out toxic venues, and attaches `MinQty` (Minimum Quantity) instructions to prevent pinging.

## Prerequisites

- ATS venue profiles (`venue_id`, `historical_fill_rate`, `toxicity_score_bps`, `min_qty_threshold`).
- Parent block order details (`symbol`, `side`, `total_quantity`, `max_acceptable_toxicity_bps`).

## Workflow

1. **ATS Venue Filtering**:
   - Filter out venues where $\text{Toxicity}_v > \text{MaxToxicity}$.
   - Exclude venues with fill rates below threshold ($\text{FillRate}_v < 0.05$).
2. **Allocation Scoring**:
   - Compute Venue Score: $S_v = \text{FillRate}_v \times \max\left(0.0, 1.0 - \frac{\text{Toxicity}_v}{50.0}\right)$.
3. **Child Order Slicing & MinQty Attachment**:
   - Allocate parent quantity across top scored venues proportionally to $S_v$.
   - Attach Minimum Quantity instruction: $\text{MinQty} = \max(\text{VenueMinQty}, \text{ChildQty} \times 0.20)$.
4. **Audit Report Generation**: Output structured `DarkPoolRoutingReport`.

> Full procedure: see `references/workflows.md`.
> Standards reference: see `references/standards.md`.
> Printable pre-flight checklist: see `assets/checklist.md`.

## Common Pitfalls

- **Omission of MinQty Constraints**: Routing dark IOC orders without a `MinQty`, allowing HFTs to ping 1 share, detect the hidden block, and front-run on lit venues.
- **Routing to Toxic Dark Pools**: Ignoring post-trade markout PnL and continuing to route to dark pools with high adverse selection toxicity ($\ge 5.0\text{ bps}$).
- **Over-allocating to Low-Liquidity Pools**: Sending large child quantities to illiquid dark pools, causing high cancellation ratios and opportunity costs.

## Verification

- Instantiate `DarkPoolRoutingEngine`. Register 3 Dark Pools: Pool A (FillRate=0.40, Toxicity=1.0bps), Pool B (FillRate=0.50, Toxicity=8.0bps), Pool C (FillRate=0.10, Toxicity=0.5bps). Input parent order of 10,000 shares with MaxToxicity=5.0bps. Verify engine excludes toxic Pool B, allocates majority to Pool A, and attaches `MinQty` parameters to child orders.
- Run `python scripts/test_dark_pool_routing_logic.py`.

## Related Skills

- `post-trade-execution-quality-scorecard`
- `smart-order-router-failover-on-venue-outage`
---
