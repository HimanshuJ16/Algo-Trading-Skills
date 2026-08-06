---
name: synthetic-data-generation-for-backtest-augmentation
description: >-
  Production-grade Synthetic Data Generation Engine for backtest augmentation implementing Geometric Brownian Motion (GBM), GARCH(1,1) stochastic volatility clustering, circular block bootstrapping, and statistical moment validation.
domain: Backtesting & Quantitative Research
subdomain: Synthetic Data & Scenario Augmentation
tags: ["synthetic-data", "backtest-augmentation", "gbm", "garch", "bootstrap", "monte-carlo"]
brokers_frameworks: ["Quantitative Research Pipeline", "NumPy", "Python Dataclasses"]
version: "1.0.0"
author: algo-trading-skills-contributors
license: Apache-2.0
---

## When to Use

Use this skill when historical asset data is limited or when evaluating strategy robustness under unobserved market scenarios (e.g., regime shifts, extreme volatility clustering, tail risk shocks). Backtesting solely on short or single-regime historical datasets causes severe overfitting. This engine generates synthetic price/return paths using Geometric Brownian Motion (GBM), GARCH(1,1) conditional volatility clustering, and Circular Stationary Block Bootstrapping, followed by statistical moment validation (`SyntheticValidationReport`).

## Prerequisites

- Empirical historical return series for bootstrap resampling or parameter estimation.
- Target path length (`steps`) and starting price ($S_0$).

## Workflow

1. **Generation Method Selection**:
   - For diffusion modeling: Use `generate_gbm(GBMConfig(mu, sigma, S0, dt, steps))`.
   - For volatility clustering: Use `generate_garch(GARCHConfig(omega, alpha, beta, mu, S0, steps))`.
   - For empirical non-parametric sampling: Use `block_bootstrap_returns(historical_returns, steps, block_size)`.
2. **Statistical Moment Reconciliation**:
   - Evaluate synthetic return paths via `validate_synthetic_path(historical_returns, synthetic_returns)`.
3. **Execution Output**: Output structured `SyntheticValidationReport`.

> Full procedure: see `references/workflows.md`.
> Standards reference: see `references/standards.md`.
> Printable pre-flight checklist: see `assets/checklist.md`.

## Common Pitfalls

- **i.i.d. Resampling Destroys Autocorrelation**: Using simple random sampling instead of block bootstrapping, destroying volatility clustering and time-series serial correlation.
- **Overestimating Unconditional Volatility**: Setting GARCH parameters $\alpha + \beta \ge 1.0$, creating explosive non-stationary variance.
- **Unvalidated Synthetic Paths**: Using synthetic data without validating statistical moment parity (mean, volatility, skewness, kurtosis) against empirical baselines.

## Verification

- Generate GARCH(1,1) paths ($\alpha=0.1, \beta=0.85$) and verify return paths exhibit volatility clustering. Perform block bootstrap resampling with block size 5 and verify output length. Validate synthetic paths against empirical distributions $\implies$ verify `is_statistically_consistent = True`.
- Run `python scripts/test_synthetic_data_generator.py`.

## Related Skills

- `survivorship-bias-free-universe-construction`
- `portfolio-stress-test-including-liquidity-crunch-scenarios`
---
