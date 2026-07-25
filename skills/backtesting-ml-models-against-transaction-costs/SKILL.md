---
name: backtesting-ml-models-against-transaction-costs
description: >-
  Use when backtesting Machine Learning (ML) return predictions to apply rigorous Trade Frequency Drag (TCA), turnover tracking, and confidence thresholding. Prevents deploying high-turnover ML models that lose money after slippage.
domain: algorithmic-trading
subdomain: backtesting-methodology
tags: ["machine-learning", "tca", "transaction-costs", "turnover-drag", "thresholding"]
brokers_frameworks: ["NumPy", "ML Backtesting"]
version: "1.0"
author: algo-trading-skills-contributors
license: Apache-2.0
---

## When to Use

Machine learning models inherently generate highly active signals (e.g., changing predictions from slightly positive to slightly negative on every bar). Without strict Transaction Cost Analysis (TCA) and confidence thresholding, these models achieve massive theoretical Sharpe ratios that instantly collapse in live trading due to **Turnover Drag**. Invoke this skill to evaluate ML model predictions against realistic slippage and commission models, filtering out low-confidence trades that do not cover their own execution costs.

## Prerequisites

- An array of numerical ML predictions (e.g., predicted forward returns or continuous signal strength).
- An array of actual underlying returns.
- Estimated transaction costs in basis points (bps) per half-turn (e.g., 5 bps for slippage + fee).

## Workflow

1. **Set Confidence Threshold**: Determine the minimum ML prediction strength required to justify entering a trade.
2. **Generate Positions**: Map predictions to positions (+1, -1, 0) based on the threshold.
3. **Calculate Turnover**: Detect state changes (e.g., going from +1 to -1 is a turnover of 2 units).
4. **Apply Cost Drag**: Multiply turnover by the bps cost.
5. **Compute Net Returns**: Subtract the cost drag from the gross returns.

> Full procedure: see `references/workflows.md`.
> Standards reference: see `references/standards.md`.
> Printable pre-flight checklist: see `assets/checklist.md`.

## Common Pitfalls

- **Ignoring State Persistence**: Charging a transaction cost on every bar simply because the model predicted "Buy", even though the portfolio is *already* in a "Buy" state. Costs are only incurred on position *changes* (Turnover).
- **Zero Thresholding**: Taking trades on microscopic predictions (e.g., model predicts +0.0001% return) which guarantees a net loss when the transaction cost is 0.05%.
- **Symmetric Costs**: Forgetting that crossing the spread (market order) costs money on *both* entry and exit (half-turns).

## Verification

- Simulate an ML model that perfectly predicts 10% returns but flips direction every day. Apply a 6% per-turn cost. The gross return will be massive, but the net return must be decisively negative.
- Run `python scripts/test_ml_tca_backtester.py` and confirm 100% pass rate.

## Related Skills

- `transaction-cost-analysis-tca-integration`
- `execution-cost-model-recalibration-cadence`
