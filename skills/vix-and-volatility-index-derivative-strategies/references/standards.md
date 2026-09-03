# VIX Derivative Contract Specifications and Formulas

Contract specifications below are from the Cboe product pages cited in §4 and were
verified 2026-09-02. Specifications change; re-verify against the exchange before
relying on any figure here for sizing or settlement.

Everything in §2 is **house policy**, not an exchange or regulatory rule. It is
recorded here so that a configured threshold can be traced to a decision rather
than mistaken for a standard.

---

## 1. Contract specifications — the two multipliers are different

| | **VIX futures (VX)** | **VIX options** |
| :--- | :--- | :--- |
| Contract multiplier | **$1,000** per index point | **$100** per index point |
| Underlying / forward | VIX Index | The VX future of matching settlement |
| Exercise style | n/a | European |
| Settlement | Cash, to the SOQ | Cash, to the SOQ |
| Settlement amount | — | (settlement value − strike) × $100 |
| Minimum price interval | 0.05 index points = $50.00 per contract (single contracts) | See Cboe premium quotation |
| Position limits | Position accountability | No position or exercise limits |

**This is the highest-value line in this file.** Using $1,000 on an options leg
overstates every premium, payoff and budget consumption by exactly 10x. The module
keeps `VIX_FUTURES_MULTIPLIER = 1000.0` and `VIX_OPTIONS_MULTIPLIER = 100.0` as
separate named constants so the two cannot be transposed silently.

### Settlement date and value

Monthly VX futures settle on the **Wednesday 30 days prior to the third Friday of
the calendar month immediately following** the contract month. If that Wednesday,
or the Friday 30 days after it, is a Cboe Options holiday, settlement moves to the
immediately preceding business day.

The final settlement value is a **Special Opening Quotation (SOQ)** of the VIX
Index, calculated from the sequence of opening trade prices of the constituent SPX
options in a special opening auction. VIX options settle to the same SOQ, on the
same date, and the exercise-settlement value is rounded to the nearest $0.01.

Cboe also lists **VIX Weeklys futures** (since 2015), generally listed on Thursdays
and expiring on Wednesdays, with up to six consecutive weekly expirations and
otherwise the same specifications as the monthlies. Where weeklys are listed,
"front month" and "front contract" are different selections.

---

## 2. Curve state classification — configurable house thresholds

The module classifies on the front-two slope `(F2 − F1) / F1 × 100`:

| State | Default condition | Typical environment | Strategy branch |
| :--- | :--- | :--- | :--- |
| **CONTANGO** | slope % ≥ **+2.0%** | Calm / risk-on | Short F1, sized on notional budget |
| **BACKWARDATION** | slope % ≤ **−2.0%** | Stress / spike | Long OTM call spread, sized on premium budget |
| **FLAT** | strictly between | Transition | Cash |

The ±2.0% dead band is a **configurable default with no external authority behind
it**, not an industry standard. It is passed to `VIXStrategyEngine(...)` and should
be calibrated to the instrument, the holding period and the cost of switching
states. The constructor rejects an overlapping pair of thresholds.

The slope is rounded to 6 decimal places before comparison. An exactly-on-threshold
slope is otherwise non-deterministic: F1=20.00 with F2=20.40 is 2% on paper and
1.999999999999993 in binary floating point. At the 0.05-point minimum tick, one tick
is a 0.25% slope increment at F1=20, so rounding at 1e-6% cannot merge two
genuinely distinct quoted slopes.

---

## 3. Formulas

### A. Front-month basis, annualized

$$\text{Annualized basis \%} = \left( \frac{F_1 - S_{\text{VIX}}}{S_{\text{VIX}}} \right) \times \frac{365}{D_{\text{expiry}}} \times 100$$

The return a short-$F_1$ position earns **if spot VIX is unchanged on the
settlement date**, since $F_1$ converges to the SOQ. It is a carry estimate under a
static-spot assumption, not an expected return: spot VIX is mean-reverting and
stochastic, and the assumption fails hardest in exactly the regime that matters.

It is also **not** the $F_1 \rightarrow F_2$ curve roll harvested by
constant-maturity ETPs. The two share a sign on a normal curve and differ in
magnitude. Slope and basis can disagree in sign outright — a backwardated curve
with $F_1 < S$ is BACKWARDATION with a *negative* basis.

### B. Daily dollar decay of a short futures position

$$D_{\text{usd}} = \frac{F_1 - S_{\text{VIX}}}{D_{\text{expiry}}} \times N_{\text{contracts}} \times \$1{,}000$$

Straight-line convergence of the basis. Actual convergence is not linear and is
dominated by moves in spot.

### C. Long 1x1 vertical call spread, $K_2 > K_1$, at the **$100** options multiplier

$$\text{Max profit per contract} = (K_2 - K_1 - \text{net debit}) \times \$100$$

$$\text{Max loss per contract} = \text{net debit} \times \$100$$

$$\text{Breakeven SOQ} = K_1 + \text{net debit}$$

The debit is already spent and cannot also be won: reporting the gross width
$(K_2-K_1) \times \$100$ as max profit inflates the payoff by the premium and makes
every spread look identically attractive.

No-arbitrage bound, enforced by the pricer:
$0 < \text{net debit} < (K_2 - K_1) e^{-rT}$.

### D. Black (1976) call on the futures underlying

$$C = e^{-rT}\left[ F\,N(d_1) - K\,N(d_2) \right], \quad
d_1 = \frac{\ln(F/K) + \tfrac{1}{2}\sigma^2 T}{\sigma\sqrt{T}}, \quad
d_2 = d_1 - \sigma\sqrt{T}$$

$F$ is the **VX future of matching settlement**, never spot VIX, because the option
and the future settle to the same SOQ. $\sigma$ must be the implied volatility of
*that strike*: VIX option smiles slope upward in strike (a call skew, the mirror
image of the equity index put skew), so an ATM quote misprices a far OTM call.

Lognormality of $F$ is the market's quoting convention for VIX option implied
volatilities, not a property of the VIX index, which is mean-reverting and
positively skewed. Treat Black-76 here as an interpolator for quoted vols.

$T$ uses a 365 calendar-day year, matching how VIX DTE and the index's own 30-day
horizon are quoted.

---

## 4. Sources

- Cboe, *VIX Futures Contract Specifications* — contract multiplier 1000, trading
  hours: https://www.cboe.com/tradable_products/vix/vix_futures/specifications/
- Cboe, *VIX Options Contract Specifications* — contract multiplier 100, European
  exercise, cash settlement, settlement amount × $100, no position or exercise
  limits: https://www.cboe.com/tradable_products/vix/vix_options/specifications/
- Cboe, *VIX Futures* product page — VIX Weeklys futures listed since 2015,
  Thursday listing / Wednesday expiry, up to six consecutive weeklys:
  https://www.cboe.com/tradable-products/vix/vix-futures
- Credit Suisse AG, Form 6-K, 2018-02-06 — XIV acceleration event: intraday
  indicative value on 2018-02-05 at or below 20% of the 2018-02-02 closing
  indicative value of $108.3681; acceleration date 2018-02-21:
  https://www.sec.gov/Archives/edgar/data/1053092/000095010318001572/dp86358_ex9901.htm
- ProShares, 2018 leverage change — UVXY 2x → 1.5x and SVXY −1x → −0.5x, effective
  2018-02-28 (ProShares Trust II Form 8-K, FY2018):
  https://www.sec.gov/Archives/edgar/data/0001415311/000119312518059052/d503117dex991.htm
- Black, F. (1976), "The pricing of commodity contracts", *Journal of Financial
  Economics* 3(1–2), 167–179 — the futures-underlying option formula in §3D.
