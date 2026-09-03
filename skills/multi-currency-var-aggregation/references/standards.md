# Standards — multi-currency-var-aggregation

## Engineering standards enforced by this skill

| Area | Standard |
|---|---|
| Return synthesis | Asset and FX returns MUST be compounded: $R_{\text{base}} = (1+R_{\text{native}})(1+R_{\text{FX}}) - 1$. Implemented as $r_n + r_{fx} + r_n r_{fx}$ — algebraically identical, but it avoids adding and subtracting 1.0 and so keeps the significant digits of small returns. |
| FX quoting direction | `fx_rate_to_base` and `fx_returns_to_base[c]` MUST both be base-currency-per-native-currency. The engine can only enforce the one case it can see: the base currency's own FX return must be identically zero and its `fx_rate_to_base` exactly 1.0. |
| Missing FX data | A missing FX return series for a non-base currency MUST raise. Substituting zeros deletes the currency risk being measured and understates VaR silently. |
| Data hygiene | Non-finite (`NaN`/`inf`) returns, prices, quantities and FX rates MUST be rejected before aggregation; misaligned series lengths MUST raise rather than being truncated by `zip`. |
| Position identity | Series MUST be indexed by position, not by symbol, so multiple lots of one instrument are not collapsed into one. |
| Aggregation basis | Portfolio P&L MUST be aggregated on position **values** ($\sum_i V_i R_i$), not on weights, so a currency-hedged or market-neutral book with near-zero net value stays measurable. |
| Confidence levels | Any level in $(0.5, 1)$ MUST be supported, via `statistics.NormalDist.inv_cdf` rather than a lookup table. Reference quantiles: $Z_{0.90}=1.2816$, $Z_{0.95}=1.6449$, $Z_{0.975}=1.9600$, $Z_{0.99}=2.3263$. |
| Historical quantile | With losses sorted worst-first and $k = \lceil n(1-\alpha) \rceil$, VaR is the $k$-th worst loss. The ceiling MUST carry an epsilon: `ceil(100 * (1 - 0.95))` evaluates to 6 in binary floating point. |
| Tail risk metric | Expected Shortfall (CVaR) — the mean of the same $k$ worst losses — MUST be reported alongside VaR, so $\text{ES} \ge \text{VaR}$ holds by construction. |
| Minimum sample | The sample MUST contain at least $\lceil 1/(1-\alpha) \rceil$ observations (20 at 95%, 100 at 99%) so the tail bucket holds at least one, and never fewer than 2 (the $(n-1)$ sample variance is undefined at $n=1$). |
| Risk decomposition | Per-currency contribution MUST be the Euler (component VaR) decomposition, which sums to the parametric VaR with no residual — never the net market value per currency, which is exposure, not risk. |

## Regulatory touchpoints (verified against primary sources)

Source: BCBS, **Minimum capital requirements for market risk** (January 2019, revised
February 2019), [bis.org/bcbs/publ/d457.pdf](https://www.bis.org/bcbs/publ/d457.pdf).

| Requirement | Location |
|---|---|
| "In calculating ES, a bank must use a 97.5th percentile, one-tailed confidence level." | MAR33.3 |
| The base-horizon ES "must be calculated for changes in the risk factors ... over the time interval T **without scaling from a shorter horizon**"; base horizon $T$ = 10 days | MAR33.4(5), MAR33.4(2) |
| FX is a distinct risk-factor category with its own liquidity horizons: 10 days for the Committee's specified currency pairs, 20 days for other pairs, 40 days for FX volatility, 40 days for FX "other types" | MAR33.12, Table 2 |
| Desk-level backtesting compares each desk's one-day VaR at **both** the 97.5th and the 99th percentile, using at least one year of current observations | MAR32.18 |
| Current-observation data sets must be updated at least quarterly, and reassessed whenever market prices change materially | MAR33.8 |

Source: **12 CFR 217 Subpart F** (US market risk rule, Board of Governors of the Federal
Reserve System), [§ 217.205](https://www.law.cornell.edu/cfr/text/12/217.205).

| Requirement | Location |
|---|---|
| "The VaR-based measure must be calculated on a daily basis using a one-tail, 99.0 percent confidence level, and a holding period equivalent to a 10-business-day movement in underlying risk factors." | § 217.205(b)(1) |
| An institution "may calculate 10-business-day measures directly or may convert VaR-based measures using holding periods other than 10 business days to the equivalent of a 10-business-day holding period" | § 217.205(b)(1) |
| "The VaR-based measure must be based on a historical observation period of at least one year." | § 217.205(b)(2) |

**The two frameworks disagree on horizon scaling, and that disagreement matters here.**
Setting `holding_period_days > 1` applies $\sqrt{T}$, which § 217.205(b)(1) permits as a
conversion but MAR33.4(5) forbids for the FRTB base-horizon ES. The engine logs a warning
and sets `holding_period_scaled` on the report so the distinction is auditable. Nothing in
this module is a regulatory capital calculation; verify against the rule that binds you.

## Known limitations

- **Linear / delta-normal only.** Position value is assumed proportional to price, and
  the historical branch revalues linearly rather than repricing. Options and other convex
  payoffs are mis-measured by both branches.
- **Frequency is the caller's.** VaR emerges at the frequency of the supplied returns;
  daily returns give a 1-day VaR. There is no annualisation.
- **$\sqrt{T}$ scaling assumes i.i.d. returns.** Volatility clustering and
  autocorrelation break it in both directions.
- **Parametric VaR assumes elliptical returns.** FX returns are heavy-tailed
  ($\kappa > 3$), so the parametric 99% figure typically sits below the historical one —
  treat a large gap as a tail-shape signal, not noise.
- **No look-ahead protection.** Series must end at the last completed period; the module
  cannot verify this.
- **Component VaR decomposes the parametric VaR only** — not the historical VaR, not
  the ES — and inherits its distributional assumption.
