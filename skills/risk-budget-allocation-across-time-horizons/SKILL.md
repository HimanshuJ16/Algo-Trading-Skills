---
name: risk-budget-allocation-across-time-horizons
description: Risk budgeting across intraday, swing, and position holding periods.
domain: risk-management
subdomain: risk-budgeting
tags:
  - risk
  - management
brokers_frameworks:
  - ccxt
  - interactive-brokers
version: 1.0.0
author: System
license: MIT
---

## When to Use

Use this skill when implementing Risk budgeting across intraday, swing, and position holding periods.

## Prerequisites

- Python 3.10+
- Pandas
- Appropriate broker credentials if live

## Workflow

1. Initialize `HorizonRiskAllocator`.
2. Provide necessary data inputs.
3. Call the execution method.

## Common Pitfalls

- Not accounting for network latency.
- Incorrect parameter parsing.

## Verification

- Run unit tests.
- Verify logs in paper-trading environment.

## Related Skills

- backtesting-framework
- position-sizing
