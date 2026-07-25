---
name: adaptive-execution-under-volatility-spikes
description: Execution algorithm for adaptive execution under volatility spikes
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
Use when implementing the adaptive execution under volatility spikes strategy.

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
Run `pytest` or `unittest` on the `scripts/test_adaptive_execution_under_volatility_spikes.py` file.

## Related Skills
- VWAP execution
- TWAP execution
