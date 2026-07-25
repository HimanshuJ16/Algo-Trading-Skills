---
name: risk-control-bypass-audit-logging
description: Audit logging for manual risk overrides.
domain: risk-management
subdomain: audit
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

Use this skill when implementing Audit logging for manual risk overrides.

## Prerequisites

- Python 3.10+
- Pandas
- Appropriate broker credentials if live

## Workflow

1. Initialize `RiskOverrideAuditLogger`.
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
