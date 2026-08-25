---
name: ensemble-signal-combination-without-overfitting
description: Use when ensembling multiple trading signals or ML models to apply causal
  signal normalization, non-negative weight constraints, 1/N shrinkage, inverse
  forecast-error-variance weighting, and per-model weight caps without overfitting to
  historical noise
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
version: "2.0.0"
author: algo-trading-skills-contributors
license: Apache-2.0
---

## When to Use

Invoke this whenever combining alpha signals from multiple sub-models (such as momentum, mean-reversion, order-flow imbalance, and sentiment indicators). Unconstrained optimization (such as standard OLS regression) creates extreme positive and negative weights that overfit to historical noise and produce catastrophic out-of-sample losses. Signal $Z$-score normalization, Non-Negative Least Squares (NNLS) weight constraints ($w_i \ge 0$), and shrinkage toward equal weighting ($1/N$) are what keep the combined signal stable out of sample.

## When NOT to Use

- **Fewer than three sub-models.** With $N \le 2$ a $0.40$ weight cap is infeasible (no vector summing to $1$ can keep every weight below $1/N$), so the cap collapses the result to equal weighting. Use `EQUAL_WEIGHT` explicitly instead of pretending to optimize.
- **Short history.** Fitting $N$ weights needs more than $N$ observations, and in practice far more. Run `EQUAL_WEIGHT` until enough history accumulates rather than fitting on a handful of bars.
- **Regime switching rather than blending.** If the intent is to *select* one model per regime rather than average them, use `regime-detection-for-strategy-switching`.
- **Time-varying weights with memory.** If weights should decay with model staleness, use `multi-model-ensemble-weight-decay`.

## Prerequisites

- Array of time-aligned sub-model signal streams ($S_1, S_2, \dots, S_N$), equal length, no NaN/Inf.
- Realized forward return series ($y$) aligned to the signals. **Required** by `INVERSE_VARIANCE` and `SHRUNK_NNLS`; `EQUAL_WEIGHT` needs no target.
- A walk-forward split: weights must be fitted on a training window and applied to a later, unseen window.

## Workflow

1. **Normalize Sub-Model Signals (causally)**:
   - Standardize each sub-model signal $S_i$ with a **causal** rolling or expanding $Z$-score, where the statistic at time $t$ uses only observations $t' \le t$:
     $$Z_{i,t} = \frac{S_{i,t} - \mu_{i,\le t}}{\sigma_{i,\le t}}$$
   - Emit $0.0$ during the warm-up window (fewer than `min_periods` observations) rather than a $Z$-score from an undefined standard deviation.
   - Clip normalized signals to $[-3.0, +3.0]$.
   - A full-sample $(x - \bar{x}) / s$ transform is **not** acceptable: it leaks every future observation into every historical bar.

2. **Compute Regularized Weight Vector ($w$)**:
   - **Equal Weighting (1/N)**: Set $w_i = 1/N$. No fitting, no target, no overfitting risk.
   - **Inverse Forecast-Error Variance** (Bates & Granger, 1969): scale each standardized signal onto the target's units by its least-squares slope $\beta_i$, then weight by inverse MSE:
     $$w_i = \frac{1/\hat{\sigma}_i^2}{\sum_j 1/\hat{\sigma}_j^2}, \qquad \hat{\sigma}_i^2 = \frac{1}{T}\sum_t (\beta_i Z_{i,t} - y_t)^2$$
     This is a *marginal* criterion — it judges each model alone and ignores redundancy between models. Do not weight by the variance of the signal itself: after standardization every signal has unit variance, so that degenerates to $1/N$.
   - **Shrunk Non-Negative Weighting**: solve the *joint* fit $\min_w \lVert Zw - y \rVert^2$ subject to $w_i \ge 0$, then normalize to $\sum w_i = 1$ and shrink toward equal weight with $\lambda = 0.5$:
     $$w_{\text{final}} = (1 - \lambda) w_{\text{opt}} + \lambda w_{\text{equal}}$$
     If NNLS zeroes every model (no sub-model has non-negative explanatory power on the training window), that is a real signal about the model set — fall back to $1/N$ and investigate, do not force a fit.

3. **Cap and Renormalize**:
   - Enforce a maximum single-model weight (default $\le 0.40$) by water-filling: pin the breaching models at the cap and rescale the rest pro rata, repeating until no model breaches. Raise the cap to $1/N$ when $1/N$ exceeds it, since a tighter cap is infeasible on the simplex.

4. **Compute Aggregate Ensemble Signal**:
   - Calculate the composite signal:
     $$S_{\text{ensemble}, t} = \sum_{i=1}^N w_i \cdot Z_{i,t}$$
   - Because $w_i \ge 0$, $\sum w_i = 1$, and each $Z_{i,t} \in [-3, +3]$, the composite is bounded by the same interval.

5. **Verify Out-of-Sample Ensemble Stability**:
   - Confirm $w_i \ge 0$, $\sum w_i = 1$, and $\max_i w_i \le$ the effective cap.
   - Refit the weights on a rolling training window and compare weight vectors across refits: weights that flip materially between adjacent windows indicate the fit is tracking noise, not alpha.

> Full step-by-step procedure with implementation detail: see `references/workflows.md`.
> Method comparison table and source citations for this skill: see `references/standards.md`.
> Printable pre-flight checklist: see `assets/checklist.md`.

## Common Pitfalls

- **Full-sample normalization**: computing $Z$-scores from the mean and standard deviation of the *entire* series, including bars after $t$. Every historical signal then encodes the future, and the backtest is invalid. Verify by appending new observations to the series — earlier normalized values must not change.
- **Fitting and evaluating weights on the same window**: NNLS on the evaluation window is still overfitting, non-negativity notwithstanding. Fit on the training window, apply forward. See `walk-forward-optimization-window-management`.
- **Unconstrained OLS Regression**: fitting linear regression without bounds, resulting in large negative weights (shorting sub-signals that performed poorly historically).
- **Weighting by signal variance instead of forecast-error variance**: standardized signals all have variance $1$, so "inverse variance" on them silently returns $1/N$ while appearing to optimize. Inverse-variance weighting is defined against forecast error, which requires the realized target.
- **Un-Normalized Signals**: combining signals with different scales (e.g. RSI 0-100 vs MACD -0.05 to +0.05) without $Z$-score standardization.
- **Ignoring Signal Multicollinearity**: near-duplicate sub-models make the Gram matrix near-singular; the split of weight between them is then arbitrary and flips between refits. Damp the fit (the reference implementation adds relative Tikhonov ridge) and prefer a genuinely diverse model set.
- **A weight cap that is silently infeasible**: a $0.40$ cap cannot bind for $N \le 2$. Check the *effective* cap, $\max(\text{cap}, 1/N)$, not the configured one.
- **Non-finite inputs**: a single NaN in one sub-model's history poisons the mean, the standard deviation, and every weight. Reject non-finite values at the boundary rather than letting them propagate into position sizes.

## Verification

- Submit 3+ sub-model signal series plus an aligned target and verify `EnsembleSignalCombiner` computes normalized ensemble signals bounded in $[-3.0, +3.0]$.
- Verify normalization is causal: `normalize_zscore(history)` must equal the leading slice of `normalize_zscore(history + future)`.
- Verify $w_i \ge 0$ for all sub-models, $\sum w_i = 1$, and $\max_i w_i \le \max(\text{cap}, 1/N)$.
- Verify the methods actually differ: `EQUAL_WEIGHT`, `INVERSE_VARIANCE`, and `SHRUNK_NNLS` must not all return $1/N$ on a dataset with one clearly superior sub-model.
- Verify shrinkage parameter $\lambda = 0.5$ blends weights toward $1/N$ equal allocation, and that $\lambda = 1$ reproduces equal weighting exactly.
- Run unit test suite `python -m unittest discover -s skills/ensemble-signal-combination-without-overfitting/scripts` and confirm 100% pass rate.

## Related Skills

- `regime-detection-for-strategy-switching`
- `walk-forward-optimization-window-management`
- `correlation-aware-exposure-limits`
- `multi-model-ensemble-weight-decay`
- `feature-engineering-without-leakage`
---
