---
name: backtest-reporting-standardized-tearsheet
description: >-
  Use at the end of a backtest run to produce one standard performance sheet (Sharpe,
  Sortino, Calmar, max drawdown, hit rate, profit factor) from a per-period returns
  array, so strategies are compared on identical metrics.
license: Apache-2.0
metadata:
  domain: algorithmic-trading
  subdomain: backtesting-methodology
  tags: backtesting-methodology, tearsheet, performance-metrics, sharpe-ratio, drawdown, backtest-reporting
  brokers_frameworks: "Standardized Tearsheet Generator; Python NumPy"
  version: "2.0.0"
  author: algo-trading-skills-contributors
---

## When to Use

Invoke this skill at the conclusion of every backtest run. Reporting fragmented or customized performance metrics makes comparing different trading strategies subject to cherry-picking bias. Generating a standardized performance tearsheet ensures every strategy is benchmarked against identical risk-adjusted returns, downside risk, trade statistics, and drawdown distribution rules.

"Standardized" only holds if the conventions are pinned down, because the same returns produce different Sharpe and Calmar values under different defensible choices. This implementation fixes them explicitly and `references/standards.md` records each one with its source.

## When NOT to Use

- **Not a trade-level analysis.** The generator consumes a per-period returns array. `Hit Rate` is the fraction of *periods* that were positive, which is not the same number as a per-trade win rate — a strategy holding one winning trade across 20 flat days has a 100% trade win rate and a 5% daily hit rate. For per-trade statistics, aggregate the trade log first and feed per-trade returns with `periods_per_year` set accordingly.
- **Not a compliance-ready performance presentation.** Under the SEC Marketing Rule, 17 CFR § 275.206(4)-1(e)(8), backtested results are *hypothetical performance*: "performance results that were not actually achieved by any portfolio of the investment adviser". A US-registered investment adviser putting this tearsheet in an advertisement must satisfy the conditions in § 275.206(4)-1(d)(6). This is a US adviser-advertising rule; it does not govern internal research. Check the rule and your own jurisdiction before distributing.
- **Not a correction for selection bias.** These are descriptive statistics for one backtest. If the parameters were chosen by searching many configurations, the reported Sharpe is the maximum of many noisy estimates and overstates expectation. See `backtest-parameter-sensitivity-analysis` and `factor-research-multiple-testing-correction`.
- **Not a verdict.** Nothing here says a strategy is deployable. Pair it with out-of-sample and regime evidence.
- **Not for log returns.** Inputs must be simple per-period returns; the equity curve compounds them as $\prod(1+R_t)$.

## Prerequisites

- Daily portfolio returns series $R_t$ as **simple** decimal returns, chronologically ordered, with no gaps.
- Annual risk-free rate $r_f$ (e.g. 0.04) on the same annualization basis as `periods_per_year`.
- At least 2 observations; annualized figures from less than one year are flagged, not suppressed.

## Workflow

1. **Set the Annualization Basis First**: `periods_per_year` must match the sampling frequency — 252 daily, 52 weekly, 12 monthly. Using 365 on a trading-day series overstates annualized volatility by roughly 20% and understates Sharpe by the same factor.
2. **Calculate Risk-Adjusted Ratios**: Sharpe uses arithmetic annualized excess return over annualized sample standard deviation (`ddof=1`). Sortino replaces the denominator with target downside deviation, averaging the squared below-target shortfalls over **all** periods rather than only the losing ones. Calmar divides CAGR by the absolute maximum drawdown; the excess-return variant is available via `calmar_uses_excess_return` and the report labels which was used.
3. **Compute Drawdown Statistics**: Maximum drawdown is measured on an equity curve **seeded at 1.0**, so a decline starting on the first period counts against starting capital. Also reported: periods from peak to trough, and periods from trough back to the prior peak (`None` if never recovered).
4. **Compute Per-Period Trade Statistics**: Hit rate, profit factor (gross win / gross loss) and average win/loss, all on periods, not trades.
5. **Read the Degenerate Values Literally**: A zero denominator returns $\pm\infty$ or `nan`, never `0.0`. An infinite Sharpe means zero variance, not a bad strategy; a `nan` profit factor means there were neither gains nor losses.
6. **Format Tearsheet Summary**: Generate structured report dictionary for standardized presentation, carrying `Periods`, `Calmar Convention` and `Annualization Extrapolated` alongside the metrics so the numbers stay interpretable.

> Full procedure: see `references/workflows.md`.
> Standards reference: see `references/standards.md`.
> Printable pre-flight checklist: see `assets/checklist.md`.

## Common Pitfalls

- **Annualizing Sharpe with Wrong Frequency**: Multiplying daily Sharpe by $\sqrt{365}$ instead of trading days $\sqrt{252}$.
- **Ignoring Downside Volatility**: Reporting high Sharpe for strategies with severe left-tail crash risk (negative skewness).
- **Unseeded Drawdown Curve**: Starting the running maximum at the first period's close instead of at starting capital. A series that opens $-50\%$ then recovers slightly reports a maximum drawdown of **zero**, and three consecutive $-10\%$ periods report $-19\%$ instead of the true $-27.1\%$.
- **Dividing Downside Deviation by the Losing Periods**: Averaging squared shortfalls over only the below-target observations instead of all of them inflates Sortino — by a factor of 2 in a series where a quarter of the periods are losses.
- **Silent NaN**: A NaN return leaves hit rate and profit factor looking perfectly normal while every risk statistic becomes `nan`, producing a report that is half plausible. Reject it at the boundary.
- **Annualizing a Handful of Periods**: One $+5\%$ day compounds to over $20{,}000{,}000\%$ per year. The figure is arithmetically correct and completely meaningless.
- **Reporting 0.0 for a Degenerate Denominator**: A flawless constant-return curve has infinite Sharpe and infinite Calmar. Collapsing those to `0.0` makes the best possible result look like one of the worst. Note also that `numpy.std` of a constant series returns rounding error near $10^{-18}$, not exact zero, so an unguarded division yields an absurd finite value like $8.7\times10^{16}$.
- **Comparing Calmar Across Sources**: Both $\text{CAGR}/|\text{maxDD}|$ and $(\text{CAGR}-r_f)/|\text{maxDD}|$ are in circulation. They differ whenever $r_f \neq 0$; always state which one produced the number.
- **Returns Below $-100\%$**: These drive the equity curve negative, where CAGR and drawdown are undefined. Before this was guarded, a $-150\%$ return produced a reported drawdown of $-151\%$.

## Verification

- Submit test return series, verify tearsheet output contains Sharpe, Sortino, Calmar, and Max Drawdown.
- Assert the seeded-curve drawdown: returns $[-0.50, +0.10, +0.05]$ must report a maximum drawdown of exactly $-0.50$.
- Assert the Sharpe closed form: returns $[a, 0, a, 0]$ with `periods_per_year=4` and $r_f=0$ give exactly $\sqrt{3}$, independent of $a$.
- Assert the Sortino convention: returns $[0.02, 0, 0.02, -0.02]$ with `periods_per_year=4` and $r_f=0$ give exactly $1.0$; dividing by the losing-period count instead would give $0.5$.
- Cross-check maximum drawdown against a brute-force $O(n^2)$ peak-to-trough scan on real data.
- Run `python -m unittest discover -s skills/backtest-reporting-standardized-tearsheet/scripts` and confirm 100% pass rate.

## Related Skills

- `benchmark-relative-performance-attribution`
- `paper-to-live-promotion-checklist`
- `backtest-parameter-sensitivity-analysis`
- `monte-carlo-strategy-robustness-testing`
---
