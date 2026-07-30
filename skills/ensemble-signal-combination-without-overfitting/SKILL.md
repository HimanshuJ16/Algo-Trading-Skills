---
name: ensemble-signal-combination-without-overfitting
description: Use when ensembling multiple trading signals or ML models to apply signal
  normalization, non-negative weight constraints, 1/N shrinkage, and inverse variance
  weighting without overfitting to historical noise
domain: algorithmic-trading
subdomain: financial-ml
tags:
- financial-ml
- ensemble-learning
- signal-combination
- l2-regularization
- 1-over-n-shrinkage
brokers_frameworks:
- scikit-learn
- SciPy Optimize
- NumPy
- Custom Signal Ensembles
version: '1.0'
author: algo-trading-skills-contributors
license: Apache-2.0
---

## When to Use

Invoke this whenever combining alpha signals from multiple sub-models (such as momentum, mean-reversion, order-flow imbalance, and sentiment indicators). Unconstrained optimization (such as standard OLS regression) creates extreme positive and negative weights that overfit to historical noise and produce catastrophic out-of-sample losses. Implementing signal $Z$-score normalization, Non-Negative Least Squares (NNLS) weight constraints ($w_i \ge 0$), and shrinkage toward equal weighting ($1/N$) is mandatory to guarantee out-of-sample signal stability.

## Prerequisites

- Array of normalized sub-model signal streams ($S_1, S_2, \dots, S_N$).
- Target return series for model alignment.
- Defined combination method (`EQUAL_WEIGHT`, `INVERSE_VARIANCE`, `SHRUNK_NNLS`).

## Workflow

1. **Normalize Sub-Model Signals**:
   - Standardize each sub-model signal $S_i$ via rolling $Z$-score or percentile ranking:
     $$Z_{i,t} = \frac{S_{i,t} - \mu_i}{\sigma_i}$$
   - Clip normalized signals to $[-3.0, +3.0]$.

2. **Compute Regularized Weight Vector ($w$)**:
   - **Equal Weighting (1/N)**: Set $w_i = 1/N$.
   - **Inverse Variance Weighting**: $w_i = \frac{1/\sigma_i^2}{\sum_j 1/\sigma_j^2}$.
   - **Shrunk Non-Negative Weighting**: Solve for non-negative weights $w_{\text{opt}} \ge 0, \sum w_i = 1$. Shrink toward equal weight with parameter $\lambda = 0.5$:
     $$w_{\text{final}} = (1 - \lambda) w_{\text{opt}} + \lambda w_{\text{equal}}$$

3. **Compute Aggregate Ensemble Signal**:
   - Calculate composite signal:
     $$S_{\text{ensemble}, t} = \sum_{i=1}^N w_i \cdot Z_{i,t}$$

4. **Verify Out-of-Sample Ensemble Stability**:
   - Ensure weight vector $w_{\text{final}}$ has zero negative weights ($w_i \ge 0$) and maximum single-model weight caps ($\le 0.40$).

> Full step-by-step procedure with broker-specific detail: see `references/workflows.md`.
> Broker/framework coverage table for this skill: see `references/standards.md`.
> Printable pre-flight checklist: see `assets/checklist.md`.

## Common Pitfalls

- **Unconstrained OLS Regression**: Fitting linear regression without bounds, resulting in large negative weights (shorting sub-signals that performed poorly historically).
- **Un-Normalized Signals**: Combining signals with different scales (e.g. RSI 0-100 vs MACD -0.05 to +0.05) without $Z$-score standardization.
- **Ignoring Signal Multicollinearity**: Failing to handle highly correlated sub-models, causing unstable weight flipping.

## Verification

- Submit 3 sub-model signal series and verify `EnsembleSignalCombiner` computes normalized ensemble signals bounded in $[-3.0, +3.0]$.
- Verify non-negative constraint $w_i \ge 0$ is enforced across all sub-models.
- Verify shrinkage parameter $\lambda = 0.5$ blends weights toward $1/N$ equal allocation.
- Run unit test suite `python scripts/test_ensemble_combiner.py` and confirm 100% pass rate.

## Related Skills

- `regime-detection-for-strategy-switching`
- `walk-forward-optimization-window-management`
- `correlation-aware-exposure-limits`
---
