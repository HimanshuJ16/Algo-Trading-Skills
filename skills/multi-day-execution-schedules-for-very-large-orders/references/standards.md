# Standards — multi-day-execution-schedules-for-very-large-orders

## Engineering standards

| Metric | Engineering Standard |
|---|---|
| Cap enforcement | No session's target MAY exceed `max_daily_participation_pct × ADV`. The cap is a hard ceiling, not a target to be exceeded when a profile asks for more. |
| Allocation integrity | The daily targets MUST sum to the parent quantity. A schedule that allocates more or less silently over- or under-executes the parent. |
| Shape preservation | Clipping a slice at the cap MUST redistribute the excess without inverting the requested trajectory. Refilling in index order can turn a monotonically increasing profile non-monotonic; water-filling cannot. |
| Rounding reconciliation | Reporting-precision residue MUST be apportioned across sessions, not handed to one. On a capacity-saturated schedule no single session has the headroom to absorb it, and the sessions receiving a spare quantum MUST be chosen so the trajectory does not reverse. |
| Horizon feasibility | A requested horizon shorter than $\lceil Q / (p_{\max}\cdot\text{ADV}) \rceil$ MUST raise. Silently extending it would return a schedule the caller did not ask for. |
| Profile integrity | An unrecognised `schedule_profile` MUST raise. Falling back to a flat schedule returns a different trajectory than requested with no signal. |
| Unidentified terms | Permanent impact MUST be reported as `None` when shares outstanding is unavailable, never estimated from an assumed turnover. |
| Input integrity | Non-finite or non-positive quantities, prices and ADV, a participation cap outside $(0, 1]$, a negative volatility, and a fractional horizon MUST raise rather than produce a schedule. |
| Risk labelling | The overnight figure is a **one-standard-deviation** dispersion, not a worst case or a VaR number, and MUST be labelled as such wherever it is reported. |

## Market impact model — Almgren, Thum, Hauptmann & Li (2005)

Almgren, R., Thum, C., Hauptmann, E. & Li, H. (2005). "Direct Estimation of Equity Market
Impact." *Risk* 18(7), 57–62. Working paper:
https://www.cis.upenn.edu/~mkearns/finread/costestim.pdf

| Term | Form | Fitted value | Location |
|---|---|---|---|
| Permanent | $I = \gamma\,\sigma\,(X/V)\,(\Theta/V)^{1/4}$ | $\gamma = 0.314 \pm 0.041$, $\delta \approx 1/4$ | Sec. 4.2–4.3 |
| Temporary | $K = \eta\,\sigma\,\lvert X/(VT)\rvert^{\beta}$ | $\eta = 0.142 \pm 0.0062$, $\beta = 0.600 \pm 0.038$ | Sec. 4.2–4.3 |
| Realized cost | $J = I/2 + K$ | — | Sec. 4.3 |

$\sigma$ is daily volatility as a fraction of price, $X$ order shares, $V$ average daily
volume, $\Theta$ shares outstanding, and $X/(VT)$ the participation rate. Here $T = 1$
session per slice, so each session's participation rate is $q_d / V$.

- ATHL **reject the square-root exponent** $\beta = 1/2$ at the 95% confidence level in
  favour of $3/5$ (ibid. Sec. 4.2). An implementation using $\sqrt{\cdot}$ contradicts the
  source it usually cites.
- Impact scales with $\sigma$. A coefficient applied to participation alone is not an
  impact model — it returns the same cost for a utility and a biotech.
- The regressions' $R^2$ is under one percent (ibid. Sec. 4.3). The model predicts the
  *expectation* of cost; realized cost on any individual order varies enormously around it.
- The fit is on Citigroup US **large-cap** desk flow, 2001–2003. Applicability to other
  markets, capitalisations, or eras is unverified and MUST be established by recalibration,
  not assumed.

## Overnight risk — Almgren & Chriss (2000)

Almgren, R. & Chriss, N. (2000). "Optimal Execution of Portfolio Transactions."
*Journal of Risk* 3(2), 5–39. https://www.smallake.kr/wp-content/uploads/2016/03/optliq.pdf

| Quantity | Definition | Location |
|---|---|---|
| Shortfall variance | $V(x) = \sigma^2\sum_{k=1}^{N}\tau\,x_k^2$, $x_k$ = shares still held after interval $k$ | Eq. (5) |
| Linear permanent impact | $g(v) = \gamma v$ | Eq. (6) |
| Linear temporary impact | $h(n_k/\tau) = \epsilon\,\mathrm{sgn}(n_k) + (\eta/\tau)n_k$ | Eq. (7) |
| Expected cost | $E(x) = \tfrac{1}{2}\gamma X^2 + \epsilon\sum\lvert n_k\rvert + \tfrac{\tilde\eta}{\tau}\sum n_k^2$, $\tilde\eta = \eta - \tfrac{1}{2}\gamma\tau$ | Eq. (8) |

Two consequences drive this skill's arithmetic:

1. **Permanent impact cost is $\tfrac{1}{2}\gamma X^2$ — a function of total size only.**
   It does not fall as the horizon lengthens. Only the temporary term responds to the
   trajectory. A model whose permanent-impact estimate shrinks with the horizon is not
   AC-2000 whatever it is labelled.
2. **Timing risk is quadratic in remaining inventory.** With $\tau = 1$ session and $\sigma$
   in price units ($\sigma_{\text{pct}} \times P$), the one-standard-deviation dispersion is
   $\sigma_{\text{pct}} P \sqrt{\sum_d x_d^2}$. This assumes independent daily returns,
   zero drift, and a constant reference price — an arithmetic random walk (ibid. Eq. 1).

The trajectory profiles here are **heuristics**, not the AC optimum. The closed-form
optimal trajectory $x_j = X\sinh(\kappa(T - t_j))/\sinh(\kappa T)$ (ibid. Eq. 17) is
implemented in `implementation-shortfall-minimization`.

## Regulatory participation limits

**There is no general regulatory cap on what fraction of ADV an ordinary institutional
order may take.** The 10–15% figures common in execution practice are house risk limits and
broker algo defaults, not rules; this library's 10% default is a starting point to be
calibrated, not a compliance threshold. Where hard ADV caps *do* exist they attach to
specific programme types:

| Jurisdiction | Instrument / programme | Limit | Source |
|---|---|---|---|
| US | Issuer repurchases seeking the Rule 10b-18 safe harbour | Purchases on any single day must not exceed **25% of ADTV**, where ADTV is measured over the four calendar weeks preceding the week of purchase. One weekly block purchase may fall outside it if no other Rule 10b-18 purchases occur that day. | 17 CFR § 240.10b-18(a)(1), (b)(4) ([Cornell LII](https://www.law.cornell.edu/cfr/text/17/240.10b-18)) |
| US | Affiliate / restricted-securities resales | In any three months, the greater of **1% of the class outstanding** or the **average weekly reported volume over the preceding four calendar weeks**. | 17 CFR § 230.144(e)(1)(i)–(ii) ([Cornell LII](https://www.law.cornell.edu/cfr/text/17/230.144)) |
| EU | Buy-back programmes relying on the MAR Art. 5 exemption | No more than **25% of the average daily volume** on the venue where the purchase is carried out, calculated over the month preceding disclosure (fixed for the programme) or the **20 trading days** preceding the purchase. | Commission Delegated Regulation (EU) 2016/1052, Art. 3(3)(a)–(b) ([EUR-Lex](https://eur-lex.europa.eu/legal-content/EN/TXT/?uri=CELEX:32016R1052)) |

Notes on currency and scope:

- The Rule 10b-18 safe harbour is **voluntary**. Failing its conditions forfeits the
  harbour, and § 240.10b-18(d) provides that no *presumption* of a s. 9(a)(2) or s. 10(b)
  violation arises from that failure — which is not the same as the conduct being lawful.
  Exceeding 25% of ADTV moves the repurchase back into an ordinary facts-and-circumstances
  manipulation analysis; it does not license it.
- ESMA's report on amendments to Delegated Regulation 2016/1052
  (ESMA74-268544963-1569, 27 February 2026) revises the Art. 2 reporting and disclosure
  requirements; it does not alter the Art. 3 trading conditions.
- Neither cap applies to an ordinary buy-side parent order in a third-party name. Do not
  transplant 25% into an unrelated order as though it were a permission.
- This engine enforces one number. Where a programme is subject to a statutory cap, that
  cap MUST be encoded as `max_daily_participation_pct` and the ADV window MUST match the
  one the rule specifies — a 20-session ADV is not a four-calendar-week ADTV.

## Documented limitations

- **ADV is treated as a constant.** It is a single scalar applied to every session. Real
  volume moves with index rebalances, expiries, earnings and holidays, and a stale or
  wrongly-windowed ADV mis-scales every slice and every cap in the same direction.
- **The horizon is in trading sessions, not calendar days.** The engine has no exchange
  calendar; mapping session indices onto dates, and skipping holidays and half-days, is the
  caller's job (`global-exchange-holiday-calendar-handling`).
- **Quantities are not lot-rounded.** Slices are reported on a 0.01-share grid, which is not
  a tradable size in most markets, and the effective per-session ceiling is the cap rounded
  down onto that grid. Apply lot rounding downstream
  (`minimum-fill-size-and-lot-rounding-logic`) and re-check the cap and the parent total
  afterwards — lot rounding reintroduces exactly the residual this engine reconciles away.
- **The schedule is open-loop.** It is planned once from the inputs given. It does not
  observe fills, does not re-plan on a shortfall, and does not react to a halt, a volume
  collapse, or a volatility regime shift.
- **Impact and risk are not combined into a single objective.** The engine reports both and
  leaves the horizon choice to the caller; it does not select $\lambda$ on the AC efficient
  frontier.

## Category

`execution-algorithms`
