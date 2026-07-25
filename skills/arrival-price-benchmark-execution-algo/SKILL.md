---
name: arrival-price-benchmark-execution-algo
description: Implementation Shortfall (IS) execution algorithm generating optimal trading trajectories based on the Almgren-Chriss framework and trader urgency.
domain: execution-algorithms
subdomain: execution-strategies
tags:
  - execution
  - implementation-shortfall
  - arrival-price
  - almgren-chriss
  - urgency
brokers_frameworks:
  - generic
version: 1.1.0
author: System
license: MIT
---

## When to Use

Use this skill to calculate the optimal trading schedule for executing a large parent order when the portfolio manager is being benchmarked against the **Arrival Price** (the mid-price at the exact moment the trading decision was made).

This execution algorithm minimizes **Implementation Shortfall (IS)** by balancing two competing costs:
1. **Market Impact**: Trading too fast moves the price against you.
2. **Timing Risk (Volatility)**: Trading too slow exposes the unfilled order to adverse price drift.

## Prerequisites

- Python 3.9+
- Total parent order size, execution time horizon, and a defined `UrgencyLevel`.

## Workflow

1. **Define Urgency**: The portfolio manager defines the urgency of the trade (`HIGH`, `MEDIUM`, `LOW`), which proxies the risk-aversion parameter ($\lambda$) in the Almgren-Chriss framework.
2. **Generate Trajectory**: The engine calculates an execution schedule (an array of order sizes per time bin).
   - **HIGH Urgency**: The trajectory is heavily "front-loaded" to capture the arrival price immediately, accepting higher market impact to eliminate timing risk.
   - **MEDIUM Urgency**: The trajectory balances impact and risk.
   - **LOW Urgency**: The trajectory approaches a uniform TWAP (Time-Weighted Average Price) schedule, minimizing immediate market impact.
3. **Execute Child Orders**: Route the child orders to the market according to the generated schedule.

## Common Pitfalls

- **Ignoring Alpha Decay**: Using a `LOW` urgency setting for an alpha signal that decays in minutes. By the time the order finishes executing, the arrival price will have moved significantly.
- **Over-trading Illiquid Names**: Using `HIGH` urgency on an illiquid micro-cap stock, causing catastrophic market impact that destroys the trade's PnL.

## Verification

Run `python scripts/test_arrival_price_benchmark_execution_algo.py` to confirm that the generated trajectories properly front-load orders for high urgency and flatten out for low urgency.

## Related Skills

- `implementation-shortfall-minimization`
- `execution-slippage-attribution-timing-vs-sizing`
