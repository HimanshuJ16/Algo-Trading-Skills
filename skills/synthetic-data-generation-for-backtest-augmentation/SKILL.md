---
name: synthetic-data-generation-for-backtest-augmentation
description: >-
  Use when historical dataset length is limited to generate synthetic price paths via Geometric Brownian Motion (GBM) and block bootstrapping, enabling stress-testing and robustness evaluation.
domain: algorithmic-trading
subdomain: backtesting-methodology
tags: ["backtesting-methodology", "synthetic-data", "gbm", "bootstrap", "data-augmentation", "robustness-testing"]
brokers_frameworks: ["Synthetic Data Generator", "Python NumPy"]
version: "1.0"
author: algo-trading-skills-contributors
license: Apache-2.0
---

## When to Use

Invoke this skill when backtesting strategies on asset classes or regimes with limited historical data history (e.g. newly listed IPOs, crypto pairs, rare economic events). Relying solely on a short 6-month historical path leads to overfitting. Generating synthetic price paths preserving empirical drift, volatility, and autocorrelation allows testing strategy robustness across thousands of plausible alternative market paths.

## Prerequisites

- Historical return series or drift ($\mu$) and volatility ($\sigma$) parameters.
- Block size parameter for block bootstrapping autocorrelation preservation.

## Workflow

1. **Estimate Empirical Parameters**: Calculate daily drift $\mu$, volatility $\sigma$, and autocorrelation.
2. **Generate Geometric Brownian Motion (GBM) Paths**:
   $$S_t = S_0 \exp\left( (\mu - 0.5 \sigma^2) t + \sigma \sqrt{t} Z_t \right)$$
3. **Generate Block Bootstrapped Return Paths**: Sample contiguous blocks of historical returns to preserve volatility clustering.
4. **Evaluate Strategy Across Synthetic Ensemble**: Compute distribution of Sharpe ratios across synthetic paths.

> Full procedure: see `references/workflows.md`.
> Standards reference: see `references/standards.md`.
> Printable pre-flight checklist: see `assets/checklist.md`.

## Common Pitfalls

- **Pure Random Walk Without Volatility Clustering**: Using standard IID Gaussian noise, ignoring real-world fat tails and volatility clustering.
- **Ignoring Regime Shifts**: Generating synthetic paths assuming constant drift during changing macroeconomic regimes.

## Verification

- Generate 100 synthetic GBM price paths, verify statistical mean drift and volatility match input parameters.
- Run `python scripts/test_synthetic_data_generator.py` and confirm 100% pass rate.

## Related Skills

- `monte-carlo-strategy-robustness-testing`
- `multi-year-regime-coverage-requirement`
---
