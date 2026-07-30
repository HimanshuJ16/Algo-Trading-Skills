---
name: real-time-greeks-recalculation-on-market-moves
description: Real-time Greeks recalculation on tick/price shifts.
domain: risk-management
subdomain: greeks
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

Use this skill when implementing Real-time Greeks recalculation on tick/price shifts.

## Prerequisites

- Python 3.10+
- Pandas
- Appropriate broker credentials if live

## Workflow

1. Initialize `RealTimeGreeksRecalculator`.
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
