---
name: risk-adjusted-performance-attribution-per-strategy
description: >-
  Use when several strategies share one risk budget and you need to know which earn
  their risk: per-strategy Sharpe, Sortino, Calmar and max drawdown, plus Euler
  decomposition of portfolio volatility across them.
license: Apache-2.0
metadata:
  domain: algorithmic-trading
  subdomain: risk-management
  tags: performance-attribution, sharpe-ratio, sortino-ratio, calmar-ratio, max-drawdown, risk-contribution, euler-decomposition
  brokers_frameworks: "Sharpe (1994); Sortino & Price (1994); Calmar (Young 1991); Euler Risk Decomposition; Python Standard Library"
  version: "2.0.0"
  author: algo-trading-skills-contributors
---

## When to Use

Use this skill when several strategies share one risk budget and you need to know which of them earn their risk, not merely which made money. It computes per-strategy Sharpe, Sortino, Calmar and max drawdown from a realized return series, then decomposes portfolio volatility across the strategies using the Euler identity, so capital allocation, risk-budget rebalancing, and retirement decisions rest on each strategy's *contribution to portfolio risk* rather than its standalone volatility.

## When NOT to Use

- **As a forecast.** Every figure describes the window supplied. A strategy's realized Sharpe is not its expected Sharpe, and ranking on a short window mostly ranks luck.
- **On return series shorter than the reporting horizon you intend to claim.** Annualizing a five-day sample is arithmetically valid and statistically meaningless. The engine flags this via `insufficient_history_warning` rather than refusing, because a short window is legitimate for monitoring — but do not publish the annualized number.
- **To explain performance against a benchmark or market factor.** This decomposes *risk across strategies*, not return across factors. For benchmark-relative attribution see `benchmark-relative-performance-attribution`; for beta separation see `strategy-performance-attribution-vs-market-beta`.
- **To set the weights.** This measures the risk contributions of weights you already have. To *solve* for equal-risk-contribution weights, use `risk-parity-allocation-across-strategies`.
- **On overlapping or non-independent series without care.** Serial correlation inflates Sharpe (Lo 2002) and breaks the $\sqrt{252}$ annualization Sharpe (1994) assumes.
- **As a risk control.** It is a reporting engine. Drawdown enforcement belongs in `kill-switch-and-drawdown-circuit-breakers`.

## Prerequisites

- Per-strategy simple periodic return series (`strategy_id`, `daily_returns`), net of fees and financing.
- **Equal length, position-aligned.** The engine holds no timestamps, so all series must cover the same periods in the same order. Align on dates upstream.
- Every return strictly greater than $-1.0$; non-finite values resolved before the call.
- Portfolio weights summing to $1.0$ (defaults to equal weight).
- Risk-free rate / MAR (`risk_free_rate_annual`, default 5%), set per engine or overridden per strategy.

## Workflow

1. **Validate and align the inputs**:
   - Reject non-finite returns and any return $\le -1.0$. A return below $-1.0$ implies negative equity; compounding it to an annualized figure raises a negative base to a fractional power and yields a complex number, and it produces a max drawdown above 100%.
   - **Decision point — ragged series are an error, not something to trim.** Truncating to the shortest series compares strategies over different periods and silently discards the newest observations of the longer ones. Fix the alignment upstream.

2. **Per-Strategy Metric Calculation**:
   - Compounded total return $\prod(1+r_t)-1$, geometric annualized return, annualized volatility $\sigma_d\sqrt{252}$, max drawdown, then Sharpe, Sortino and Calmar.
   - The risk-free rate is de-annualized **geometrically**, $(1+r_f)^{1/252}-1$, to match how returns compound. The $r_f/252$ shortcut overstates the daily hurdle by about 2.5% at a 5% rate.
   - **Decision point — an undefined ratio is `None`, never `0.0`.** A strategy with zero volatility has no Sharpe; one that never fell below the MAR has no Sortino; one that never drew down has no Calmar. Reporting $0.0$ ranks the best strategy below a mediocre one. Handle `None` explicitly when sorting — read `undefined_ratios` for the reason.

3. **Portfolio-Level Blended Returns**:
   - Build the weighted portfolio return series $r_{p,t}=\sum_i w_i r_{i,t}$ and compute its annualized return, volatility and Sharpe.

4. **Euler Risk Decomposition**:
   $$\mathrm{MCR}_i = \frac{(\Sigma w)_i}{\sigma_p}, \qquad \mathrm{CR}_i = w_i\,\mathrm{MCR}_i, \qquad \sum_i \mathrm{CR}_i = \sigma_p$$
   - `risk_contribution_pct` reports $\mathrm{CR}_i/\sigma_p$, summing to 100%.
   - **Decision point — a negative contribution is a real result, not an error.** A diversifying strategy legitimately removes risk. Do not clip it to zero, and do not rank on its absolute value.
   - **Decision point — if `risk_decomposition_available` is false**, portfolio volatility is zero (fully offsetting strategies) and the contributions are $0/0$. They are `None`. Do not substitute the weighted-volatility share.

5. **Attribution Report**: Output the structured `PortfolioAttributionReport`, checking `insufficient_history_warning` before quoting any annualized figure.

> Full procedure: see `references/workflows.md`.
> Standards reference: see `references/standards.md`.
> Printable pre-flight checklist: see `assets/checklist.md`.

## Common Pitfalls

- **Treating a weighted-volatility share as risk attribution**: $w_i\sigma_i / \sum_j w_j\sigma_j$ ignores correlation and is only correct when every pairwise correlation is $+1$. With a hedge in the book it is not imprecise but *directionally wrong* — it assigns a large positive risk contribution to a strategy that is removing risk. A perfectly hedged pair has zero portfolio volatility and this formula still reports 50%/50%. It is retained only as `standalone_volatility_share_pct`, a measure of gross scale.
- **Reading `0.0` as a real score for an undefined ratio**: a strategy returning 65% annualized with no drawdown has *no* Calmar, not a Calmar of zero. Defunding it because it sorted last is the exact inversion of the decision this skill exists to support.
- **Summing returns instead of compounding them**: $-50\%$ followed by $+100\%$ sums to $+50\%$ while the investor is exactly flat.
- **Comparing a short-window Calmar to a published one**: Young's 1991 convention is a trailing **36-month** window evaluated monthly. A Calmar computed over six weeks is a different statistic wearing the same name.
- **Comparing Sortino ratios computed against different MARs**: the ratio is only comparable across strategies when the minimum acceptable return is identical. The engine allows a per-strategy override precisely so this is a deliberate choice — which also means it is easy to make accidentally.
- **Dividing the downside deviation by the count of losing periods**: the denominator is the *total* number of observations. Using only the losing periods roughly doubles the denominator's square root on a mostly-positive series and silently inflates every Sortino ratio.
- **Trusting downside deviation from a mostly-positive sample**: Sortino & Forsey (1996) show the discrete historical method significantly understates downside risk when few returns fall below the MAR — the Japanese equity market showed ten straight positive years before falling 39%.
- **Ranking on Sharpe alone**: Sharpe (1994) notes the ratio takes no account of correlation. A strategy with a mediocre Sharpe that diversifies the book can be worth more than a high-Sharpe strategy that duplicates existing exposure — which is what the Euler contribution is for.
- **Ignoring the alignment assumption**: the engine can detect *ragged* series but not *misaligned* ones. Two equal-length series offset by one day will produce a confidently wrong covariance and therefore a confidently wrong decomposition.

## Verification

- Instantiate `RiskAdjustedPerformanceAttributionEngine(risk_free_rate_annual=0.05)`.
- **Euler decomposition, hand-derived**: feed strategy $B = -0.5A$ at weights $[0.5, 0.5]$. With $v=\mathrm{Var}(A)$, $\sigma_p^2 = 0.0625v$, so $\mathrm{CR}_A = +200\%$ and $\mathrm{CR}_B = -100\%$, summing to 100%. Confirm `standalone_volatility_share_pct` instead reports $66.67\%/33.33\%$ — the correlation-blind answer that hides the hedge.
- **Ranking inversion**: feed a constant $+0.2\%$ daily series. Verify `annualized_return` $= 1.002^{252}-1 \approx 0.6545$ and that `sharpe_ratio`, `sortino_ratio` and `calmar_ratio` are all `None` with three entries in `undefined_ratios` — not $0.0$.
- **Max drawdown**: `[0.10, -0.20, 0.05]` gives equity $1.10, 0.88, 0.924$, so `max_drawdown` $= 0.20$ exactly.
- **Compounding**: `[-0.5, 1.0]` gives `total_return` $= 0.0$, not $0.5$.
- **Downside deviation denominator**: with MAR $=0$ and `[0.10, 0.10, 0.10, -0.10]`, verify $\sqrt{0.01/4}\times\sqrt{252} = 0.05\sqrt{252}$ — averaged over all four observations, not the single losing one.
- **Perfect hedge**: feed $A$ and $-A$; verify `risk_decomposition_available` is false and every `risk_contribution_pct` is `None`.
- **Negative checks**: an empty list, a 1-observation series, a `NaN`, a return of $-1.0$ or below, a ragged pair of series, mismatched weight length, and weights not summing to $1.0$ must each raise.
- Run `python -m unittest discover -s skills/risk-adjusted-performance-attribution-per-strategy/scripts` and confirm 100% pass rate.

## Related Skills

- `risk-parity-allocation-across-strategies`
- `strategy-performance-attribution-vs-market-beta`
- `benchmark-relative-performance-attribution`
- `cross-strategy-correlation-monitoring`
- `strategy-lifecycle-retirement-criteria`
- `kill-switch-and-drawdown-circuit-breakers`
