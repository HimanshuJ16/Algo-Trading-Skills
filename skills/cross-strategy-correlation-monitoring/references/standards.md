# Standards for Cross-Strategy Correlation Monitoring

## Status of the thresholds

There is no regulator, exchange or industry body that mandates a pod-level PnL
correlation limit, a maximum average portfolio correlation or a minimum
Diversification Ratio. Every number below is a **configurable internal policy
default** shipped with the engine, not an external standard — calibrate against
the empirical distribution of your own pods' correlations before automating
capital decisions.

| Parameter | Default | Meaning |
|---|---|---|
| `high_correlation_threshold` | $\rho \ge 0.70$ | Pair flagged `HIGH_CORRELATION` (inclusive, one-sided, compared before rounding). |
| `redundancy_threshold` | $\rho \ge 0.85$ | Pair flagged `REDUNDANT_POD`; triggers capital re-allocation review. Must be $\ge$ the high threshold. |
| `max_avg_correlation_threshold` | $\bar{\rho} \ge 0.55$ | Portfolio flagged as diversification-compromised on the signed mean of the unique off-diagonal entries. |
| `min_diversification_ratio` | $DR \ge 1.20$ | Portfolio-level floor; below it the report is unhealthy. Must be $\ge 1.0$. |
| `min_observations` | 30 rows | Minimum window length. Hard floor 3 — below that every off-diagonal $\rho$ is algebraically $\pm 1$. |
| `lookback_window` | `None` | Trailing rows used. `None` leaves windowing to the caller. |
| `ewma_span` | `None` | `None` = equal weighting over the window. An integer $\ge 2$ switches to EWMA weighting. |
| `shrinkage_delta` | $\delta = 0.0$ | Fixed intensity of the linear shrinkage applied to the emitted **covariance**. Never applied to the reported correlations. |

`is_diversification_healthy` is false if *any* of the three conditions fires: a
pair breach, $\bar{\rho} \ge$ its threshold, or $DR <$ its floor. None can mask
another.

## Diversification Ratio — definition and properties

$$DR(P) = \frac{\sum_{i=1}^{M} w_i \sigma_i}{\sqrt{w^T \Sigma w}}$$

"the ratio of the weighted average of volatilities divided by the portfolio
volatility" — Y. Choueifaty and Y. Coignard, *Toward Maximum Diversification*,
The Journal of Portfolio Management 35(1), Fall 2008, pp. 40-51.
<https://jpm.pm-research.com/content/35/1/40.short>

Properties the engine relies on:

- $DR \ge 1$ for any non-negative weight vector (triangle inequality:
  $\sigma_P = \lVert \sum_i w_i X_i \rVert \le \sum_i w_i \lVert X_i \rVert$).
  The bound fails for negative weights, which is why the engine rejects them —
  the paper's own framing is the long-only constraint, "all weights must be positive."
- $DR = 1$ exactly when the pods are perfectly correlated (or $M = 1$): no
  diversification benefit.
- $M$ orthogonal, equal-volatility pods at equal weight give $DR = \sqrt{M}$ —
  the reference value used in this skill's tests.
- Portfolio variance of exactly zero (perfectly offsetting pods) makes the ratio
  divergent; the engine reports $DR = \infty$ and logs a warning rather than
  returning a finite placeholder.

$\sigma$ and $\Sigma$ are taken from the *same* weighted estimate as the reported
correlations, so a DR computed under `ewma_span` describes the same window as the
matrix beside it.

## Weighting: rolling window vs EWMA

With `ewma_span=None` the estimate is the ordinary sample covariance ($ddof=1$)
over the trailing `lookback_window` rows: every observation in the window counts
equally and the effective sample size is the row count.

With an integer `ewma_span`, weights follow the pandas
`ewm(span=..., adjust=True)` convention: $\alpha = 2/(\text{span}+1)$ with
$w_i \propto (1-\alpha)^i$ over the $i$ periods back from the latest observation,
normalized to sum to 1 (span $\ge 1$ is the pandas constraint; this engine
additionally requires span $\ge 2$, because $\alpha = 1$ puts all weight on a
single observation and the debiased covariance becomes $0/0$ — pandas returns an
all-NaN matrix there).
<https://pandas.pydata.org/docs/reference/api/pandas.DataFrame.ewm.html>

The covariance at the latest observation is the debiased weighted second moment

$$S = \frac{1}{1 - \sum_t w_t^2} \sum_t w_t\,(x_t - \mu_w)(x_t - \mu_w)^\top,
\qquad \mu_w = \sum_t w_t x_t.$$

This is the `bias=False` form pandas' `ewm(...).cov()` produces (verified against
pandas to $10^{-10}$ relative on a 200×4 panel in this skill's test suite).
Computing it directly as one weighted sum of outer products makes it positive
semi-definite by construction and avoids evaluating the covariance at every
intermediate timestamp.

**Choose the span against how quickly you are willing to act.** A short span
recovers a live convergence within a handful of observations but manufactures
threshold crossings; a long span is stable but will report a benign $\rho$
through the first days of a convergence.

### Effective sample size

With normalized weights, the Kish effective sample size is
$N_{\text{eff}} = 1/\sum_t w_t^2$. Under equal weighting that is the row count.
Under EWMA weights it converges to `ewma_span` itself as history grows:

| `ewma_span` | rows = 100 | rows = 1,000 | rows = 10,000 |
|---|---|---|---|
| 10 | 10.00 | 10.00 | 10.00 |
| 60 | 55.87 | 60.00 | 60.00 |
| 250 | 94.99 | 249.83 | 250.00 |

Report `effective_observations`, not the row count, when documenting how much
data an alert rests on.

## Estimation quality

Pearson correlation estimates carry sampling error. Under the Fisher
transformation $z = \operatorname{artanh}(\hat\rho)$, $z$ is approximately normal
with standard error $1/\sqrt{N-3}$ (R. A. Fisher, 1921;
<https://en.wikipedia.org/wiki/Fisher_transformation>). At $N = 30$ that is
$\approx 0.19$ on the $z$ scale, and at $N_{\text{eff}} = 60$ still $\approx 0.13$
— wide enough that a single window crossing 0.70 is weak evidence on its own.
With $M$ pods, $M(M-1)/2$ pairs are tested simultaneously, so require persistence
across windows before acting.

## Covariance shrinkage — what this engine does and does not implement

The engine applies **fixed-intensity linear shrinkage toward a diagonal target**:

$$\hat{\Sigma} = \delta \operatorname{diag}(S) + (1 - \delta)\,S$$

Properties it relies on:

- $\operatorname{diag}(\hat{\Sigma}) = \operatorname{diag}(S)$ — variances are untouched.
- Off the diagonal, $\hat{\Sigma}_{ij} = (1-\delta) S_{ij}$, so the **implied
  correlations are exactly $(1-\delta)\rho_{ij}$**. This is why alert thresholds
  are applied to the unshrunk correlation matrix, never to $\hat{\Sigma}$.
- $S$ is PSD and $\operatorname{diag}(S)$ is PD for positive variances, so
  $\hat{\Sigma}$ is positive definite for any $\delta > 0$ — invertible even when
  the pod count exceeds the observation count and $S$ is singular. This is the
  actual benefit of the shrinkage step, and the reason it is emitted for
  downstream mean-variance optimization.

### Attribution

The diagonal, unequal-variance target is **"Target D"** of J. Schäfer and
K. Strimmer, *A Shrinkage Approach to Large-Scale Covariance Matrix Estimation and
Implications for Functional Genomics*, Statistical Applications in Genetics and
Molecular Biology **4**(1), 2005, Article 32.

The general form $\hat{\Sigma} = \delta F + (1-\delta) S$ is the shrinkage family
of Olivier Ledoit and Michael Wolf. Their published targets and papers are:

| Paper | Target $F$ |
|---|---|
| Ledoit & Wolf, *Improved estimation of the covariance matrix of stock returns with an application to portfolio selection*, Journal of Empirical Finance **10**, 2003, pp. 603-621 | Sharpe single-index matrix |
| Ledoit & Wolf, *Honey, I Shrunk the Sample Covariance Matrix*, Journal of Portfolio Management **30**(4), 2004, pp. 110-119 (<https://www.econ.uzh.ch/dam/jcr:ffffffff-961c-1dd9-ffff-ffffb4762fbf/honey.pdf>) | Constant-correlation model |
| Ledoit & Wolf, *A well-conditioned estimator for large-dimensional covariance matrices*, Journal of Multivariate Analysis **88**(2), 2004, pp. 365-411 (<https://doi.org/10.1016/S0047-259X(03)00096-4>) | Scaled identity $\frac{\operatorname{tr}(S)}{M}I$ |

**This engine is not the Ledoit-Wolf estimator.** In their own framing, "the
challenge is to know the optimal shrinkage intensity, and we give the formula for
that" — the intensity $\hat{\delta}$ is *estimated from the data* (JPM 2004, §3.3
and Appendix B). A hard-coded $\delta$ carries none of the resulting optimality
guarantees, and none of the three targets above is the diagonal target used here.
If you need the actual estimator, use `sklearn.covariance.LedoitWolf`, which
implements the JMVA 2004 scaled-identity variant with the data-estimated intensity
— note that its derivation assumes i.i.d. sampling and is not defined for
EWMA-weighted observations.

**Never shrink toward the raw identity $I$.** Daily pod return variances are on
the order of $10^{-4}$; a unit-variance target dominates the diagonal and collapses
the correlations. On a representative 4-pod panel, $\delta I + (1-\delta)S$ at
$\delta = 0.15$ reduced a true $\rho = 0.883$ to $0.0004$. An identity-shaped target
must be scaled by $\operatorname{tr}(S)/M$, as in Ledoit & Wolf (JMVA 2004).

## Rejected inputs

The engine raises rather than imputing, because a risk monitor that substitutes a
value for missing or degenerate data reports a portfolio that does not exist:

| Condition | Why it is rejected |
|---|---|
| Fewer than 2 pod columns | No off-diagonal pair exists. |
| Fewer than `min_observations` rows | Sampling error dominates; below 3 rows every $\rho$ is $\pm 1$. |
| NaN or infinite values | Pairwise deletion silently estimates different pairs over different samples and can produce a non-PSD matrix. |
| Zero-variance (flat / stale / idle) column | Correlation is undefined; reporting 0.0 invents a perfect diversifier, inflates $DR$ and deflates $\bar{\rho}$. |
| Duplicate `strategy_names` | Pair breaches would not be attributable to a specific pod. |
| Negative, non-finite or mis-sized weights | $DR \ge 1$ does not hold off the long-only simplex. |
