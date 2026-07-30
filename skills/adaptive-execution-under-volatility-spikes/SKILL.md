---
name: adaptive-execution-under-volatility-spikes
description: Execution algorithm logic for dynamically adjusting order sizing, participation
  rates, and routing during severe market volatility and flash crashes.
domain: execution-algorithms
subdomain: execution-strategies
tags:
- execution
- trading
- algo
- volatility
- flash-crash
- risk-management
brokers_frameworks:
- generic
version: 1.0.0
author: System
license: MIT
---

## When to Use

Use this execution overlay when running TWAP, VWAP, or POV (Percentage of Volume) execution algorithms that need to survive sudden market dislocations, flash crashes, or liquidity mirages. It dynamically reduces participation rates, shrinks child order sizes, and halts trading if volatility exceeds critical safety thresholds.

## Prerequisites

- Python 3.10+
- Access to real-time market data to compute short-term realized volatility (e.g., micro-ATR or rolling standard deviation).
- A base execution scheduler (e.g., TWAP/VWAP).

## Workflow

1. **Calculate Micro-Volatility**: Continuously monitor real-time spread and short-term price variance.
2. **Regime Classification**: Classify the market state as `NORMAL`, `HIGH_VOLATILITY`, or `CRITICAL_SHOCK`.
3. **Parameter Adaptation**:
   - `NORMAL`: Use standard participation rates (e.g., 10% POV) and normal child sizes.
   - `HIGH_VOLATILITY`: Halve the participation rate to reduce market impact and toxic liquidity exposure. Increase limit offsets to prevent rejected orders.
   - `CRITICAL_SHOCK`: Halt execution immediately (trigger circuit breaker) to prevent pro-cyclical cascading losses.

## Common Pitfalls

- **Algorithmic Feedback Loops**: Blindly continuing to execute aggressive market orders during a flash crash, which amplifies the crash.
- **Liquidity Mirage**: Assuming order book depth is real during high volatility; HFT market makers often withdraw quotes, meaning large child orders will cause massive slippage.
- **Static Stop-Losses**: Relying on static price-based stop losses which suffer extreme slippage during gapping markets.

## Verification

Run the provided unit tests to verify the regime switching logic and parameter adjustments.

## Related Skills

- VWAP execution
- TWAP execution
- execution-algorithm-kill-switch-integration
