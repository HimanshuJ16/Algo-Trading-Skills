---
name: cross-sectional-vs-time-series-model-design
description: Quantitative model architecture selector for distinguishing between Cross-Sectional
  (relative ranking, dollar-neutral) and Time-Series (absolute trend, volatility-scaled)
  alpha strategies.
domain: Quant Research & Modeling
subdomain: Model Architecture Design
tags:
- quant-modeling
- cross-sectional
- time-series
- z-score
- market-neutral
- volatility-scaling
- alpha-factors
brokers_frameworks:
- Pandas
- NumPy
- Scikit-Learn
version: "2.0.0"
author: algo-trading-skills-contributors
license: Apache-2.0
---

## When to Use

Use this skill when designing quantitative trading models to choose between **Cross-Sectional (XSMOM / Relative Value)** and **Time-Series (TSMOM / Absolute Trend)** architectures. Cross-Sectional models evaluate assets relative to peers at a single point in time ($Z_{i,t} = \frac{X_{i,t} - \mu_{cs,t}}{\sigma_{cs,t}}$) to construct dollar-neutral long-short portfolios (Jegadeesh & Titman 1993; Asness, Moskowitz & Pedersen 2013). Time-Series models evaluate an asset relative to its own historical trajectory ($Z_{i,t} = \frac{X_{i,t} - \mu_{ts,i}}{\sigma_{ts,i}}$) to generate directional positions sized at $\sigma_{\text{target}}/\sigma_{i,t-1}$ (Moskowitz, Ooi & Pedersen 2012).

## When NOT to Use

- **When you need beta/market neutrality.** This engine enforces *dollar* neutrality ($\sum w_i = 0$) only. Dollar neutrality does **not** imply beta neutrality: $\sum_i w_i = 0$ places no constraint on $\sum_i w_i \beta_i$. A book long high-beta names and short low-beta names sums to zero dollars while carrying substantial net market exposure. Beta neutrality requires an explicit beta estimate and hedge — this skill does not provide one.
- **On small cross-sections with fat-tailed factors under default settings.** $\pm 3\sigma$ winsorization provably cannot bind at $K \le 10$ assets (see Pitfalls); use `winsorize_method="mad"` or `weighting="rank"`.
- **As a risk sizing system.** The transforms emit relative weights and a per-asset vol scalar. Portfolio-level exposure, drawdown, and leverage limits belong in dedicated controls (`correlation-aware-exposure-limits`, `kill-switch-and-drawdown-circuit-breakers`).
- **When the vol estimate is not strictly lagged.** If `asset_realized_vol_annual` is computed using the bar being sized, position sizes leak future information — see `lookahead-bias-elimination`.
- **For a single-asset universe requiring neutrality.** Contradictory mandate; the engine raises rather than recommending an architecture it cannot execute.

## Prerequisites

- Multi-asset feature matrix $X_{N, K}$ ($N$ timestamps, $K$ assets), free of NaN/Inf — the engine rejects non-finite input rather than imputing a factor value.
- Strategy mandates: dollar-neutrality requirement, target portfolio volatility $\sigma_{\text{target}}$.
- For time-series sizing: an **annualized** realized volatility per asset, estimated strictly from data **before** the bar being sized, plus at least `min_history` (default 5) observations of the *same* quantity as the current factor.

## Workflow

1. **Architecture Selection Audit**:
   - If the strategy requires dollar neutrality across $K \ge 2$ assets $\implies$ `CROSS_SECTIONAL`.
   - If the strategy trades directional trends on single assets or futures $\implies$ `TIME_SERIES`.
   - Decision point: a neutrality mandate on $K < 2$ assets, or neutrality combined with a single-asset trend flag, is a contradictory mandate — resolve it upstream rather than letting the selector pick one side silently.
2. **Cross-Sectional Factor Normalization**:
   - Winsorize first, and check the threshold can actually bind: with $K$ assets the largest attainable $|z|$ is $(K-1)/\sqrt{K}$, so a $\pm 3\sigma$ clip is inert for $K \le 10$. Switch to MAD-based clipping or rank weighting at small $K$.
   - For each timestamp $t$, standardize across the asset axis: $Z_{i,t} = \frac{X_{i,t} - \mu_{cs,t}}{\sigma_{cs,t}}$.
   - Normalize to $\sum w_i = 0$, $\sum |w_i| = 1$. Note that $\sigma_{cs,t}$ **cancels** in this normalization — the resulting weights equal $(X_i - \mu_{cs})/\sum_j |X_j - \mu_{cs}|$ and are invariant to the standardization. Z-scoring changes the reported diagnostics, not the book.
   - Where outlier influence matters, use rank weights instead: $w_{i,t} = c_t\left(\text{rank}(X_{i,t}) - \overline{\text{rank}}_t\right)$ (AMP 2013, eq. 1).
3. **Time-Series Volatility Scaling**:
   - Standardize over a lookback $W$ per asset $i$: $Z_{i,t} = \frac{X_{i,t} - \mu_{ts,i}}{\sigma_{ts,i}}$.
   - Size by the target volatility: $w_{i,t} = \text{sign}(Z_{i,t}) \times \frac{\sigma_{\text{target}}}{\sigma_{i,t-1}}$, capped at `max_leverage`.
   - Decision point: only $\text{sign}(Z)$ enters the weight — the magnitude is deliberately discarded, per the MOP trend rule. If a degenerate history makes $Z = 0$, emit a **flat** weight; do not fall back to a default z-score, which would size a full position off no evidence.
4. **Signal Validation**: Confirm $|\sum w_i| \le 10^{-5}$ on the **returned** weights (not on an unrounded intermediate), and that time-series weights scale inversely with realized volatility.

> Full procedure: see `references/workflows.md`.
> Standards reference: see `references/standards.md`.
> Printable pre-flight checklist: see `assets/checklist.md`.

## Common Pitfalls

- **Reading "dollar-neutral" as "market-neutral"**: $\sum w_i = 0$ constrains dollars, not beta. Long-high-beta / short-low-beta books are dollar-neutral with large net market exposure. Verify $\sum w_i \beta_i$ separately.
- **Trusting $\pm 3\sigma$ winsorization on a small universe**: for $K$ assets the maximum attainable z-score is $(K-1)/\sqrt{K}$ — 1.79 at $K=5$, 2.85 at $K=10$. The clip is *mathematically unreachable* below $K=11$, so a factor of 500 among values of 1-4 passes through untouched and takes ~97% of the gross book. Use MAD-based clipping or rank weights (AMP 2013 adopt ranks precisely to "mitigate the influence of outliers").
- **Rounding weights before reporting neutrality**: rounding $K$ weights to 4dp injects up to $K \times 5\times10^{-5}$ of net exposure. If the reported `net_exposure` is computed *before* rounding, it will read 0.0 while the weights actually returned breach the $10^{-5}$ standard.
- **Silent NaN propagation**: a single NaN factor makes `np.mean`/`np.std` NaN, and every weight NaN — a NaN weight is not a neutral position, it is an unhandled order size. Reject non-finite input at the boundary.
- **Flooring a bad volatility input**: clamping $\sigma_{realized} \le 0$ to a small floor turns garbage input into *maximum* leverage, the most dangerous possible response. Raise instead.
- **Sizing off a defaulted z-score**: falling back to $\mu = 0, \sigma = 1$ when history is short fabricates a full-size position from two observations. Require a real minimum history.
- **Look-ahead in the volatility estimate**: MOP (2012) apply $\sigma_{t-1}$ to time-$t$ returns explicitly "to ensure no look-ahead bias contaminates our results". A vol computed including the current bar inflates backtest Sharpe.
- **Mismatched factor and history units**: comparing a trailing 12-month return against the mean/std of *daily* returns produces a z-score on the wrong scale. Pass the history of the same quantity as the current factor.
- **Un-scaled Time-Series Positions**: sizing trend positions on raw momentum without scaling by realized volatility lets high-volatility assets dominate portfolio risk.
- **Gross-exposure convention drift**: this engine normalizes to $\sum|w| = 1$ ($0.50 long / $0.50 short); AMP (2013) scale to $1 long / $1 short ($\sum|w| = 2$). Reproducing published factor returns requires rescaling.

## Verification

- Cross-sectional weights are hand-checkable: for factors $[10, 50, -20, 30, -10]$, $\mu_{cs} = 12$, deviations $[-2, 38, -32, 18, -22]$, $\sum|dev| = 112$, so $w = dev/112$ — no $\sigma$ appears, confirming the standardization cancels.
- Rank weighting on the same input gives ranks $[2, 4, 0, 3, 1]$, minus mean rank 2, over $\sum|dev| = 6$.
- Over 500 random 13-asset draws, $|\sum w|$ on the **returned** weights must stay $\le 10^{-5}$ and $\sum|w| = 1$.
- With $\sigma_{target} = 15\%$: a 30% vol asset sizes to 0.5x, a 10% vol asset to 1.5x, and a 2% vol asset clips at the 2.0x `max_leverage` cap.
- Non-finite factors, non-positive volatility, and histories shorter than `min_history` must all raise — never return a weight.
- Run `python -m unittest discover -s skills/cross-sectional-vs-time-series-model-design/scripts`.

## Related Skills

- `ensemble-signal-combination-without-overfitting`
- `factor-research-multiple-testing-correction`
- `lookahead-bias-elimination`
- `dynamic-position-sizing-based-on-realized-volatility`
