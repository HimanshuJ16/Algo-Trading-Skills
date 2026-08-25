# Standards for Execution Algo Parameter Optimization

## Engineering standards

| Area | Standard |
|---|---|
| Objective metric | The score MUST be expected Implementation Shortfall plus a dispersion penalty plus an incomplete-fill penalty. Shortfall MUST be measured against the arrival (decision) price and MUST include the opportunity cost of unexecuted quantity (Perold 1988). |
| Historical sensitivity | The simulated shortfall MUST be a function of the sample's arrival price and observed price path. A shortfall computed from the parameter set alone is a cost model, not a backtest, and MUST NOT be described as one. |
| Fill completion | Fills MUST be capped by observed interval volume at the configured participation ceiling. Fill completion is an outcome of the replay, never an input assumption. |
| Statistical separation | A candidate ranking MUST be reported alongside the standard error of each candidate's mean shortfall. A winner whose margin falls inside the combined standard error MUST NOT be presented as the better configuration. |
| In-sample / out-of-sample split | Selection MUST be validated on a holdout set that took no part in the selection. A run without one MUST carry an explicit warning. |
| Determinism | Identical inputs MUST produce an identical selection. Ties MUST resolve deterministically (this implementation: earliest candidate in grid order). |
| Numerical integrity | Non-finite shortfalls and scores MUST raise rather than propagate — a `NaN` score sorts arbitrarily in Python and can silently be selected as the optimum. |

## Market impact model

Coefficients from Almgren, R., Thum, C., Hauptmann, E. & Li, H. (2005), "Direct
Estimation of Equity Market Impact", *Risk* 18(7), 57-62, Sec. 4.3:

| Term | Form | Fitted value |
|---|---|---|
| Permanent | $I = \gamma \sigma (X/V) (\Theta/V)^{1/4}$ | $\gamma = 0.314 \pm 0.041$, $\delta \approx 1/4$ |
| Temporary | $K = \eta \sigma \,\lvert X/(VT) \rvert^{\beta}$ | $\eta = 0.142 \pm 0.0062$, $\beta = 0.600 \pm 0.038$ |
| Realized cost | $J = I/2 + K$ | — |

$\sigma$ is daily volatility as a fraction, $X$ order shares, $V$ average daily
volume, $\Theta$ shares outstanding, and $X/(VT)$ the participation rate.

- ATHL **reject the square-root exponent** $\beta = 1/2$ at the 95% confidence level in favour of $3/5$ (ibid. Sec. 4.2). An implementation using $\sqrt{\cdot}$ contradicts the source it usually cites.
- The regressions' $R^2$ is under one percent (ibid. Sec. 4.3). The model predicts the expectation of cost; realized cost on any individual order varies enormously around it.
- The fit is on Citigroup US **large-cap** desk flow, 2001-2003. Applicability to other markets, capitalisations, or eras is unverified and MUST be established by recalibration, not assumed.

## Execution schedule

Almgren, R. & Chriss, N. (2000), "Optimal Execution of Portfolio Transactions",
*Journal of Risk* 3(2), 5-39:

| Element | Reference |
|---|---|
| Objective $E(x) + \lambda V(x)$ | Sec. 3 |
| Optimal trajectory $x_j = X \sinh(\kappa(T-t_j)) / \sinh(\kappa T)$ | Eq. (17) |
| $\tilde{\kappa}^2 = \lambda \sigma^2 / \tilde{\eta}$, $\;\kappa \sim \sqrt{\lambda\sigma^2/\eta}$ as $\tau \to 0$ | Eq. (16), (19) |
| Temporary impact $h(n_k/\tau) = \epsilon\,\mathrm{sgn}(n_k) + (\eta/\tau) n_k$, with $\epsilon$ "the fixed costs of selling, such as half the bid-ask spread plus fees" | Eq. (7) |

$\lambda$ selects a point on the efficient frontier; it is **not** an additive cost
term. This implementation maps $\lambda$ to $\kappa$ through Eq. (19) with $\eta$
linearised from the ATHL temporary-impact cost at the participation ceiling over a
one-day reference horizon. That linearisation is an approximation — ATHL's cost
function carries $\sigma$, so its units differ from AC's linear $\eta$ by a
$\sqrt{\text{day}}$ factor that is unity only at a one-day horizon. **$\lambda$ is
therefore meaningful as an ordering over the grid on this module's scale, not as a
transferable constant**, and its optimum must be recalibrated per desk.

## Participation ceiling — what is and is not a regulatory limit

The 25% default in this module is a **house risk limit**, not a general legal cap on
algorithmic trading. There is no universal regulatory participation ceiling; treat any
unqualified claim of one with suspicion.

The one place 25% of ADTV is genuinely a rule is **SEC Rule 10b-18** (17 CFR
240.10b-18), the safe harbour for **issuer repurchases of their own equity**. Its
volume condition, Rule 10b-18(b)(4), requires that an issuer's Rule 10b-18 purchases
on any single day not exceed 25% of the security's four-week ADTV, with a once-weekly
block exception. Key qualifications:

- It applies to **issuers buying their own stock**, not to agency or proprietary flow generally.
- It is a **condition of a safe harbour**, not a prohibition: exceeding it forfeits the safe harbour rather than being unlawful per se.
- It is a **US** rule. Other jurisdictions have their own buy-back regimes.

Set `max_allowed_participation_rate` from your own written policy, from venue rules,
and — where the flow actually is an issuer repurchase — from 10b-18. See
`algo-parameter-defaults-by-instrument-liquidity-tier`.

## Sources

- Almgren, R. & Chriss, N. (2000). "Optimal Execution of Portfolio Transactions", *Journal of Risk* 3(2), 5-39.
- Almgren, R., Thum, C., Hauptmann, E. & Li, H. (2005). "Direct Estimation of Equity Market Impact", *Risk* 18(7), 57-62.
- Perold, A. F. (1988). "The Implementation Shortfall: Paper Versus Reality", *Journal of Portfolio Management* 14(3), 4-9.
- 17 CFR 240.10b-18 — Purchases of certain equity securities by the issuer and others. <https://www.law.cornell.edu/cfr/text/17/240.10b-18>
- SEC, Division of Trading and Markets, "Answers to Frequently Asked Questions Concerning Rule 10b-18". <https://www.sec.gov/rules-regulations/staff-guidance/trading-markets-frequently-asked-questions/division-trading-markets-answers-frequently-asked-questions-concerning-rule-10b-18-safe-harbor>
