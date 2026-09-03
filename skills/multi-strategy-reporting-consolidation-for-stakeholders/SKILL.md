---
name: multi-strategy-reporting-consolidation-for-stakeholders
description: >-
  Use when reporting consolidated performance to a risk committee or investors,
  recomputing portfolio volatility, Sharpe, max drawdown and the diversification ratio
  from joint returns rather than averaging strategy-level figures.
license: Apache-2.0
metadata:
  domain: algorithmic-trading
  subdomain: portfolio-multi-strategy
  tags: multi-strategy, reporting, stakeholder-reporting, portfolio-attribution, sharpe-ratio, diversification-ratio, pnl-consolidation
  brokers_frameworks: "Executive Reporting Engine; Portfolio Performance Attribution; Python Dataclasses"
  version: "2.0.0"
  author: algo-trading-skills-contributors
---

## When to Use

Use this skill when reporting consolidated performance across multiple sub-strategies (e.g. Statistical Arbitrage, Trend Following, Options Market Making) to fund managers, risk committees, and LP investors. Summing or averaging strategy-level metrics distorts every risk figure because sub-strategy returns are not perfectly correlated ($\rho_{ij} < 1$): the portfolio Sharpe ratio is not the mean of sleeve Sharpe ratios, and the portfolio drawdown is neither the sum nor the maximum of sleeve drawdowns, because sleeves trough on different dates. This engine synthesizes the capital-weighted joint daily return series and recomputes portfolio volatility, Sharpe ratio, max drawdown, and the diversification ratio from that series.

## When NOT to Use

- **As a GIPS-compliant or SEC-marketing-ready performance report.** The engine consolidates whatever PnL and returns it is given and applies no fee, carry, or transaction-cost model. If the inputs are gross, the output is gross. SEC rule 17 CFR 275.206(4)-1(d)(1) requires gross performance in an advertisement to be accompanied by net performance with equal prominence and computed over the same period by the same methodology; GIPS 2020 Provision 2.A.13 requires returns net of transaction costs. Feed net inputs, or treat the output as an internal figure.
- **On a window shorter than one year, for external presentation.** GIPS 2020 Provision 2.A.12: "Returns for periods of less than one year must not be annualized." The engine still computes the annualized figures but flags the window in `report.warnings`.
- **On return series that are not already aligned by date.** `daily_returns` carries no timestamps, so the engine cannot align sleeves itself. Unequal lengths are rejected rather than truncated.
- **On heavily smoothed or illiquid marks, without a caveat.** $\sqrt{252}$ Sharpe annualization assumes serially uncorrelated returns; Lo (2002) shows a hedge fund's annual Sharpe can be overstated by as much as 65% when returns are serially correlated.
- **For factor or benchmark decomposition.** This measures the portfolio against itself, not against market exposure — see `benchmark-relative-performance-attribution` and `strategy-performance-attribution-vs-market-beta`.

## Prerequisites

- Sub-strategy telemetry payloads (`strategy_id`, `allocated_capital_usd`, `realized_pnl_usd`, `unrealized_pnl_usd`, `daily_returns`, `max_drawdown_pct`), with every `daily_returns` series **covering the same dates and therefore the same length**.
- Returns as decimals (0.001 = 0.1%), finite, and no worse than $-1.0$ ($-100\%$).
- Reporting config (`portfolio_name`, `risk_free_rate_ann`: e.g. 0.04, `trading_days_per_year`: e.g. 252 for daily bars).

## Workflow

1. **Validate before aggregating**:
   - Reject duplicate `strategy_id` (the same sleeve would be double-counted in capital, PnL, and weights), negative or non-finite allocated capital, non-finite PnL, and non-finite returns.
   - **Decision point — unequal series lengths are a rejection, not a truncation.** Truncating to the shortest series pairs a late-launch sleeve with the *oldest* observations of the longer-running ones, so every co-movement-dependent figure is computed from mismatched dates. Align by date upstream instead.
2. **Capital & PnL Aggregation**:
   - $C_{\text{total}} = \sum C_k$, $\text{PnL}_{\text{total}} = \sum (\text{realized}_k + \text{unrealized}_k)$.
   - **Decision point — reconcile the two return measures.** `portfolio_return_pct` (PnL over allocated capital) and `series_implied_return_pct` (compounded joint series) describe the same portfolio and window. A material gap means the PnL and the return series came from different systems, books, or date ranges; resolve it before publishing either number.
3. **Joint Return Synthesis & Volatility**:
   - $R_{p,t} = \sum_{k=1}^K w_k R_{k,t}$ where $w_k = C_k / C_{\text{total}}$ (fixed allocation weights, so this is a rebalanced-to-allocation series, not buy-and-hold).
   - $\sigma_p = \text{stdev}(R_{p,t}) \cdot \sqrt{252}$, using the sample $(n-1)$ standard deviation.
4. **Sharpe Ratio & Diversification Benefit Audit**:
   - $SR_p = \dfrac{\text{mean}(R_{p,t}) \cdot 252 - R_f}{\sigma_p}$ — arithmetic annualization, so this is not derivable from a CAGR.
   - $\text{DR} = \dfrac{\sum w_k \sigma_k}{\sigma_p}$ (Choueifaty & Coignard 2008, Eq. 1).
   - **Decision point — if $\sigma_p = 0$, both are undefined, not large.** Perfectly offsetting sleeves make the window risk-free; the engine returns `NaN` and a warning rather than substituting a placeholder volatility.
5. **Drawdown Consolidation**: compute `portfolio_max_drawdown_pct` from the compounded joint equity path, and compare it against `max_strategy_max_drawdown_pct` (worst single sleeve over the same window). The portfolio figure cannot be derived from the sleeve figures.
6. **Audit Report Generation**: output `ConsolidatedStakeholderReport` and **read `report.warnings` before quoting any figure** — the status stays `REPORT_CONSOLIDATED_SUCCESS` even when individual metrics are qualified or `NaN`.

> Full procedure: see `references/workflows.md`.
> Standards reference: see `references/standards.md`.
> Printable pre-flight checklist: see `assets/checklist.md`.

## Common Pitfalls

- **Naive Metric Summation**: averaging sub-strategy Sharpe ratios instead of computing the portfolio Sharpe from the joint return series. The same error applied to drawdown is worse: the maximum of the sleeve drawdowns is neither an upper nor a lower bound on the portfolio drawdown, because sleeves trough on different dates.
- **Substituting a placeholder for an undefined denominator**: when the weighted sleeves offset perfectly, $\sigma_p = 0$ and the Sharpe ratio and diversification ratio are undefined. Replacing the zero with a small constant such as `0.0001` does not degrade gracefully — it manufactures a headline Sharpe in the hundreds and a diversification ratio in the tens, and those are the numbers that reach the LP deck. Report `NaN`.
- **Silently truncating misaligned series**: taking the first $\min_k n_k$ observations of every sleeve aligns them by *index from the start*, so a sleeve that launched later is paired with the oldest observations of the others. The resulting diversification ratio measures a co-movement that never happened.
- **Unweighted Return Aggregation**: treating equal dollar allocations across strategies with vastly different capital bases.
- **Reading a negative-PnL contribution share as a loss**: `pnl_contribution_pct` is a share of total PnL. When the portfolio loses money, the sign inverts — a *profitable* sleeve shows a negative share. The shares still sum to 100%; the engine warns whenever total PnL is negative and returns `NaN` shares when it is exactly zero.
- **Presenting an annualized figure from a short window**: annualizing a 50-day sample produces a headline Sharpe with no statistical support and, under GIPS 2020 Provision 2.A.12, one that must not appear in a GIPS report at all.
- **Treating $\sqrt{252}$ scaling as exact**: it assumes serially uncorrelated returns. Smoothed or illiquid marks are positively autocorrelated, which understates volatility and inflates the reported Sharpe (Lo 2002).
- **Publishing gross PnL as an LP return**: the engine applies no management fee, performance fee, or transaction-cost model. Whatever goes in comes out.

## Verification

- Instantiate `MultiStrategyReportingConsolidatorEngine` with two $\$500{,}000$ anti-correlated sleeves whose daily returns are $A_t = 0.0006 \pm 0.010$ and $B_t = 0.0004 \mp 0.004$ over 50 observations. Because each sleeve deviates from its own mean by a constant amplitude, the diversification ratio is exact and independent of the sample size and annualization factor: $\text{DR} = (0.5 \cdot 0.010 + 0.5 \cdot 0.004) / (0.5 \cdot (0.010 - 0.004)) = 7/3 = 2.33$. Verify total capital $= \$1{,}000{,}000$, portfolio volatility $= 4.81\%$ against a weighted-sum volatility of $11.22\%$, portfolio Sharpe $= 1.79$ (above the $0.82$ naive average of the sleeve Sharpes $0.69$ and $0.95$), and status `REPORT_CONSOLIDATED_SUCCESS`.
- Feed two sleeves that each lose $10\%$ on *different* days: `max_strategy_max_drawdown_pct` is $10.0$ while `portfolio_max_drawdown_pct` is $1 - 0.95^2 = 9.75\%$.
- Feed two perfectly offsetting sleeves ($A = [+1\%, -0.5\%]$, $B = [-0.5\%, +1\%]$, equal weight): portfolio volatility must be $0.0$ with `portfolio_sharpe_ratio` and `diversification_ratio` both `NaN` and a populated warning — **not** a Sharpe of $371.67$ and a diversification ratio of $74.23\times$.
- Negative checks: an empty list, unequal series lengths, fewer than 2 observations, a `NaN`/`Inf` return, a return below $-100\%$, negative or non-finite allocated capital, zero total capital, and a duplicate `strategy_id` must each raise `ConsolidationError` (a `ValueError` subclass).
- Run `python -m unittest discover -s skills/multi-strategy-reporting-consolidation-for-stakeholders/scripts` and confirm 100% pass rate.

## Related Skills

- `strategy-performance-attribution-vs-market-beta`
- `benchmark-relative-performance-attribution`
- `risk-adjusted-performance-attribution-per-strategy`
- `cross-strategy-correlation-monitoring`
