---
name: short-selling-borrow-cost-and-availability-modeling
description: Modeling stock-borrow cost and availability constraints in a backtest
  for any strategy that shorts equities.
domain: Backtesting
subdomain: Constraints
tags:
- backtesting
- short-selling
- borrow-cost
- constraints
brokers_frameworks:
- QuantConnect
- Zipline
version: 1.0.0
author: System
license: MIT
---

# Short Selling Borrow Cost and Availability Modeling

## When to Use
Use this skill when developing or backtesting a strategy that involves short selling equities. Modeling borrow constraints (HTB fees, availability) is critical because ignoring them often results in highly unrealistic returns for short strategies, particularly on small caps or high-short-interest names.

## Prerequisites
- Familiarity with short selling mechanics.
- Understanding of General Collateral (GC) versus Hard-To-Borrow (HTB) rates.
- Historical data on short utilization or explicit historical borrow rates (if available).

## Workflow
1. Initialize the `BorrowCostModeler`.
2. Supply utilization estimates or HTB status for the assets being shorted.
3. Check availability before placing short orders.
4. Calculate the annualized borrow cost drag on returns.

## Common Pitfalls
- **Ignoring Borrow Costs:** Assuming zero borrow fees leads to overestimating short strategy performance.
- **Assuming Unlimited Availability:** Assuming you can always short any stock, leading to trades that are practically impossible.
- **Static Rates:** Assuming a static borrow rate for HTB stocks, when in reality these rates fluctuate wildly based on supply/demand.

## Verification
- Ensure that GC and HTB rates are applied correctly based on the utilization inputs.
- Ensure that shorts are rejected if availability drops to zero.
- Verify that borrow cost accurately drags down overall returns.

## Related Skills
- `backtest-transaction-cost-modeling`
- `slippage-modeling`
