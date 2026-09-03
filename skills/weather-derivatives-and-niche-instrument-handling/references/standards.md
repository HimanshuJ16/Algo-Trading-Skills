# Weather Derivatives & Niche Instrument Standards

All payoffs below are stated in the **contract's own currency**, at the contract's own
multiplier. There is no universal "\$20 per index point" — that figure is specific to
CME's US degree-day contracts. Degree-day indexes are unitless point counts; a CAT
index is a sum of daily mean temperatures in degrees Celsius and may be negative.

---

## 1. CME weather contract specifications

| Contract family | Index | Base temperature | Multiplier | Settlement |
| :--- | :--- | :--- | :--- | :--- |
| CME US Degree Days Index futures & options | HDD, CDD | $65^\circ\text{F}$ | **USD 20** / index point | Cash |
| CME European Monthly/Seasonal Weather HDD | HDD | $18^\circ\text{C}$ | **EUR 20** / index point | Cash |
| CME European Monthly/Seasonal Weather CAT | CAT | none | **EUR 20** / index point | Cash |
| CME Pacific Rim CAT (Tokyo) | CAT | none | **JPY 2,500** / index point | Cash |
| OTC capped weather swap | negotiated | negotiated | negotiated | Cash, bilateral |

Sources: CME Rulebook Chapter 403 (*CME Degree Days Index Futures*) and Chapter 411
(*CME Pacific Rim CAT Index Futures*), <https://www.cmegroup.com/rulebook/CME/>; CME
weather product specifications and *Overview of Weather Markets*,
<https://www.cmegroup.com/markets/weather.html>.

**Product codes.** CME US weather futures use per-city code families — `H` + digit for
HDD and `K` + digit for CDD (Las Vegas HDD is `H0`, Las Vegas CDD is `K0`), with a
month letter appended for seasonal strips. Codes are assigned per city and change as
the listed city set changes, so resolve them from CME's current weather product slate
rather than from a hard-coded table.

**Listed geography.** CME has listed temperature contracts on US, European and
Japanese cities; the specific city list is revised periodically. Confirm that a city
and contract month are currently listed before assuming a hedge is executable.

---

## 2. Index definitions

The daily average temperature is the mean of the day's maximum and minimum on a
midnight-to-midnight basis:

$$T_{\text{mean},i} = \frac{T_{\max,i} + T_{\min,i}}{2}$$

Accumulated over the contract period $i = 1 \ldots n$:

$$I_{\text{HDD}} = \sum_{i=1}^{n} \max\left(0,\; T_{\text{base}} - T_{\text{mean},i}\right)
\qquad
I_{\text{CDD}} = \sum_{i=1}^{n} \max\left(0,\; T_{\text{mean},i} - T_{\text{base}}\right)$$

$$I_{\text{CAT}} = \sum_{i=1}^{n} T_{\text{mean},i}$$

$T_{\text{base}}$ is $65^\circ\text{F}$ for CME US contracts and $18^\circ\text{C}$ for
CME European HDD — the same physical threshold expressed in each contract's own unit.
CAT applies no base and, being a sum of Celsius means, may be negative.

**Settlement authority.** The official settlement index is calculated and reported by
**Speedwell Settlement Services Ltd** from National Weather Service (US) and Japan
Meteorological Agency (Japan) station observations. Open futures settle to that
reported index on the **second Exchange Business Day after the contract month**, under
the methodology in effect on that date. Speedwell's published methodology — not this
reference — governs rounding of the daily average and the treatment of missing station
observations. A locally recomputed index is an estimate for pricing and reconciliation
only.

---

## 3. Payoff equations

Let $I$ be the accumulated settlement index, $K$ the strike index, $M$ the multiplier
in the contract currency, and $Q$ the signed position size.

### A. Futures

Cash settlement value of the contract:

$$V_{\text{settlement}} = I \times M \times Q$$

Position profit and loss, measured from the entry index price $I_{\text{entry}}$:

$$\text{P\&L}_{\text{futures}} = \left(I - I_{\text{entry}}\right) \times M \times Q$$

These are different quantities. $V_{\text{settlement}}$ is what the contract is worth;
substituting it for P&L overstates the result by the entire entry notional.

### B. Options (intrinsic value at expiry, before premium)

$$\text{Payoff}_{\text{call}} = \max\left(0,\; I - K\right) \times M \times Q
\qquad
\text{Payoff}_{\text{put}} = \max\left(0,\; K - I\right) \times M \times Q$$

### C. Capped OTC weather swap

$$P_{\text{uncapped}} = \left(I - K\right) \times M \times Q$$

With a symmetric cap $C_{\text{cap}}$:

$$P_{\text{swap}} = \operatorname{sign}(P_{\text{uncapped}}) \times
\min\left(\left|P_{\text{uncapped}}\right|,\; C_{\text{cap}}\right)$$

With an asymmetric cap and floor — a gain cap $C_{\text{cap}}$ and a loss limit
$F_{\text{floor}}$, both non-negative magnitudes:

$$P_{\text{swap}} = \min\left(C_{\text{cap}},\; \max\left(-F_{\text{floor}},\; P_{\text{uncapped}}\right)\right)$$

A cap of zero is a genuine zero cap, distinct from an absent cap.

---

## 4. Burn analysis and detrending

Burn analysis values a contract as the mean payoff over $N$ historical seasons
(20–30 years is the market convention):

$$V_{\text{burn}} = D \cdot \frac{1}{N} \sum_{j=1}^{N} \text{Payoff}\left(I_j\right)$$

where $D$ is the discount factor from settlement to today. This is unbiased only if
each $I_j$ is a draw from the *contract season's* climate, which a raw historical
record is not.

**Linear trend correction.** Fit $I_j = a + b j$ by ordinary least squares over the
chronologically ordered record and re-centre each season on the fitted level of the
target season $t$:

$$\hat{I}_j = I_j + b\left(t - j\right)$$

Because $\hat{I}_j = (a + bt) + \varepsilon_j$, the adjusted series is exactly the OLS
residuals shifted onto $\hat{f}(t)$: the trend is removed while each season's departure
from the fitted climate is preserved. Standard practice per Jewson & Brix, *Weather
Derivative Valuation*, Cambridge University Press, 2005 (ISBN 978-0-521-84371-3),
Chapter 3 ("The valuation of single contracts using burn analysis") and the "Trend
models" appendix.

A fitted slope is not automatically climate. A station relocation or instrument change
produces the same linear signal and must be handled as a structural break in the
record, not smoothed as a trend.

**Limits of burn analysis.** It carries no distributional model: the payoff
distribution is the empirical one, so with 30 seasons a 5th percentile rests on one or
two observations. Use it for the expected payoff and for the realised range; use an
index model or a stochastic temperature model for genuine tail estimation.

---

## 5. Why Black-Scholes does not apply

Weather is neither storable nor tradable, so there is no replicating portfolio, no
cost-of-carry relation, and no way to infer a risk-neutral drift from a spot price —
the market is incomplete and the derivative cannot be delta-hedged in its underlying.
The index is also strongly seasonal and mean-reverting, which lognormal diffusion does
not describe. See `commodity-futures-storage-and-carry-cost-modeling` for the same
non-storability argument applied to physical commodities.

---

## 6. OTC documentation and credit

OTC weather swaps are documented under an ISDA Master Agreement with a credit support
annex. Because a weather swap has no market-observable underlying to liquidate against,
counterparty exposure is managed through the payout cap, the collateral threshold, and
periodic mark-to-market — see `counterparty-credit-risk-for-otc-derivatives`.
