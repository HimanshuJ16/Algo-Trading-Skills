---
name: multi-order-netting-before-routing
description: >-
  Multi-order pre-routing netting engine matching internal buy/sell orders at mid-price before routing residual net orders to external exchanges.
domain: Execution Algorithms
subdomain: Pre-Routing Internal Order Netting & Cost Optimization
tags: ["multi-order-netting", "pre-routing", "internal-crossing", "order-routing", "cost-savings", "spread-savings"]
brokers_frameworks: ["Internal Crossing Engine", "Smart Order Routing (SOR)", "Python Dataclasses"]
version: "1.0.0"
author: algo-trading-skills-contributors
license: Apache-2.0
---

## When to Use

Use this skill when multiple internal strategies, trading sub-accounts, or portfolio desks generate simultaneous buy and sell orders on the same security within a short batching interval. Routing opposing un-netted orders directly to market exchanges incurs double exchange fee penalties, spread crossing costs, and market impact. Pre-routing internal netting matches opposing buy/sell orders internally at the current mid-price ($P_{\text{mid}}$) and routes only the net residual quantity to external market venues.

## Prerequisites

- Internal order batch (`strategy_id`, `symbol`, `side`: `'BUY'`/`'SELL'`, `quantity`: int).
- Market quote payload (`symbol`, `bid_price`, `ask_price`, `fee_per_share_usd`: e.g. 0.003).

## Workflow

1. **Batch Ingestion & Side Aggregation**:
   - Sum total buy volume $Q_{\text{buy}}$ and total sell volume $Q_{\text{sell}}$ across incoming internal orders for symbol $S$.
2. **Internal Mid-Price Crossing**:
   - Compute internal match price: $P_{\text{mid}} = \frac{P_{\text{bid}} + P_{\text{ask}}}{2}$.
   - Determine internal matched quantity: $Q_{\text{matched}} = \min(Q_{\text{buy}}, Q_{\text{sell}})$.
   - Allocate internal fills at $P_{\text{mid}}$ to participating buy and sell strategies.
3. **Net Residual Routing Sizing**:
   - Calculate net residual quantity: $Q_{\text{residual}} = |Q_{\text{buy}} - Q_{\text{sell}}|$.
   - Generate single external market order for $Q_{\text{residual}}$ on the dominant side.
4. **Execution Cost Savings Audit**:
   - Compute exchange fee savings ($2 \times Q_{\text{matched}} \times \text{Fee}$) and spread crossing savings ($Q_{\text{matched}} \times \text{Spread}$).
5. **Audit Report Generation**: Output structured `NettingReport`.

> Full procedure: see `references/workflows.md`.
> Standards reference: see `references/standards.md`.
> Printable pre-flight checklist: see `assets/checklist.md`.

## Common Pitfalls

- **Bypassing Internal Netting**: Sending raw un-netted buy and sell orders to the exchange, paying double taker fees and bid-ask spreads.
- **Unfair Internal Fill Allocation**: Failing to allocate internal fills pro-rata across strategies when internal buy/sell demand is partially matched.
- **Out-of-Date Mid-Prices**: Matching internal orders using stale bid-ask quotes, leading to internal price dislocation.

## Verification

- Instantiate `MultiOrderNettingEngine`. Audit batch of Buy 500 AAPL (Strategy A) and Sell 300 AAPL (Strategy B) @ Bid $150.00 / Ask $150.10 $\implies$ verify $300$ shares crossed internally @ $150.05 mid-price, Buy 200 AAPL residual routed externally, and total cost savings audited.
- Run `python scripts/test_multi_order_netting_before_routing.py`.

## Related Skills

- `smart-order-router-failover-on-venue-outage`
- `minimum-fill-size-and-lot-rounding-logic`
---
