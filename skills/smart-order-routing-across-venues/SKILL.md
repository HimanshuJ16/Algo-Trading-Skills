---
name: smart-order-routing-across-venues
description: Execution algorithm for smart order routing across venues
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
Use when implementing the smart order routing across venues strategy.

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
Run `pytest` or `unittest` on the `scripts/test_smart_order_routing_across_venues.py` file.

## Related Skills
- VWAP execution
- TWAP execution
