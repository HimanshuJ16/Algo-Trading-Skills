---
name: queue-position-modeling-for-passive-orders
description: >-
  FIFO limit order book queue position tracking engine estimating volume ahead, fill probabilities, and defensive order cancellations for passive limit orders.
domain: Execution Algorithms
subdomain: Market Microstructure & Queue Priority
tags: ["queue-position", "fifo-order-book", "passive-execution", "adverse-selection", "microstructure", "fill-probability"]
brokers_frameworks: ["L2/L3 Order Book Microstructure Standards", "Python Dataclasses"]
version: "1.0.0"
author: algo-trading-skills-contributors
license: Apache-2.0
---

## When to Use

Use this skill when deploying passive liquidity-providing strategies (market making, peg orders, TWAP/VWAP passive slices) in FIFO limit order books. Resting limit orders must wait behind existing volume ahead ($Q_{\text{ahead}}$) before receiving execution. This engine tracks trade fills and cancellations, updates estimated queue rank in real-time, models fill probability ($P_{\text{fill}}$), and triggers defensive cancellations when queue position deteriorates or adverse selection risks increase.

## Prerequisites

- Order tracking parameters (`order_id`, `side`: `'BUY'`/`'SELL'`, `price`, `our_quantity`, `initial_queue_ahead`, `total_level_volume`).
- Config options (`cancellation_share_alpha`: default 0.5).

## Workflow

1. **Queue Position Initialization**:
   - Record initial volume ahead ($Q_{\text{ahead}}^{(0)}$) and total volume at price level ($Q_{\text{total}}$).
2. **Trade Fill & Cancellation Update**:
   - Subtract 100% of trade fills at price level: $Q_{\text{ahead}} \leftarrow \max(0, Q_{\text{ahead}} - V_{\text{fill}})$.
   - Subtract proportional cancellations: $Q_{\text{ahead}} \leftarrow \max(0, Q_{\text{ahead}} - \alpha \cdot V_{\text{cancel}})$ where $\alpha = \frac{Q_{\text{ahead}}}{Q_{\text{total}}}$.
3. **Queue Rank & Fill Probability Calculation**:
   - Compute rank $\text{Rank} = \lfloor Q_{\text{ahead}} / \text{AverageOrderSize} \rfloor + 1$.
   - Estimate Fill Probability $P_{\text{fill}} = \min\left(1.0, \frac{\text{TradeRate} \cdot \Delta t}{Q_{\text{ahead}} + \text{OurQty}}\right)$.
4. **Audit Report Generation**: Output structured `QueuePositionReport`.

> Full procedure: see `references/workflows.md`.
> Standards reference: see `references/standards.md`.
> Printable pre-flight checklist: see `assets/checklist.md`.

## Common Pitfalls

- **Ignoring Cancellation Distribution**: Assuming all cancellations happen behind our order, overestimating queue priority.
- **Static Queue Assumptions**: Failing to update $Q_{\text{ahead}}$ dynamically as market trades execute at the bid/ask.
- **Holding Toxic Front-of-Queue Positions**: Failing to cancel passive orders when order book imbalance turns toxic against our position.

## Verification

- Instantiate `QueuePositionModelEngine`. Submit BUY order (Our Qty $= 100$, Initial $Q_{\text{ahead}} = 1,000$). Process $500$ fill volume $\implies$ verify $Q_{\text{ahead}}$ drops to $500$, rank improves to front half, and `ESTIMATING_QUEUE_PRIORITY` status generated.
- Run `python scripts/test_queue_position_modeling_for_passive_orders.py`.

## Related Skills

- `post-only-limit-repricing-under-fast-markets`
- `order-book-imbalance-signal-pipeline`
---
