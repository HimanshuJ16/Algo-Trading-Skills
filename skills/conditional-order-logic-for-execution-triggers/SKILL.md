---
name: conditional-order-logic-for-execution-triggers
description: Quantitative execution engine for evaluating nested Boolean condition
  trees (price, volume, time, cross-asset benchmarks) to trigger conditional order
  submission.
domain: Execution Algorithms
subdomain: Order Logic & Triggers
tags:
- conditional-orders
- execution-triggers
- boolean-tree
- cross-asset-trigger
- oms
- ems
brokers_frameworks:
- Generic Execution
- Python Dataclasses
version: "1.0.0"
author: algo-trading-skills-contributors
license: Apache-2.0
---

## When to Use

Use this skill when implementing automated conditional order logic (If-Touched, Bracket, One-Cancels-the-Other / OCO, or Cross-Asset triggers) in an Order/Execution Management System (OMS/EMS). Conditional orders remain dormant in local memory until incoming market data ticks evaluate a nested Boolean condition tree to `TRUE`, upon which child orders are released for pre-trade risk checking and venue routing.

## Prerequisites

- Streaming market data feed for target instrument and benchmark reference instruments.
- Unique client order identifier and child order execution specification.

## Workflow

1. **Condition Tree Construction**: Define atomic condition nodes (`PriceCondition`, `VolumeCondition`, `TimeCondition`, `CrossAssetCondition`) and combine them using `AndCondition` / `OrCondition` trees.
2. **Conditional Order Registration**: Instantiate `ConditionalOrderTrigger` pairing the condition tree with a child order payload (`symbol`, `side`, `quantity`, `order_type`).
3. **Tick Processing**: On each market tick update, evaluate `trigger.condition_tree.evaluate(market_state)`.
4. **Trigger Firing**:
   - If evaluation is `TRUE`, mark trigger as `TRIGGERED`.
   - Release child order payload to OMS/EMS for execution.
   - Deactivate trigger to prevent duplicate executions.

> Full procedure: see `references/workflows.md`.
> Standards reference: see `references/standards.md`.
> Printable pre-flight checklist: see `assets/checklist.md`.

## Common Pitfalls

- **Firing Duplicate Orders**: Failing to atomically mark a conditional order as `TRIGGERED` after the condition evaluates to `TRUE`, causing subsequent ticks to release duplicate child orders.
- **Blocking the Hot Path**: Executing heavy string parsing or complex network calls during Boolean tree evaluation inside the market data callback thread.
- **Ignoring Stale Benchmark Quotes**: Evaluating a cross-asset condition (e.g. `SPY > 500`) when the benchmark quote timestamp is stale or disconnected.

## Verification

- Instantiate `ConditionalOrderEngine`. Build a Boolean AND condition: `(AAPL.last >= 150.00) AND (SPY.last >= 500.00)`. Feed a market update where `AAPL` is 150.50 but `SPY` is 499.00. Verify the order remains `DORMANT`. Feed an update with `SPY` at 500.50. Verify the order transitions to `TRIGGERED` and yields a child order payload.
- Run `python scripts/test_conditional_order_logic_for_execution_triggers.py`.

## Related Skills

- `close-auction-participation-strategy`
- `execution-algorithm-kill-switch-integration`
---
