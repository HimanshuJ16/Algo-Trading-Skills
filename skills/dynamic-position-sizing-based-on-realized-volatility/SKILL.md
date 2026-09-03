---
name: dynamic-position-sizing-based-on-realized-volatility
description: Use when calculating portfolio position sizes to scale allocations inversely
  to recent realized volatility (volatility targeting), maintaining a constant ex-ante
  risk budget across calm and volatile market regimes.
domain: algorithmic-trading
subdomain: risk-management
tags:
- risk-management
- volatility-targeting
- realized-volatility
- position-sizing
- riskmetrics-ema
- vol-scaling
brokers_frameworks:
- Realized Volatility Position Sizer
- Python NumPy
version: "2.0.0"
author: algo-trading-skills-contributors
license: Apache-2.0
---

## When to Use

Invoke this skill when executing trend-following, mean-reversion, or multi-asset strategies where static capital allocation ($X\%$ per trade) leads to unintentional risk concentration during high-volatility market regimes. Volatility targeting scales position size inversely to annualized realized volatility $\sigma_{\text{realized}}$, so a strategy consumes a constant *ex-ante* risk budget (e.g. $15\%$ target annualized volatility) whether the VIX is at $12$ or $45$.

## When NOT to Use

- **As a stop-loss, drawdown control, or gap protection.** The scalar is set from volatility *already realized*. It cannot anticipate a jump, and a position sized on calm data is full-size when the gap arrives. Pair it with an independent circuit breaker.
- **On a return history shorter than the estimator's effective window.** A $\lambda = 0.94$ EWMA consumes ~74 daily returns at a 1% tolerance (RiskMetrics Table 5.7). Below that the estimate mostly reflects the arbitrary seed, so the sizer raises rather than sizing.
- **On intraday bars without changing `annualization_factor`.** The default 252 is daily. Feeding 5-minute bars while leaving it at 252 understates volatility by roughly $\sqrt{78}$ and oversizes the position ~9×. The frequency cannot be inferred from a list of floats — you must set it.
- **As portfolio construction.** This sizes one asset at a time. Summing independently vol-targeted positions does not produce a vol-targeted portfolio; see `correlation-aware-exposure-limits`.
- **When the constraint is liquidity or lot size rather than volatility** — see `liquidity-adjusted-position-sizing` and `minimum-fill-size-and-lot-rounding-logic`.

## Prerequisites

- Return series $r_t$ **ending at the last completed observation before the bar being sized**, sampled at one consistent frequency.
- `annualization_factor` = observations per year for that frequency (252 for daily bars).
- Target annualized volatility $\sigma_{\text{target}}$ (e.g. $0.15 = 15\%$).
- Estimator choice: RiskMetrics EWMA ($\lambda = 0.94$ daily, $0.97$ monthly) or simple rolling sample standard deviation.

## Workflow

1. **Estimate Realized Volatility**:
   - EWMA (RiskMetrics Eq. 5.3): $\sigma_t^2 = \lambda \sigma_{t-1}^2 + (1 - \lambda) r_t^2$, using raw squared returns — RiskMetrics centres on zero, not on the sample mean.
   - Annualize: $\sigma_{\text{ann}} = \sqrt{F} \times \sigma_t$, where $F$ is observations per year.
   - **Decision point — check the history length before trusting the number.** `required_ewma_observations(λ, tolerance)` gives the effective window ($\lambda=0.94 \Rightarrow 74$ at 1%). A shorter history is not a noisy estimate, it is largely the seed; the sizer raises instead of returning one.
   - **Decision point — the two estimators are not interchangeable.** EWMA assumes a zero mean; the rolling estimator subtracts the sample mean and applies the $(n-1)$ correction. They disagree on identical data. Pick one per strategy; do not compare their outputs or switch mid-backtest.

2. **Compute Volatility Scalar**:
   $$\text{Scalar} = \frac{\sigma_{\text{target}}}{\max(\sigma_{\text{floor}}, \sigma_{\text{ann}})}$$
   - **Decision point — if `vol_floor_binding` is true**, the size was set by the floor, not by measured volatility. That is a deliberate leverage brake on an abnormally quiet series, not a volatility reading; do not report it as one.

3. **Apply Min/Max Multiplier Bounds**:
   $$\text{FinalScalar} = \text{clip}(\text{Scalar}, \text{MinScalar}, \text{MaxScalar})$$

4. **Calculate Volatility-Adjusted Allocation**:
   $$\text{CapitalAllocation} = \text{BaseCapital} \times \text{FinalScalar}$$
   - Share count is **floored**, never rounded up, so the position cannot exceed the risk budget.

> Full procedure: see `references/workflows.md`.
> Standards reference: see `references/standards.md`.
> Printable pre-flight checklist: see `assets/checklist.md`.

## Common Pitfalls

- **Sizing on corrupt data**: a single `NaN` in the return series collapses a naive variance calculation to zero, which the volatility floor then converts into the *maximum* leverage scalar — the largest possible position produced by the worst possible data. Reject non-finite returns before estimating; never let them reach the estimator.
- **Silently sizing with no data**: returning the target volatility as a fallback yields a scalar of exactly $1.0$ — a full-size position justified by nothing. Absent or too-short history must raise, not default.
- **Including the current bar's return**: the volatility used to size period $t$ must be built from returns through $t-1$ (RiskMetrics Eq. 5.37). Including the bar being sized leaks its outcome into the size and flatters every backtest.
- **Mismatched annualization**: intraday returns annualized with 252 understate volatility by $\sqrt{\text{bars per day}}$, producing a position several times larger than intended.
- **Reporting the floored volatility as realized volatility**: when the floor binds it is a sizing guard, not a measurement; conflating the two overstates a genuinely quiet asset's risk in every downstream report.
- **Lagged Volatility Response During Crashing Markets**: a 60-day simple estimator reacts too slowly during a sudden crash, leaving positions oversized. Faster $\lambda$ reacts sooner but consumes less data — the trade-off is explicit in RiskMetrics Table 5.7.
- **Hyper-Leveraging in Ultra-Quiet Regimes**: allowing the scalar to reach $10\times$ during abnormally low vol without enforcing a `MaxScalar` cap.
- **Assuming the floor caps leverage**: with $\sigma_{\text{target}}=15\%$ and $\sigma_{\text{floor}}=5\%$ the floor permits a raw scalar of $3.0$, so with `MaxScalar` $=2.0$ it is the *cap*, not the floor, that binds. Set both deliberately.

## Verification

- Instantiate `RealizedVolPositionSizer(target_annualized_vol=0.15, min_scalar=0.20, max_scalar=2.00, vol_floor=0.05)`. Feed an alternating $\pm d$ return series with $d = 0.60/\sqrt{252}$ (100 observations, exactly 60% annualized): verify `realized_annualized_vol` $= 0.60$, `bounded_vol_scalar` $= 0.25$, and `adjusted_capital_usd` $= \$25{,}000$ on $\$100{,}000$ base. Repeat at 4% annualized: verify the raw scalar is $3.0$ (floor-bound, not $3.75$), clipped to the $2.0$ cap, with `vol_floor_binding` true.
- Verify `required_ewma_observations(0.94, 0.01) == 74` and `(0.97, 0.01) == 151`, reproducing RiskMetrics Table 5.7.
- Negative checks: an empty history, a 1-element history, a 73-element history at $\lambda=0.94$, a `NaN` return, and a negative `base_capital_usd` must each raise.
- Run `python -m unittest discover -s skills/dynamic-position-sizing-based-on-realized-volatility/scripts` and confirm 100% pass rate.

## Related Skills

- `correlation-aware-exposure-limits`
- `liquidity-adjusted-position-sizing`
- `value-at-risk-var-live-monitoring`
- `kill-switch-and-drawdown-circuit-breakers`
