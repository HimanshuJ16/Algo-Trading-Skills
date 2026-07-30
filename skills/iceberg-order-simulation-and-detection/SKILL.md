---
name: iceberg-order-simulation-and-detection
description: Execution algorithm for iceberg order simulation and detection
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
Use when implementing the iceberg order simulation and detection strategy.

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
Run `pytest` or `unittest` on the `scripts/test_iceberg_order_simulation_and_detection.py` file.

## Related Skills
- VWAP execution
- TWAP execution
