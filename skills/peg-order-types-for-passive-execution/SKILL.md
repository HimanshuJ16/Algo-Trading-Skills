---
name: peg-order-types-for-passive-execution
description: Execution algorithm for peg order types for passive execution
domain: execution-algorithms
subdomain: execution-strategies
tags:
  - execution
  - trading
  - algo
brokers_frameworks:
  - generic
version: 1.0.0
author: assistant
license: MIT
---

## When to Use
Use when implementing the peg order types for passive execution strategy.

## Prerequisites
- Python 3.10+
- Pandas, NumPy

## Workflow
1. Load configurations
2. Monitor market data
3. Execute based on algorithm rules

## Common Pitfalls
- Handling partial fills improperly
- Delay in order routing

## Verification
Run `pytest` or `unittest` on the `scripts/test_peg_order_types_for_passive_execution.py` file.

## Related Skills
- VWAP execution
- TWAP execution
