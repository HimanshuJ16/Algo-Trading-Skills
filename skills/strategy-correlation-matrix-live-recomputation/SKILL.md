---
name: strategy-correlation-matrix-live-recomputation
description: >-
  Live inter-strategy correlation matrix engine for multi-strategy portfolios: EWMA-weighted covariance estimation, high-correlation pair alerting, average-correlation diversification breakdown detection, and a fixed-intensity shrunken covariance matrix for downstream optimizers.
domain: Portfolio & Risk Management
subdomain: Live Correlation Matrix & Diversification
tags: ["strategy-correlation", "live-correlation", "ewma-decay", "covariance-shrinkage", "well-conditioned-covariance", "diversification-breakdown"]
brokers_frameworks: ["EWMA Covariance Estimation", "Linear Covariance Shrinkage", "Python Dataclasses", "pandas", "numpy"]
version: "2.0.0"
author: algo-trading-skills-contributors
license: Apache-2.0
---

## When to Use

Use this skill when monitoring live inter-strategy correlations across a multi-strategy quantitative portfolio (e.g. trend following, statistical arbitrage, market making, crypto momentum). During market stress, strategies that were independent in normal conditions converge, and the diversification the book was sized on disappears before the drawdown shows up in PnL. This engine recomputes an EWMA-weighted correlation matrix on demand, flags pairs at or above a configured $\rho$, flags the portfolio when the average inter-strategy correlation breaches its own threshold, and returns a shrunken, invertible covariance matrix for a downstream optimizer.

## When NOT to Use

- **As a capital control.** The report is an input to an allocation or de-risking decision, not a limit engine. Pair it with `correlation-aware-exposure-limits` and `multi-strategy-capital-allocation-limits` for enforcement, and `strategy-level-kill-switch-vs-portfolio-level-kill-switch` for automated cuts.
- **As a tail-risk model.** Pearson correlation under EWMA weights is a linear, full-distribution measure. Strategies that decouple in normal markets and converge only in the left tail will show a benign $\rho$ right up to the loss. Use `tail-correlation-between-strategies-under-stress` for lower-tail dependence.
- **For a long, stable historical view.** The EWMA weighting deliberately discards the past: at `ewma_span=60` the estimate has an effective sample size of ~60 observations no matter how much history you pass. Use `cross-strategy-correlation-monitoring` for an explicit rolling window plus a Diversification Ratio.
- **On unsynchronized, gross, or partially-missing return series.** Misaligned timestamps and un-stripped market beta produce a correlation that describes the data pipeline, not the strategies. The engine rejects non-finite values rather than imputing them.
- **Without threshold calibration.** 0.70 and 0.55 are internal policy defaults, not standards. Calibrate against the empirical distribution of your own strategies' EWMA $\rho$ before automating capital decisions.

## Prerequisites

- Synchronized strategy return series (`returns_df`: pandas DataFrame, one numeric column per strategy, one row per timestamp, oldest first), $N \ge 2$ strategies sharing one timestamp index.
- Unique column names, no non-finite values, and no zero-variance (flat / stale / idle) column — the engine rejects all three rather than imputing them.
- At least `min_observations` rows (default 30). The hard floor is 3: with 2 observations every off-diagonal correlation is algebraically $\pm 1$ regardless of the data.
- `ewma_span` $\ge 2$ (pandas convention $\alpha = 2/(\text{span}+1)$) and a shrinkage intensity $\delta \in [0, 1]$ (default 0.15).

## Workflow

1. **Validation Gate**: Reject the panel before estimating anything if it has fewer than 2 strategies, fewer than `min_observations` rows, duplicate column names, non-numeric columns, non-finite values, or a zero-variance column. Decision point: a strategy that traded nothing this window is a *scope* decision (exclude it), never an imputation — filling it with zeros reports a dead strategy as the portfolio's best diversifier and drags the portfolio average below its breakdown threshold, masking a real breach on the live pairs.
2. **EWMA Covariance Estimation**: Weight observations $w_t \propto (1-\alpha)^{T-t}$, $\alpha = 2/(\text{span}+1)$, normalized to sum to 1, and compute the debiased weighted covariance $S = \frac{1}{1 - \sum_t w_t^2}\sum_t w_t (x_t - \mu_w)(x_t - \mu_w)^\top$ at the latest observation. Check `effective_observations` ($1/\sum_t w_t^2$, which converges to `ewma_span`) rather than the row count: 10,000 rows at span 60 is a 60-observation estimator.
3. **Correlation Matrix Conversion**: $R = D^{-1/2} S D^{-1/2}$ with $D = \operatorname{diag}(S)$. **This unshrunk matrix is what the thresholds are applied to.**
4. **Shrinkage for Downstream Optimizers**: $\hat{\Sigma} = \delta \operatorname{diag}(S) + (1-\delta) S$ — a fixed-intensity linear shrinkage toward the diagonal (zero-correlation) target. It is positive definite for $\delta > 0$, so it stays invertible even when the number of strategies exceeds the number of observations and $S$ is singular. Decision point: **never threshold this matrix.** A diagonal target leaves the variances untouched and scales every off-diagonal correlation by exactly $(1-\delta)$, so a 0.70 alert applied to it would only fire at a true $\rho \ge 0.70/(1-\delta) = 0.824$ at $\delta = 0.15$ — a silent, systematic under-report of the breakdown this engine exists to catch.
5. **Pairwise Alerting** (inclusive, one-sided, compared before rounding): $\rho_{i,j} \ge 0.70 \implies$ high-correlation alert. Negative $\rho$ is never an alert — it is diversification.
6. **Portfolio Breakdown Alerting**: compute $\bar{\rho}$ as the mean of the $N(N-1)/2$ unique off-diagonal entries. `is_portfolio_diversification_compromised` is the **OR** of $\bar{\rho} \ge$ `max_avg_correlation_threshold` and *any* pair alert, so a single converged pair inside an otherwise diversified book still flags the portfolio.
7. **Execution Output**: emit a structured `LiveCorrelationMatrixReport` carrying both matrices, `observations_used`, `effective_observations`, and the audit note.

> Full procedure: see `references/workflows.md`.
> Standards reference: see `references/standards.md`.
> Printable pre-flight checklist: see `assets/checklist.md`.

## Common Pitfalls

- **Thresholding a Shrunken Correlation Matrix**: shrinkage toward a diagonal target multiplies every off-diagonal $\rho$ by $(1-\delta)$. Comparing 0.70 against that deflated number silently raises the real trigger to $0.70/(1-\delta)$, so a pair genuinely at $\rho = 0.80$ reports 0.68 and never alerts. Threshold the correlation estimate; shrink the covariance you hand to an optimizer.
- **Calling a Fixed Intensity "Ledoit-Wolf"**: the defining contribution of Ledoit-Wolf (and of Schäfer-Strimmer) is a shrinkage intensity *estimated from the data*, together with a specific target — single-index, constant-correlation, or scaled identity. A hard-coded $\delta = 0.15$ against a diagonal target is neither, and inherits none of their optimality results. See `references/standards.md`.
- **Shrinking Toward the Raw Identity Matrix**: $\delta I + (1-\delta) S$ is dimensionally wrong for return data. Daily strategy variances are on the order of $10^{-4}$, so a unit-variance target dominates the diagonal and collapses every correlation toward zero — on a representative panel it turned a true $\rho = 0.88$ into $0.0004$. If an identity target is wanted, it must be scaled, $\frac{\operatorname{tr}(S)}{N} I$.
- **Imputing a Stale Strategy**: a flat return column has an *undefined* correlation to everything. Substituting 0.0 makes an idle or dead strategy the portfolio's best diversifier and pulls $\bar{\rho}$ below its threshold. The engine raises instead; never bypass it by pre-filling zeros.
- **Reading the Row Count as the Sample Size**: EWMA weighting caps the effective sample size at roughly `ewma_span` regardless of history depth. A correlation estimated from an effective 20 observations carries ~$1/\sqrt{N-3}$ standard error on the Fisher-$z$ scale (Fisher, 1921) — wide enough that a true $\rho = 0.4$ crosses 0.70 by chance regularly.
- **Multiple Testing Across Pairs**: $N$ strategies produce $N(N-1)/2$ simultaneous comparisons; at $N=10$ that is 45 pairs, so isolated crossings are expected even under independence. Require persistence across consecutive recomputations before cutting capital.
- **Reading $\bar{\rho}$ Alone**: the average is signed, so a $+0.9$ pair and a $-0.9$ pair average to zero. Always read `high_correlation_pairs` alongside it — that is why the compromised flag ORs the two conditions.
- **Ignoring Live Correlation Convergence**: assuming historical low correlation holds during market panics, allowing hidden concentration risk to accumulate.

## Verification

- Two perfectly scaled series ($B = 2.5A$): correlation is exactly $+1$ and one alert fires. Perfectly negated series ($B = -3A$): exactly $-1$, **no** alert, portfolio not compromised.
- Build a pair with $\rho = 0.75$ exactly (Gram-Schmidt under the EWMA weights) and run it at $\delta = 0.30$: the reported correlation must be 0.75, not $0.70 \times 0.75 = 0.525$, and the 0.70 alert must fire. This is the regression test for the alert-deflation defect.
- Cross-check the full matrix against `returns_df.ewm(span=S).cov()` — the two must agree to $10^{-10}$ relative.
- Confirm $\operatorname{diag}(\hat{\Sigma}) = \operatorname{diag}(S)$ and that $\hat{\Sigma}$'s implied off-diagonal correlation is $(1-\delta)\rho$; with 6 strategies over 4 observations, confirm $S$ is rank-deficient while $\hat{\Sigma}$ has strictly positive eigenvalues.
- Feed a flat column, a NaN, an infinity, a duplicate column name, a non-numeric column, or a sub-`min_observations` panel: the engine must raise, **not** report a healthy portfolio.
- With 5,000 rows at `ewma_span=60`, confirm `effective_observations` $\approx 60$.
- Run `python -m unittest discover -s skills/strategy-correlation-matrix-live-recomputation/scripts`.

## Related Skills

- `cross-strategy-correlation-monitoring`
- `tail-correlation-between-strategies-under-stress`
- `correlation-aware-exposure-limits`
- `multi-strategy-capital-allocation-limits`
- `cross-asset-correlation-regime-shifts`
