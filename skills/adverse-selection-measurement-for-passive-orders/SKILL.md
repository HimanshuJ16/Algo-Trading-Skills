---
name: adverse-selection-measurement-for-passive-orders
description: Computes markout curves for passive limit order fills across multiple
  time horizons to quantify adverse selection and toxic liquidity exposure.
domain: algorithmic-trading
subdomain: execution-quality
tags:
- execution
- trading
- adverse-selection
- markouts
- market-microstructure
brokers_frameworks:
- generic
version: "1.1.0"
author: System
license: MIT
---

## When to Use

Use this skill to evaluate the execution quality of market-making or passive liquidity-providing algorithms. If your algorithm's passive limit orders are consistently filled right before the market moves against you (e.g., you buy right before a price drop), you are suffering from adverse selection ("toxic flow"). This skill calculates Post-Trade Markouts in basis points (bps) to quantify this leakage.

## Prerequisites

- Python 3.9+
- A ledger of passive order fills (timestamp, side, execution price).
- High-resolution historical market mid-prices.

## Workflow

1. **Ingest Fills**: Load your passive limit order fills.
2. **Define Horizons**: Specify the forward time horizons for markout measurement (e.g., 100ms, 1s, 10s, 60s).
3. **Compute Markouts**: For each fill, find the market mid-price exactly $T$ seconds after the fill timestamp.
4. **Calculate Bps Difference**:
   - Buy Orders: `(Future_Mid / Fill_Price - 1) * 10000`
   - Sell Orders: `(Fill_Price / Future_Mid - 1) * 10000`
5. **Aggregate**: Output the average markout curve. A persistently negative curve means you are being adversely selected by informed flow.

## Common Pitfalls

- **Ignoring Time Horizons**: Measuring only at EOD (End of Day). Microstructure toxicity happens in milliseconds to seconds; if you only check 1 hour later, alpha decay hides the execution friction.
- **Directional Sign Errors**: Failing to invert the calculation for Sell orders. 

## Verification

Run `python scripts/test_adverse_selection_measurement_for_passive_orders.py` to verify that a toxic buy order (where price drops after fill) correctly registers as a negative markout.

## Related Skills

- `post-trade-execution-quality-scorecard`
- `execution-slippage-attribution-timing-vs-sizing`
