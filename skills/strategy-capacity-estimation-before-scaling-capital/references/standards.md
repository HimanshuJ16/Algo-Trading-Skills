# Standards for Strategy Capacity Estimation Before Scaling Capital

## Engine parameters

| Metric | Engineering Standard |
|---|---|
| Impact Formula | $I(Q) = Y \cdot \sigma_{\text{daily}} \cdot \sqrt{Q / V}$ — the empirical **square-root law**, with $Q$ the daily one-way notional, $V$ the average daily volume in the same currency, and $Y$ a dimensionless prefactor. This is **not** the Almgren-Chriss model; see below. |
| Impact Prefactor $Y$ | `impact_gamma`, default $0.5$. Empirical fits for stocks and futures fall roughly in $0.5$–$1.0$, so the default is the **optimistic end** and must be calibrated to realized slippage. Drag is linear in $Y$; capacity is not. Where the Sharpe gate binds, capacity $\propto Y^{-2}$, so doubling $Y$ cuts it roughly fourfold. Where the participation cap binds, capacity is independent of $Y$. The value used is echoed on the report. |
| Spread Cost | $Q \times \text{half\_spread\_bps} / 10^4$, charged **once** on one-way notional. Pair a half-spread with one-way turnover; pairing it with two-way turnover doubles the cost. |
| Annualisation | $252$ trading days (`TRADING_DAYS_PER_YEAR`). An equity/futures convention — crypto and FX venues do not follow it. |
| Sharpe Definition | $(R - r_f)/\sigma$, an **excess** return ratio (Sharpe 1994). `risk_free_rate_pct` defaults to $0.0$, which is only correct if the inputs are already excess returns. |
| Sharpe Denominator | **Gross** strategy volatility. Costs enter as a deterministic drag, so realized impact variance is ignored and net Sharpe is biased **upward**. |
| Max ADV Participation | Default $\le 5.0\%$ of ADV, **inclusive**. A practitioner risk convention, **not** a regulatory limit — see the regulatory section. |
| Min Acceptable Sharpe | Default $\ge 1.0$ net Sharpe. An allocation-policy threshold chosen by the caller, not a standard. |
| Capacity Definition | The largest grid AUM with an **unbroken feasible run beneath it**. True capacity lies within `capacity_resolution_usd` above the reported figure; $0.0$ means "below one grid step". |
| Limiting Factors | `ADV_PARTICIPATION_LIMIT`, `MIN_SHARPE_BREACH`, `BELOW_MIN_SHARPE_AT_ALL_SIZES`, `SEARCH_RANGE_EXHAUSTED`. Exported as `LIMITING_FACTORS`. There is no `UNLIMITED` value; a censored search reports `SEARCH_RANGE_EXHAUSTED`. |

## Market impact functional form and its attribution

The square-root impact law is an **empirical regularity**, not a derived result, and it is
routinely mis-credited.

- **Almgren and Chriss (2000), "Optimal Execution of Portfolio Transactions"**, solve the
  optimal-liquidation problem under **linear** temporary and permanent impact functions of
  the trading rate, with prices following an arithmetic random walk. They do not propose a
  square-root impact law. Earlier versions of this skill labelled the formula below
  "Almgren-Chriss"; that attribution was wrong and has been corrected.
  <https://www.smallake.kr/wp-content/uploads/2016/03/optliq.pdf>
- **The square-root law** $I(Q) = Y\,\sigma\,\sqrt{Q/V}$ is credited to Torre/BARRA (1997)
  and Grinold and Kahn (1999), and has been confirmed independently many times since —
  Almgren et al. (2005), Moro et al. (2009), Tóth et al. (2011), Bershova and Rakhlin
  (2013), Kyle and Obizhaeva (2016). It is strikingly universal across stocks, futures,
  options, and geographies. See Tóth et al., "The square-root impact law also holds for
  option markets": <https://arxiv.org/abs/1602.03043>
- **The prefactor** $Y$ is "of order unity", with reported values for stocks and futures in
  the range $0.5$–$1.0$ (same source). There is no transferable universal constant.
- **The exponent** is not settled at exactly $0.5$. Almgren et al. (2005) and Kyle and
  Obizhaeva (2016) fit values nearer $0.6$. This engine hard-codes $0.5$.
- **Impact is roughly independent of execution schedule.** Empirically it depends on total
  metaorder size, not on how many child orders it is split into or how long execution takes.
  That is why this engine can price a day's turnover without modelling a schedule.
- **Charging $I(Q)$ on the full notional is conservative.** $I(Q)$ is the *terminal* price
  displacement; the average price paid across a metaorder is strictly below it, and impact
  subsequently relaxes to roughly two-thirds of peak (the fair-pricing condition; Farmer,
  Gerig, Lillo and Waelbroeck 2013). This over-charge partially offsets the optimism of a
  low $Y$, but the two errors are not the same size and neither is calibrated.

## Sharpe ratio definition

Sharpe (1994), "The Sharpe Ratio", *Journal of Portfolio Management* 21(1), 49–58, defines
the ratio as the expected **differential** return over a benchmark divided by the standard
deviation of that differential. Dividing a *total* return by volatility overstates the ratio
by $r_f/\sigma$ — at a 4% rate against 15% volatility, $+0.27$. Because the capacity gate
here is a Sharpe threshold, that error translates directly into over-allocation.

## Regulatory touchpoints — read the jurisdiction

There is **no general regulatory limit on trading a percentage of ADV**. The $5\%$ default in
this engine is a risk-management convention. Two real, correctly-scoped anchors:

| Rule | Jurisdiction & scope | What it actually says |
|---|---|---|
| SEC Rule 10b-18 (17 CFR 240.10b-18), volume condition | US; **issuer repurchases of their own equity only** | A **non-exclusive safe harbour**, not a prohibition. Daily Rule 10b-18 purchases must not exceed **25% of the security's four-week ADTV** to qualify, with an alternative of one block purchase per week in lieu of that day's limit. Does not apply to third-party trading, and non-compliance forfeits the safe harbour rather than creating a violation per se. <https://www.law.cornell.edu/cfr/text/17/240.10b-18> |
| SEC Rule 22e-4 (17 CFR 270.22e-4) | US; **registered open-end funds** (mutual funds, most ETFs) — not hedge funds, prop firms, or private funds | Requires a liquidity risk management programme classifying each portfolio investment by the days needed to convert it to cash **without significantly changing its market value**, assessed at the fund's **"reasonably anticipated trade size"**. SEC staff guidance states a zero or near-zero anticipated trade size is not a reasonable assumption. This is the closest regulatory analogue to what this engine computes. <https://www.sec.gov/rules-regulations/staff-guidance/division-investment-management-frequently-asked-questions/investment-company-liquidity-risk-management-programs-frequently-asked-questions> |

Do not universalise either rule. If you operate outside the US, or outside the entity types
named above, neither applies and the applicable regime must be checked separately.
