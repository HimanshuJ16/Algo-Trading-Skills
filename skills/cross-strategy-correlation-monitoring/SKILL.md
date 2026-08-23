---
name: cross-strategy-correlation-monitoring
description: Quantitative multi-strategy risk management engine for monitoring rolling
  PnL correlations across strategy pods, detecting diversification breakdown, and
  computing Diversification Ratios.
domain: Multi-Strategy & Portfolio Risk
subdomain: Cross-Strategy Correlation
tags:
- multi-strategy
- cross-strategy
- pnl-correlation
- diversification-ratio
- pod-risk
- correlation-breach
brokers_frameworks:
- NumPy
version: "1.1.0"
author: algo-trading-skills-contributors
license: Apache-2.0
---

## When to Use

Use this skill in multi-strategy hedge fund platforms or multi-pod quantitative trading architectures to monitor rolling PnL correlations across active strategies (e.g., Statistical Arbitrage, Trend Following, Options Volatility, Alt-Data Equity). Sub-strategy pods that appear independent during normal markets often converge during market stress, exhibiting high PnL correlation. This module calculates pairwise PnL correlation matrices over a rolling window, computes the Portfolio Diversification Ratio ($DR = \frac{\sum w_i \sigma_i}{\sigma_{\text{portfolio}}}$, Choueifaty & Coignard 2008), and flags strategy redundancies.

## When NOT to Use

- **As a stand-alone capital control.** The report is an input to an allocation process, not a limit engine. Pair it with `multi-strategy-capital-allocation-limits` and `correlation-aware-exposure-limits` for enforcement.
- **As a tail-risk model.** Pearson correlation is a linear, full-sample measure; pods that decouple in normal markets and converge only in the left tail will show a benign $\rho$ right up to the drawdown. Use `tail-correlation-between-strategies-under-stress` for lower-tail dependence.
- **With short-only or market-neutral *weights*.** The Diversification Ratio is defined for long-only allocations; $DR \ge 1$ does not hold once a capital weight is negative, so the engine rejects negative weights. (Pod *strategies* may of course be short the market — this constrains capital allocation weights, not positions.)
- **Without threshold calibration.** 0.70 / 0.85 / 1.20 are internal policy defaults, not standards. Calibrate against the empirical distribution of your own pods' rolling $\rho$ before automating capital cuts.
- **On unsynchronized or gross PnL series.** Misaligned timestamps and un-stripped market beta both produce correlations that describe the data pipeline, not the strategies.

## Prerequisites

- Synchronized daily or hourly PnL return series for all active sub-strategies ($S_1, S_2, \dots, S_M$), $M \ge 2$, sharing one timestamp index.
- No non-finite values and no zero-variance (flat/stale/idle) pod column — the engine rejects both rather than imputing them.
- At least `min_observations` rows (default 30) in the evaluated window. The hard floor is 3; below that every off-diagonal correlation is algebraically $\pm 1$ regardless of the data.
- Strategy capital allocation weights ($w_i \ge 0$); omit them for equal weighting. Weights are normalized to sum to 1.

## Workflow

1. **PnL Return Ingestion**: Ingest PnL return matrix $R_{N, M}$ ($N$ timestamps, $M$ strategies). Column order must match `strategy_names` — the engine can verify the count, not the identity.
2. **Window and Validation Gate**: Set `lookback_window=W` to evaluate only the trailing $W$ rows (the rolling window); leave it `None` to window upstream. A history shorter than $W$ is *not* an error — it is used in full so a warming-up system still reports, provided it clears `min_observations`; check `observations_used` on the report rather than assuming $W$ rows were available. The window is rejected before estimation if it is shorter than `min_observations`, holds non-finite values, or contains a zero-variance column. Decision point: an idle pod that traded nothing this window is a *scope* decision (exclude it), never an imputation — filling it with zeros reports a dead feed as a perfect diversifier.
3. **Rolling Correlation Matrix Computation**: Compute the pairwise Pearson correlation matrix $C_{\text{pnl}}$ over the window.
4. **Correlation Pairwise Breach Audit** (thresholds inclusive, one-sided):
   - $\rho_{i,j} \ge 0.70 \implies$ `HIGH_CORRELATION`.
   - $\rho_{i,j} \ge 0.85 \implies$ `REDUNDANT_POD` (takes precedence).
   - Negative $\rho$ is never a breach — it is diversification. Offsetting pods are a capital-efficiency question, out of scope here.
5. **Diversification Ratio (DR) Calculation**:
   - $DR = \frac{\sum_{i=1}^M w_i \sigma_i}{\sqrt{w^T \Sigma w}}$, with $\sigma$ and $\Sigma$ both sample estimates ($ddof=1$) over the same window.
   - $DR = 1.0 \implies$ zero diversification benefit; $DR \ge 1$ always holds for $w \ge 0$; $M$ orthogonal equal-volatility pods at equal weight give exactly $DR = \sqrt{M}$.
   - A perfectly offsetting portfolio (portfolio variance exactly zero) returns $DR = \infty$ — the limit of the ratio, and a signal to verify the inputs, not a healthy portfolio.
6. **Capital Re-Allocation Alert**: Every correlation breach *and* any DR shortfall is emitted in `recommendations`; the two conditions are reported independently, so a DR breach is never masked by a concurrent pair breach.

> Full procedure: see `references/workflows.md`.
> Standards reference: see `references/standards.md`.
> Printable pre-flight checklist: see `assets/checklist.md`.

## Common Pitfalls

- **Relying on Static Historical PnL Correlations**: Using 3-year static PnL correlations that hide real-time correlation spikes during market sell-offs. Set `lookback_window` explicitly rather than passing whatever history is on hand.
- **Imputing a Stale Pod**: a flat PnL column has an *undefined* correlation to everything. Substituting 0.0 makes a dead or idle pod the portfolio's best diversifier and inflates $DR$. The engine raises instead; never bypass it by pre-filling zeros.
- **Short-Window Breach Artefacts**: correlation estimates carry sampling error of roughly $1/\sqrt{N-3}$ on the Fisher-$z$ scale (Fisher, 1921) — at $N=12$ a true $\rho = 0.4$ crosses 0.70 by chance regularly. Size `min_observations` to your tolerance instead of trusting a short window.
- **Multiple-Testing Across Pairs**: $M$ pods produce $M(M-1)/2$ simultaneous tests; at $M=10$ that is 45 pairs, so isolated threshold crossings are expected even under independence. Require persistence (N consecutive windows) before cutting capital.
- **Ignoring Equal-Weighted Capital Fallacies**: Assuming equal capital allocations guarantee zero PnL correlation across pods.
- **Neglecting Un-hedged Factor Contamination**: Failing to strip market beta or factor exposures before computing strategy PnL correlations — otherwise the monitor mostly rediscovers shared beta.
- **Reading $DR$ as a Tail Metric**: $DR$ is built from a covariance matrix and says nothing about co-movement in the left tail; a portfolio can hold $DR > 1.5$ and still have every pod lose together in a crisis.

## Verification

- Build three pods from mutually orthogonal, zero-mean sign vectors (exact $\rho = 0$, equal variance) at equal weight: verify $DR = \sqrt{3} \approx 1.7321$, no breaches, `is_diversification_healthy` true.
- Blend two orthogonal equal-variance columns as $z = \rho u + \sqrt{1-\rho^2} v$ with $\rho = 0.90$: verify the reported correlation is exactly 0.90 and severity is `REDUNDANT_POD`.
- Re-weight the orthogonal case to $(0.5, 0.25, 0.25)$: verify $DR = 1/\sqrt{0.375} \approx 1.6330$.
- Feed a pod with $\rho = 0.99$ against another so $DR < 1.20$: verify **both** the `REDUNDANT PODS` and the `Diversification Ratio` recommendations appear.
- Feed a flat (zero-variance) pod, a NaN, or a 2-row window: the engine must raise, **not** report a healthy portfolio or a redundant pair.
- Run `python -m unittest discover -s skills/cross-strategy-correlation-monitoring/scripts`.

## Related Skills

- `capital-reallocation-based-on-live-performance`
- `strategy-correlation-matrix-live-recomputation`
- `tail-correlation-between-strategies-under-stress`
- `multi-strategy-capital-allocation-limits`
