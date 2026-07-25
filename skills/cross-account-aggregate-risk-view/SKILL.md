---
name: cross-account-aggregate-risk-view
description: Aggregating risk across multi-account entities under common control.
domain: risk-management
subdomain: aggregation
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

Use this skill when implementing Aggregating risk across multi-account entities under common control.

## Prerequisites

- Python 3.10+
- Pandas
- Appropriate broker credentials if live

## Workflow

1. Initialize `CrossAccountRiskAggregator`.
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
