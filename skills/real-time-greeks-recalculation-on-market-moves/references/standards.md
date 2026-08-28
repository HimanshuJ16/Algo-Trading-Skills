# Standards — real-time-greeks-recalculation-on-market-moves

## Verified facts (primary sources)

| Fact | Source |
|---|---|
| A delta sensitivity alone is not accepted as the risk measure for options. The curvature charge exists to "calculate the incremental loss for instruments sensitive to that risk factor above that already captured by the delta risk capital requirement", and it is computed by **repricing** each instrument under an upward and a downward shock — "the price of instrument $i$ after $k$ is shifted (ie 'shocked') upward and downward respectively" — not by extrapolating a sensitivity | BCBS, [*Calculation of RWA for market risk — MAR21*](https://www.bis.org/basel_framework/chapter/MAR/21.htm), MAR21.5 (also published as [*Minimum capital requirements for market risk*](https://www.bis.org/bcbs/publ/d457.pdf)) |
| "Instruments whose cash flows cannot be written as a linear function of underlying notional… all options are subject to vega risk and curvature risk" — i.e. the non-linearity is an explicit, separately capitalised risk, not a rounding error | BCBS, MAR21.2(3) |
| Curvature must be measured from **two** shocks, up and down: "an upward shock and a downward shock must be applied to $k$" | BCBS, MAR21.5(1) |
| Vega is quoted per **1 percentage point** of implied volatility: "Vega measures the amount of increase or decrease in premium based on a 1% (100 basis points) change in the implied volatility assumption" | OIC, [*Vega*](https://www.optionseducation.org/advancedconcepts/vega) |
| After a corporate action the **premium multiplier stays 100** while the **deliverable changes**: a 1-for-20 reverse split causes "the option contract to be adjusted by changing the deliverable to 5 shares of the new stock. You can expect the contract multiplier to remain 100" | OIC, [*Splits, Mergers, Spinoffs & Bankruptcies*](https://www.optionseducation.org/referencelibrary/faq/splits-mergers-spinoffs-bankruptcies) |
| Adjusted contract terms are published per event; the deliverable must be read from the memo, never assumed | [OCC Information Memos](https://infomemo.theocc.com/) |

**What the Basel citation does and does not establish.** It is evidence that a
recognised prudential standard treats delta-only measurement of options as
insufficient and mandates repricing under shocks. It is **not** a revaluation cadence,
a threshold, or a requirement that any firm implement this engine. MAR21 governs
regulatory capital for banks in scope; it says nothing about the refresh rate of an
intraday risk display.

## The pricing model

European Black-Scholes-Merton with a continuous dividend yield $q$ (Merton, 1973), per
unit of the deliverable:

$$d_1 = \frac{\ln(S/K) + (r - q + \tfrac{1}{2}\sigma^2)T}{\sigma\sqrt{T}}, \qquad d_2 = d_1 - \sigma\sqrt{T}$$

| Greek | Call | Put |
|---|---|---|
| Price | $Se^{-qT}N(d_1) - Ke^{-rT}N(d_2)$ | $Ke^{-rT}N(-d_2) - Se^{-qT}N(-d_1)$ |
| Delta | $e^{-qT}N(d_1)$ | $-e^{-qT}N(-d_1)$ |
| Gamma | $e^{-qT}\varphi(d_1) / (S\sigma\sqrt{T})$ | same as call |
| Vega (per vol pt) | $Se^{-qT}\varphi(d_1)\sqrt{T} \,/\, 100$ | same as call |
| Theta (per calendar day) | $\left[-\frac{S\varphi(d_1)\sigma e^{-qT}}{2\sqrt{T}} + qSe^{-qT}N(d_1) - rKe^{-rT}N(d_2)\right] / 365$ | $\left[-\frac{S\varphi(d_1)\sigma e^{-qT}}{2\sqrt{T}} - qSe^{-qT}N(-d_1) + rKe^{-rT}N(-d_2)\right] / 365$ |

Units: vega is divided by 100 because it is quoted per **one vol point** (OIC, above);
theta is divided by 365 because it is quoted per **one calendar day**.

Model scope — stated, not hedged: European exercise only, continuous dividend yield
only (no discrete dividends), and the implied vol is taken as given per strike, so the
reval inherits every property of the surface that produced it. American exercise is
`american-vs-european-style-option-exercise-handling`; the surface is
`options-implied-volatility-surface-construction`.

## The approximation

$$V(S_0 + h) \approx V_0 + \Delta_0 h + \tfrac{1}{2}\Gamma_0 h^2, \qquad \Delta(S_0 + h) \approx \Delta_0 + \Gamma_0 h$$

The delta-gamma expansion is a **local** approximation: its residual is $O(h^3)$ in
value and $O(h^2)$ in delta, with the delta residual scaling on $\partial\Gamma/\partial S$
(speed), which is large near the strike and near expiry. Two consequences the engine
acts on:

1. The same $h$ that is negligible on a one-year option is not negligible on a one-day
   one. The near-expiry trigger exists for this reason, not for tidiness.
2. The error is only knowable by repricing. The engine therefore reports
   `spot_taylor_value_error_per_unit` on every revaluation — the realised residual of
   the step it replaced, evaluated at the anchor's vol and expiry so the comparison
   isolates spot. That number, not a rule of thumb, is what a threshold should be
   calibrated on.

## Engineering standard

| Rule | Requirement |
|---|---|
| Anchor | The move test MUST be measured against the spot of the **last full revaluation**, never the previous tick. A last-tick baseline lets a monotone run of sub-threshold ticks travel arbitrarily far with zero revaluations. |
| Re-anchor atomicity | Spot, implied vol, expiry, timestamp, price and all four Greeks MUST be replaced in one update. A half-updated anchor is a permanent, silent bias. |
| Trigger set | Spot drift alone is NOT sufficient. Implied-vol movement, anchor age and proximity to expiry MUST each be able to force a revaluation independently. |
| Expiry | $T \le 0$ MUST raise. It MUST NOT be clamped to a small positive floor: an expired contract has no Black-Scholes delta and a floored one reports a confident wrong number. |
| Option type | MUST be validated against `{CALL, PUT}`. An `if CALL / else PUT` branch sign-flips delta on any typo. |
| Delta bounds | A stepped delta outside $[0,1]$ for a call or $[-1,0]$ for a put MUST force a revaluation rather than being published. |
| Non-finite input | A NaN/Inf spot, quantity, vol or expiry MUST raise. `abs(nan) > threshold` is `False`, so a NaN reads as a *small move* and pins the book on cached Greeks indefinitely. |
| Partial results | One invalid leg MUST reject the whole tick. A partial snapshot is a risk number with an unknown fraction of the book missing. |
| Underlying scope | A tick MUST apply only to positions on that underlying. Cross-underlying netting belongs to `options-greeks-real-time-portfolio-aggregation`. |
| Tick ordering | A tick older than the last processed tick for the same underlying MUST be rejected, not applied. |
| Multiplier | MUST be supplied per position from the contract master. MUST NOT default to 100. MUST be $> 0$. |
| Aggregation | Nets MUST be order-independent (`math.fsum`). |
| Cache invalidation | A feed gap or session boundary MUST reset the anchor. A stale anchor suppresses the revaluation that a missing one would force. |

## Thresholds are calibrated, not standard

The library defaults — 0.5% spot drift, 0.5 vol points, 60 seconds of anchor age, one
calendar day of near-expiry horizon — are **illustrative starting points and nothing
more**. No regulator, exchange or standards body publishes a Greeks-revaluation
threshold or cadence; MAR21 prescribes stress *shock sizes* for capital, not a refresh
rate for an intraday display. Calibrate each threshold against the observed
`spot_taylor_value_error_per_unit`, the book's gamma profile, and the measured cost of
a full revaluation pass, and record the rationale.

Equally, this repository makes **no latency claim** for either path. Whether a step or
a reprice fits a given tick budget depends on the interpreter, the hardware, the book
size and the call pattern; measure it on the target host before designing around it.

Note also where the saving actually comes from. Per-tick validation costs more than the
delta-gamma step it guards — a step is a few floating-point operations, validating a leg
is a couple of dozen type and range checks. That trade is deliberate (a risk engine that
publishes a NaN quickly has published nothing), but it means this architecture saves the
cost of *revaluation*, not the cost of touching every position. A caller whose position
list is stable between book changes can recover the rest by validating once at load:
`validate()` is idempotent and normalises in place.

## Known limitations

- **Single underlying per call**, single currency, monitoring only — no hedge is
  generated and nothing is halted.
- **Not thread-safe.** The anchor cache is mutable engine state; serialise ticks per
  instance.
- **Vol and expiry are caller-supplied per tick.** The engine detects that they
  changed; it does not know whether they are correct or current.
- **Second-order cross-Greeks** (vanna, volga, charm, speed) are not carried. They are
  what the trigger set bounds, not what it models.

## Category

`risk-management`
