# Standards for Cross-Strategy Correlation Monitoring

## Status of the thresholds

There is no regulator, exchange or industry body that mandates a pod-level PnL
correlation limit or a minimum Diversification Ratio. Every number below is a
**configurable internal policy default** shipped with the engine, not an external
standard — calibrate against the empirical distribution of your own pods' rolling
correlations before automating capital decisions.

| Parameter | Default | Meaning |
|---|---|---|
| `high_correlation_threshold` | $\rho \ge 0.70$ | Pair flagged `HIGH_CORRELATION` (inclusive, one-sided). |
| `redundancy_threshold` | $\rho \ge 0.85$ | Pair flagged `REDUNDANT_POD`; triggers capital re-allocation review. Must be $\ge$ the high threshold. |
| `min_diversification_ratio` | $DR \ge 1.20$ | Portfolio-level floor; below it the report is unhealthy. Must be $\ge 1.0$. |
| `min_observations` | 30 rows | Minimum window length. Hard floor 3 — below that every off-diagonal $\rho$ is algebraically $\pm 1$. |

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

## Estimation quality

Pearson correlation estimates carry sampling error. Under the Fisher
transformation $z = \operatorname{artanh}(\hat\rho)$, $z$ is approximately normal
with standard error $1/\sqrt{N-3}$ (R. A. Fisher, 1921;
<https://en.wikipedia.org/wiki/Fisher_transformation>). At $N = 30$ that is
$\approx 0.19$ on the $z$ scale — wide enough that a single window crossing 0.70
is weak evidence on its own. With $M$ pods, $M(M-1)/2$ pairs are tested
simultaneously, so require persistence across windows before acting.
