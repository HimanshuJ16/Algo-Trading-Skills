# Standards — options-greeks-real-time-portfolio-aggregation

## Contract scaling facts (verified against primary sources)

| Fact | Source |
|---|---|
| A standard US equity/ETP option contract is "Generally, 100 shares of one of the exchange-traded products" | Cboe, [*Equity Options Product Specifications*](https://www.cboe.com/exchange-traded-stock/equity-options-spec/) — Contract Size |
| After a corporate action the **premium multiplier stays 100** while the **deliverable changes**: a 1-for-20 reverse split "causes the option contract to be adjusted by changing the deliverable to 5 shares of the new stock. You can expect the contract multiplier to remain 100" | OIC, [*Splits, Mergers, Spinoffs & Bankruptcies*](https://www.optionseducation.org/referencelibrary/faq/splits-mergers-spinoffs-bankruptcies) |
| Adjusted contract terms are published per event; the deliverable must be read from the memo, not assumed | [OCC Information Memos](https://infomemo.theocc.com/) |
| Vega is quoted per **1 percentage point** of implied volatility: "Vega measures the amount of increase or decrease in premium based on a 1% (100 basis points) change in the implied volatility assumption" | OIC, [*Vega*](https://www.optionseducation.org/advancedconcepts/vega) |
| Theta is per **one calendar day**: "how much an option's premium may decay per day", and "Pricing models take into account weekends, so options will tend to decay seven days over the course of five trading days" | OIC, [*Theta*](https://www.optionseducation.org/advancedconcepts/theta) |

**The multiplier is therefore an input, not a constant.** Greeks scale with the
deliverable, so an OCC-adjusted contract delivering 5 shares scaled by 100 overstates
its risk exactly 20×. Non-US and crypto products differ outright — a Deribit BTC
option is 1 BTC per contract.

## Engineering standard

| Rule | Requirement |
|---|---|
| Multiplier | MUST be supplied per position from the contract master. MUST NOT default to 100. MUST be `> 0`. |
| Greeks input | MUST be per unit of the deliverable, with the long/short sign carried by the quantity. |
| Delta sanity | $\|\delta\| \le 1$ per unit. A larger value means a percent-quoted feed and MUST be rejected, not scaled. |
| Non-finite input | A NaN/Inf Greek, quantity, spot or multiplier MUST raise. It MUST NOT be netted: `abs(nan) > limit` is `False`, so a NaN book otherwise reports as compliant. |
| Cross-asset delta | Cross-underlying comparison MUST use Dollar Delta, $\Delta_{\text{USD}} = Q M \delta S$. Raw delta units are per-underlying only. |
| Cross-asset gamma | Cross-underlying comparison MUST use Dollar Gamma, $\Gamma_{\text{USD}} = Q M \gamma S^2 \times 0.01$ — the dollar delta gained on a $+1\%$ move. Raw gamma is per-underlying only. |
| Limit evaluation | Every limit MUST be evaluated independently and every breach reported. A first-match-wins status hides concurrent breaches. |
| Limit comparison | Limits MUST be compared against the same rounded values that are reported, so a status can never contradict the figure beside it. |
| Aggregation | Totals MUST be order-independent (`math.fsum`), so a near-limit book's breach status does not depend on position ordering. |

## Limit sign conventions

These are not interchangeable, and getting one wrong silently disables the control:

| Limit | Kind | Valid range | Breach test |
|---|---|---|---|
| `max_dollar_delta_usd` | magnitude | $> 0$ | $\|\Delta_{\text{USD,net}}\| > L$ |
| `max_negative_theta_usd` | signed floor on daily decay | $\le 0$ | $\Theta_{\text{net}} < L$ |
| `max_vega_usd` | magnitude | $> 0$ | $\|\nu_{\text{net}}\| > L$ |
| `max_abs_dollar_gamma_usd` | magnitude, optional | $> 0$ or `None` | $\|\Gamma_{\text{USD,net}}\| > L$ |

Only decay is capped by the theta limit: a short-premium book collecting theta is not
the risk it exists to catch. Testing $|\Theta| \le L$ against a negative $L$ is never
satisfiable and flags every portfolio.

The library's default values (\$500,000 dollar delta, −\$5,000/day theta, \$10,000
vega, dollar gamma unaudited) are illustrative starting points, **not** an industry
standard. No regulator or standards body publishes a mandatory portfolio Greeks limit;
calibrate each against the book's capital, mandate and drawdown tolerance, and record
the rationale.

## Known limitations

- **Single currency.** The engine multiplies numbers; it does not convert them. Every
  `_usd` field is in the currency the inputs were quoted in.
- **Net vega assumes a parallel vol shift** across every underlying in the book — one
  point on every surface, simultaneously. Real vol shocks are neither parallel nor
  equal across names.
- **First-order snapshot.** Greeks are inputs, taken as given. No revaluation, no
  staleness detection, and no second-order cross-Greeks (vanna, volga, charm).
- **Monitoring only.** No hedge is generated and nothing is halted on a breach.

## Category

`risk-management`
