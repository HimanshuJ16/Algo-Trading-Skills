# Standards — multi-strategy-reporting-consolidation-for-stakeholders

## Metric definitions (verified against primary sources)

| Metric | Definition as implemented | Source |
|---|---|---|
| Joint return series | $R_{p,t} = \sum_k w_k R_{k,t}$, $w_k = C_k / C_{\text{total}}$, weights fixed across the window | Allocation-weighted by construction; see limitations below. |
| Portfolio volatility | $\sigma_p = \text{stdev}(R_{p,t}) \cdot \sqrt{F}$, sample $(n-1)$ standard deviation, $F$ = observations per year | $\sqrt{F}$ scaling is the square-root-of-time rule and assumes serially uncorrelated returns. |
| Portfolio Sharpe ratio | $SR_p = (\text{mean}(R_{p,t}) \cdot F - R_f) / \sigma_p$ | Sharpe, W.F. (1994), "The Sharpe Ratio", *Journal of Portfolio Management* 21(1), 49–58. The ex post ratio is the mean *differential* return over the standard deviation of that differential return; with a constant $R_f$ the differential series has the same standard deviation as the raw series, so the form above is algebraically identical. Annualization is arithmetic, not geometric. |
| Diversification ratio | $\text{DR} = (\sum_k w_k \sigma_k) / \sigma_p$ | Choueifaty, Y. & Coignard, Y. (2008), "Toward Maximum Diversification", *Journal of Portfolio Management* 35(1), 40–51, **Eq. (1)**: "the ratio of the weighted average of volatilities divided by the portfolio volatility." ([TOBAM reprint](https://www.tobam.fr/wp-content/uploads/2014/12/TOBAM-JoPM-Maximum-Div-2008.pdf)) |
| Portfolio max drawdown | Peak-to-trough of the compounded joint equity path $\prod_t (1 + R_{p,t})$ | Not recoverable from sleeve drawdowns, which trough on different dates. |

**$\text{DR} \ge 1$ is a property of non-negative weights, not a law.** The paper defines the
ratio over long-only portfolios. With a negative weight the inequality $\sigma_p \le \sum_k w_k \sigma_k$
no longer holds, so the engine rejects negative `allocated_capital_usd` rather than
publishing a ratio that cannot be interpreted as a diversification benefit.

## Regulatory and standards touchpoints

These constrain how the engine's output may be *presented*; they do not change the
arithmetic. Jurisdiction and applicability are stated explicitly — none of them are
universal.

| Requirement | Source | Applies to | Effect here |
|---|---|---|---|
| "Returns for periods of less than one year must not be annualized." | GIPS 2020 for Firms, **Provision 2.A.12** ([CFA Institute PDF](https://www.gipsstandards.org/wp-content/uploads/2021/03/2020_gips_standards_firms.pdf)) | Firms claiming GIPS compliance (voluntary standard, global) | The engine emits a warning whenever `observations < trading_days_per_year`. It still computes the annualized figures — they are useful internally — but they must not be presented as GIPS-compliant returns. |
| "All returns must be calculated after the deduction of transaction costs incurred during the period." | GIPS 2020 for Firms, **Provision 2.A.13** | Same | The engine applies no cost model. Inputs must already be net of transaction costs for the output to satisfy this. |
| "Total returns must be used." | GIPS 2020 for Firms, **Provision 2.A.8** | Same | Sleeve return series must include income, not price change alone. |
| Three-year annualized ex post standard deviation, computed from **monthly** returns, as of each annual period end | GIPS 2020 for Firms, **Provision 4.A.1.j** | Same | The volatility this engine produces is annualized from *daily* returns over the supplied window. It is a different statistic from the GIPS-required 36-month figure and is not a substitute for it. |
| "Any presentation of gross performance [is prohibited] unless the advertisement also presents net performance: (i) With at least equal prominence to, and in a format designed to facilitate comparison with, the gross performance; and (ii) Calculated over the same time period, and using the same type of return and methodology, as the gross performance." | SEC marketing rule, **17 CFR 275.206(4)-1(d)(1)** | SEC-registered investment advisers (US); "advertisement" as defined in paragraph (e) | If the telemetry is gross, the consolidated report is gross and cannot stand alone in an advertisement. |
| Performance of a portfolio or composite must be shown for one-, five-, and ten-year periods with equal prominence | SEC marketing rule, **17 CFR 275.206(4)-1(d)(2)** | SEC-registered advisers; **expressly excludes private funds** | A private-fund LP report is outside (d)(2), but an adviser advertising a separately managed multi-strategy composite is not. Confirm which applies before publishing. |

## Known limitations

- **$\sqrt{F}$ Sharpe annualization requires IID returns.** Lo, A.W. (2002), "The
  Statistics of Sharpe Ratios", *Financial Analysts Journal* 58(4), 36–52: the
  square-root-of-time conversion holds only under restrictive conditions, and a hedge
  fund's annual Sharpe ratio can be overstated by as much as 65% when monthly returns
  are serially correlated. Smoothed or illiquid marks — common in exactly the sleeves
  this engine consolidates — are that case.
- **Fixed allocation weights.** The joint series is rebalanced to the allocation each
  period. A buy-and-hold portfolio whose weights drifted over the window will not match.
- **No fee, carry, or cost model.** Gross in, gross out.
- **No date alignment.** `daily_returns` is an untimestamped list; alignment is the
  caller's responsibility and unequal lengths are rejected.
- **Undefined metrics are `NaN`.** A zero-volatility window leaves the Sharpe ratio and
  the diversification ratio undefined. The degeneracy test is exact equality with zero,
  not a tolerance — a tolerance such as `1e-8` would misclassify a genuinely low-volatility
  market-neutral book as degenerate.

## Category

`risk-management`
