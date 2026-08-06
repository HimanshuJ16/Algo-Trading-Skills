---
name: strategy-correlation-matrix-live-recomputation
description: >-
  Production-grade strategy correlation matrix engine featuring live EWMA exponential decay, Ledoit-Wolf shrinkage, high correlation pair detection, and diversification breakdown alerting across multi-strategy portfolios.
domain: Portfolio & Risk Management
subdomain: Live Correlation Matrix & Diversification
tags: ["strategy-correlation", "live-correlation", "ewma-decay", "ledoit-wolf", "shrinkage", "diversification-breakdown"]
brokers_frameworks: ["Ledoit-Wolf Shrinkage", "EWMA Covariance Estimation", "Python Dataclasses", "pandas", "numpy"]
version: "1.0.0"
author: algo-trading-skills-contributors
license: Apache-2.0
---

## When to Use

Use this skill when monitoring live inter-strategy correlations across a multi-strategy quantitative portfolio (e.g., trend following, statistical arbitrage, market making, crypto momentum). During market stress or volatility spikes, previously uncorrelated strategies can suddenly converge to high correlation ($\rho > 0.70$), destroying portfolio diversification. This engine recomputes live shrunken correlation matrices using Exponentially Weighted Moving Average (EWMA) decay and Ledoit-Wolf shrinkage, detecting high correlation pairs and issuing diversification breakdown alerts.

## Prerequisites

- Strategy return streams (`returns_df`: pandas DataFrame with strategy return series as columns).
- EWMA decay span (default 60 periods) and Ledoit-Wolf shrinkage factor ($\delta = 0.15$).

## Workflow

1. **EWMA Covariance Matrix Calculation**:
   - Compute exponentially weighted moving average covariance matrix across strategy return streams.
2. **Ledoit-Wolf Shrinkage Application**:
   - Shrink sample covariance matrix towards target identity matrix: $\hat{\Sigma} = \delta I + (1-\delta) S$.
3. **Correlation Matrix Conversion**:
   - Convert shrunken covariance to correlation matrix: $R = D^{-1/2} \hat{\Sigma} D^{-1/2}$.
4. **High Correlation Alerting**:
   - Detect pairwise strategy correlations $\rho_{i,j} \ge 0.70$ and compute average inter-strategy correlation $\bar{\rho}$.
5. **Execution Output**: Output structured `LiveCorrelationMatrixReport`.

> Full procedure: see `references/workflows.md`.
> Standards reference: see `references/standards.md`.
> Printable pre-flight checklist: see `assets/checklist.md`.

## Common Pitfalls

- **Using Simple Rolling Window Correlation**: Standard simple rolling correlation windows suffer from "ghosting" effects when historical volatility shocks drop off the window.
- **Ill-Conditioned Covariance Matrices**: Failing to apply Ledoit-Wolf shrinkage, leading to non-positive definite correlation matrices that break portfolio optimization algorithms.
- **Ignoring Live Correlation Convergence**: Assuming historical zero correlation holds during market panics, allowing hidden concentration risk to cause catastrophic portfolio drawdowns.

## Verification

- Instantiate `StrategyCorrelationMatrixEngine`. Pass synthetic returns (Strat_A & Strat_B highly correlated, Strat_C uncorrelated) $\implies$ verify $3\times3$ correlation matrix computed, diagonal elements $= 1.0$, Strat_A <-> Strat_B correlation $> 0.70$, high correlation alert triggered, and `is_portfolio_diversification_compromised=True`.
- Run `python scripts/test_strategy_correlation_matrix_live_recomputation.py`.

## Related Skills

- `cross-strategy-correlation-monitoring`
- `tail-correlation-between-strategies-under-stress`
---
