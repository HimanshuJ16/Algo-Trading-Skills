# Standards for Quanto Options and Cross-Currency Derivative Structures

## Regulatory status

There is **no** regulator, exchange, or standards body that prescribes a quanto
pricing model. Everything below is derivatives-pricing mathematics; it holds or
fails independently of jurisdiction. Nothing in this skill should be cited as a
compliance requirement.

## Conventions this engine fixes

Both are choices about *notation*, and both change the sign of a result if
reversed. They must be stated, not assumed.

| Convention | Definition | Consequence if reversed |
|---|---|---|
| Exchange rate direction | $X_t$ = cost in **domestic** currency of one unit of the **foreign** currency ("domestic per foreign"). The domestic/foreign labels are a property of the quote, not of anyone's location. | $\rho$ flips sign; the drift moves by $2\rho\sigma_S\sigma_X$. |
| Strike currency | $K$ is in the **foreign** asset's own units, the same units as $S$; the payoff $\max(S_T-K,0)$ is then scaled by $F_X$. | A domestic-currency strike misprices by the FX rate, undetectably. |
| Rate roles | $r_d$ discounts the domestic payoff and appears nowhere else. $r_f$ drifts the foreign asset and appears only inside $\mu_{\text{quanto}}$ and hence $d_1$. | Price stays positive and near the money; no downstream check fires. |

## Model definitions

| Quantity | Definition | Source |
|---|---|---|
| Domestic risk-neutral dynamics of the foreign asset | $dS_t = (r_f - q - \rho\sigma_X\sigma)S_t\,dt + \sigma S_t\,dW_t$ | Haugh, *Foreign Exchange, ADRs and Quanto-Securities*, IEOR E4707 (Columbia University, Fall 2013), Section 4, equation (17). Drawn there from Back, *A Course in Derivative Securities*. |
| Quanto drift | $\mu_{\text{quanto}} = r_f - q - \rho\,\sigma_S\,\sigma_X$ | As above. Equivalently the quanto correction factor $\exp(-\gamma\,\sigma_S\,\sigma_\chi\,t)$ applied to the forward, with $\chi$ the domestic-per-foreign rate. |
| Quanto forward | $F^q_t = \bar{X} S_t e^{(r_f - q - \rho\sigma_x\sigma)(T-t)}$ | Haugh, equation (18). |
| Equivalent Merton formulation | A quanto option equals $\bar{X}$ times a Black-Scholes-Merton option on $S$ with strike $K/\bar{X}$, volatility $\sigma$, domestic rate $r_d$, and dividend yield $q_f := q + r_d - r_f + \rho\sigma_x\sigma$. Used as the independent cross-check in this skill's tests. | Haugh, equation (21) and the surrounding paragraph. |
| Underlying European formula | $C = Se^{-q\tau}N(d_1) - Ke^{-r\tau}N(d_2)$, $d_1 = [\ln(S/K) + (r - q + \sigma^2/2)\tau]/(\sigma\sqrt{\tau})$ | Merton, *Theory of Rational Option Pricing*, Bell Journal of Economics and Management Science 4(1), 141–183, 1973. |
| Contract taxonomy | A **quanto** is cash-settled in a currency other than the underlying's, converting at a **fixed** exchange rate. A **composite** ("compo") converts at the **prevailing spot** rate with the strike fixed in domestic currency. Different payoffs, different FX risk. | *Pricing Quanto and Composite Contracts with Local-Correlation Models*, arXiv:2501.07200 (2025), Section 1 and equation (7). |

## Greeks

| Greek | Definition | Note |
|---|---|---|
| Delta | $\pm F_X e^{-r_d T} e^{\mu T} N(\pm d_1)$ | The $n(d_1)$ terms cancel via $F\,n(d_1) = K\,n(d_2)$, exactly as in Black-Scholes. |
| Gamma | $F_X e^{-r_d T} e^{\mu T} n(d_1) / (S\sigma_S\sqrt{T})$ | Identical for call and put. |
| Drift sensitivity | $\partial V/\partial\mu = \pm F_X e^{-r_d T} S e^{\mu T} T\, N(\pm d_1)$ | Positive for a call, **negative** for a put. Every quanto-specific Greek below is signed by it. |
| Vega (total) | $F_X e^{-r_d T} S e^{\mu T} n(d_1)\sqrt{T} \;+\; (\partial V/\partial\mu)(-\rho\sigma_X)$ | $\sigma_S$ enters $d_1/d_2$ **and** $\mu$. The first term alone is the Black-Scholes vega and is *not* the quanto vega. Call and put totals differ. |
| Correlation sensitivity | $\partial V/\partial\rho = (\partial V/\partial\mu)\cdot(-\sigma_S\sigma_X)$ | **Negative for a call, positive for a put.** |

## Engine conventions (implementation choices, not standards)

| Convention | Value | Rationale |
|---|---|---|
| $\sigma_X = 0$ | Accepted | A hard-pegged FX rate is a legitimate quanto input; the adjustment simply vanishes. |
| $\rho = \pm 1$ | Accepted | Attainable correlation bound, not an error. |
| $T \le 0$ | Raises `ValueError` | An expired contract is a settlement question, not a pricing one. This engine deliberately does not return intrinsic. |
| Unknown `option_type` | Raises `ValueError` | Defaulting to a put silently misprices typos and produces an audit trail that does not record which side was priced. |
| Report rounding | None | Rounding gamma to 6 decimals destroys all but the first significant figure on an index-level underlying. Quantize at the presentation layer. |

## Scope limits

- European exercise, cash settlement, single flat $\sigma_S$ (no smile, no term
  structure), constant $\sigma_X$, constant $\rho$ to expiry.
- **Quanto only.** Composite/compo options, dual-currency notes, and any structure
  converting at a floating rate are out of scope and are *not* a parameterization
  of this model.
- $\rho$ is the single least stable input here and the model has no mechanism for
  its term structure or its behavior in stress. Use the reported
  $\partial V/\partial\rho$ to re-mark against a stressed correlation.
- Discrete cash dividends are not modelled; only a continuous yield $q$.

## Sources

- Haugh, M. *Foreign Exchange, ADRs and Quanto-Securities.* IEOR E4707: Financial
  Engineering: Continuous-Time Models, Columbia University, Fall 2013. Section 4
  ("Quanto-Securities"), equations (17)–(21).
  <http://www.columbia.edu/~mh2078/ContinuousFE/FX_Quanto.pdf>
- Merton, R. C. *Theory of Rational Option Pricing.* Bell Journal of Economics and
  Management Science 4(1), 141–183, 1973.
- *Pricing Quanto and Composite Contracts with Local-Correlation Models.*
  arXiv:2501.07200, 2025. <https://arxiv.org/abs/2501.07200>
