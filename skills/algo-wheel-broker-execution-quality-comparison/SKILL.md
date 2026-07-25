---
name: algo-wheel-broker-execution-quality-comparison
description: Implements an Algorithmic Wheel that dynamically routes orders to brokers based on historical Transaction Cost Analysis (TCA), explicitly minimizing Implementation Shortfall (IS).
domain: execution-algorithms
subdomain: smart-order-routing
tags:
  - execution
  - algo-wheel
  - tca
  - implementation-shortfall
  - broker-routing
brokers_frameworks:
  - generic
version: 1.1.0
author: System
license: MIT
---

## When to Use

Use this skill to systematically remove human bias from broker selection. Instead of a human trader defaulting to their favorite broker, the "Algo Wheel" automatically assigns flow based on quantitative execution quality. This skill calculates the **Implementation Shortfall (IS)** in Basis Points (bps) for past trades and mathematically rewards the best-performing broker with a higher weight (more order flow).

## Prerequisites

- Python 3.9+
- A database of historical order executions including the original decision price (arrival price), the final fill price, and explicit fees.

## Workflow

1. **Ingest Historical Executions**: Load past trades into the `AlgoWheelEvaluator`.
2. **Calculate IS**: Compute the Implementation Shortfall for every trade.
   - For Buy orders: `((Fill_Price - Decision_Price) / Decision_Price) * 10000 + Fees_bps`
   - For Sell orders: `((Decision_Price - Fill_Price) / Decision_Price) * 10000 + Fees_bps`
3. **Rank Brokers**: Aggregate the IS scores per broker. The broker with the lowest average IS (least slippage and fees) is ranked #1.
4. **Dynamic Routing**: Update the Algo Wheel weights. Reward top performers with 50% of flow, demote underperformers to 10% of flow (canary testing).

## Common Pitfalls

- **Ignoring Direction**: Failing to invert the math for Sell orders.
- **Ignoring Fees**: A broker might offer zero slippage but charge massive explicit commissions, ultimately hurting fund performance. Fees must be included in the IS calculation.
- **Starvation**: Setting an underperforming broker's weight to 0%. If they are removed entirely, the wheel can never test if their algorithms have improved. Always maintain a minimum canary flow (e.g., 5-10%).

## Verification

Run `python scripts/test_algo_wheel_broker_execution_quality_comparison.py` to ensure the TCA math accurately identifies the lowest-cost broker and correctly assigns wheel weights.

## Related Skills

- `implementation-shortfall-minimization`
- `post-trade-execution-quality-scorecard`
