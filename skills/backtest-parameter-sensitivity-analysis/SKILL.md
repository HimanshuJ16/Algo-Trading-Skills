---
name: backtest-parameter-sensitivity-analysis
description: >-
  Use when evaluating strategy robustness by perturbing backtest parameters across a grid and measuring Sharpe ratio sensitivity to detect overfitting sweet spots vs genuinely robust parameter plateaus.
domain: algorithmic-trading
subdomain: backtesting-methodology
tags: ["backtesting-methodology", "parameter-sensitivity", "overfitting-detection", "grid-search", "robustness", "sharpe-surface"]
brokers_frameworks: ["Parameter Sensitivity Analyzer", "Python"]
version: "1.0"
author: algo-trading-skills-contributors
license: Apache-2.0
---

## When to Use

Invoke this skill after optimizing strategy parameters. A strategy whose Sharpe jumps from 0.5 to 3.0 with a $\pm 1\%$ parameter tweak is overfit. This skill systematically perturbs parameters across a grid and measures the gradient of Sharpe ratio to distinguish fragile peaks from robust plateaus.

## Prerequisites

- Strategy with tunable parameters (e.g., lookback window, entry threshold).
- Backtest engine that accepts parameter overrides and returns performance metrics.

## Workflow

1. **Define Parameter Grid**: Specify parameter ranges and step sizes.
2. **Run Grid Sweep**: Execute backtest for each parameter combination.
3. **Compute Sensitivity Gradient**: Measure $\Delta \text{Sharpe} / \Delta \text{Param}$ across neighbors.
4. **Classify Robustness**: Flag parameters with high gradient as fragile.

> Full procedure: see `references/workflows.md`.

## Common Pitfalls

- **Single-Parameter Analysis Only**: Ignoring interaction effects between correlated parameters.
- **Too Fine Grid**: Overfitting the grid search itself.

## Verification

- Run `python scripts/test_sensitivity_analyzer.py` — 100% pass rate.

## Related Skills

- `walk-forward-validation-setup`
- `multi-year-regime-coverage-requirement`
---
