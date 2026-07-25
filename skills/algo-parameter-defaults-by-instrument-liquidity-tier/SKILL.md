---
name: algo-parameter-defaults-by-instrument-liquidity-tier
description: Dynamically assigns algorithmic execution parameters (participation rates, aggression, TWAP/VWAP defaults) based on the instrument's liquidity tier (ADV).
domain: algorithmic-trading
subdomain: execution-algorithms
tags:
  - execution
  - smart-order-routing
  - market-impact
  - twap
  - vwap
brokers_frameworks:
  - generic
version: 1.1.0
author: System
license: MIT
---

## When to Use

Use this skill when initializing algorithmic execution engines (like VWAP or Implementation Shortfall). Applying the same default parameters (e.g., 10% participation rate) across a massive trading universe will cause massive market impact in illiquid names while failing to capture available volume in liquid names. This skill dynamically sets parameters based on Average Daily Volume (ADV) tiers.

## Prerequisites

- Python 3.9+
- A data source providing 30-day Average Daily Volume (ADV) for the instrument universe.

## Workflow

1. **Calculate ADV**: Compute or ingest the instrument's recent ADV.
2. **Classify Tier**: Route the ADV through the `ExecutionParameterManager` to classify the asset as `HIGH`, `MEDIUM`, or `LOW` liquidity.
3. **Assign Profile**: Retrieve the `ExecutionProfile`.
    - **High Liquidity**: Lower participation (to avoid market impact), passive aggression, defaults to TWAP/VWAP.
    - **Low Liquidity**: Higher relative participation (must capture whatever liquidity appears), conservative limit buffers, defaults to Implementation Shortfall (IS).

## Common Pitfalls

- **Static Defaults**: Hardcoding `participation_rate = 0.10` in a base class, leading to predatory HFTs front-running predictable child orders in thin books.
- **Crossing Spreads in Illiquid Names**: Allowing an algorithm to "cross the spread" (pay the offer to buy) in a low-liquidity tier guarantees massive slippage.

## Verification

Run `python scripts/test_algo_parameter_defaults_by_instrument_liquidity_tier.py` to assert that low-liquidity assets are assigned Implementation Shortfall profiles and forbidden from crossing the spread.

## Related Skills

- `implementation-shortfall-minimization`
- `participation-of-volume-pov-execution`
