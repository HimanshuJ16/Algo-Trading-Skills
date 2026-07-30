---
name: post-only-and-maker-taker-fee-optimization
description: Use when deploying order execution algorithms to exchanges or brokers
  with maker-taker fee structures to inject Post-Only order flags, guarantee maker
  fee tiers, handle spread-crossing order cancellations, and quantify net transaction
  fee savings.
domain: algorithmic-trading
subdomain: broker-integration
tags:
- broker-integration
- post-only
- maker-taker
- fee-optimization
- liquidity-provider
- execution-cost
brokers_frameworks:
- Exchange Fee Optimizer
- Python Trading Engine
version: '1.0'
author: algo-trading-skills-contributors
license: Apache-2.0
---

## When to Use

Invoke this skill when executing passive liquidity-providing quantitative strategies (market making, statistical arbitrage, passive rebalancing) on exchanges or brokers that employ maker-taker fee schedules (e.g., Coinbase Advanced, Binance, IBKR Pro, Bybit). Taker orders that cross the spread incur 2x to 5x higher commission fees. Injecting `Post-Only` flags guarantees that orders are accepted strictly as liquidity-adding (Maker) orders or cancelled without executing at taker fee rates.

## Prerequisites

- Exchange or broker support for Post-Only order flags (`post_only=True`, `time_in_force="POC"`, or `execInst="ParticipateDoNotInitiate"`).
- Current exchange fee schedule (Maker Fee % vs Taker Fee %).

## Workflow

1. **Configure Maker-Taker Fee Schedule**:
   - Register exchange maker fee rate (e.g. 0.05% or 5 bps) and taker fee rate (e.g. 0.25% or 25 bps).

2. **Inject Post-Only Order Flags**:
   - Attach post-only parameter to limit order requests.

3. **Handle Post-Only Spread-Crossing Cancellations**:
   - If order is cancelled because limit price would cross bid/ask spread, recalculate limit price to passive side (e.g., `best_bid` for Buy, `best_ask` for Sell) or evaluate if taker execution is explicitly justified by signal urgency.

4. **Quantify Cumulative Fee Savings**:
   - Calculate net fee savings:
     $$\text{Savings} = \sum \text{Volume}_{\text{USD}} \times (\text{Rate}_{\text{taker}} - \text{Rate}_{\text{maker}})$$

> Full procedure: see `references/workflows.md`.
> Standards reference: see `references/standards.md`.
> Printable pre-flight checklist: see `assets/checklist.md`.

## Common Pitfalls

- **Endless Cancellation Loops**: Repeatedly resubmitting Post-Only limit orders at crossing prices, leading to excessive API order placement cancellations.
- **Unintended Taker Executions on Non-Post-Only Orders**: Omitting the post-only flag on limit orders placed inside the spread.
- **Ignoring Urgency Multipliers**: Using Post-Only flags on stop-loss or emergency risk liquidation orders where immediate execution is far more critical than fee savings.

## Verification

- Submit limit order with post-only flag and verify flag injection in payload.
- Simulate post-only rejection on spread crossing and verify passive price repricing.
- Run `python scripts/test_fee_optimizer.py` and confirm 100% pass rate.

## Related Skills

- `execution-algo-twap-vwap-slicing`
- `broker-order-type-capability-matrix`
- `paper-to-live-promotion-checklist`
---
