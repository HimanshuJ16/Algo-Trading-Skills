# Reference Notes for Options Tail Risk Hedging

Nothing in this document is a regulatory requirement. Tail-hedge budgets, strike
moneyness and roll schedules are **discretionary policy choices**, not mandated
standards. The figures below are practitioner conventions and published empirical
results, labelled as such.

## Contract specifications (verifiable)

| Item | Value | Source |
|---|---|---|
| CBOE SPX index option multiplier | \$100 per index point; European exercise; cash settled | [Cboe SPX product specifications](https://www.cboe.com/tradable_products/sp_500/spx_options/specifications/) |
| OCC standard listed equity option | 100 shares of the underlying per contract | [OCC equity options product specifications](https://www.theocc.com/clearance-and-settlement/clearing/equity-options-product-specifications) |
| Adjusted contracts | The **multiplier stays 100**, but a corporate action can change the deliverable to something other than 100 shares (e.g. 3-for-2 split → 150 shares, strike adjusted). Read the adjusted contract, do not assume | [Fidelity, Option contract adjustments](https://www.fidelity.com/learning-center/investment-products/options/contract-adjustments) |

Cash settlement matters for the payoff model: an SPX put settles in cash against the
exercise-settlement value, so there is no share delivery and no assignment risk.
Physically settled equity options do not share that property.

## Volatility skew — not optional for this strategy

Index option markets have priced a smile since inception and, after the October 1987
crash, an asymmetric **skew/smirk in which deep-OTM puts carry the highest implied
volatilities** (AQR, *Tail Risk Hedging: Contrasting Put and Trend Strategies*, July
2020, p.4).

Consequence for sizing, at this skill's reference contract (spot 400, strike 340,
90 DTE, r = 4%, no dividend):

| IV fed to the pricer | Premium per 100-share contract | Multiple of the 20% price |
|---|---|---|
| 20% | \$61.00 | 1.00× |
| 25% | \$171.05 | 2.80× |
| 30% | \$334.01 | 5.48× |
| 35% | \$538.37 | 8.83× |

A deep-OTM put's price is highly convex in volatility. Substituting ATM vol for the
strike's own IV is not a small approximation — it changes the contract count by a
multiple. This is why `plan_systematic_otm_put_hedge` has no default `volatility`.

## Expected cost — published evidence

AQR (Ilmanen, Thapar, Tummala, Villalon), *Tail Risk Hedging: Contrasting Put and
Trend Strategies*, July 2020:

- A baseline strategy of buying 5% OTM one-month S&P 500 puts and rolling at expiry
  showed "persistently negative performance" over roughly 35 years (Jan 1985 – Mar
  2020), across a sample containing 1987, 1998, 2001, 2008 and 2020 (pp.3–5).
- "The broad pattern … is robust to every specification of passive put buying that
  we tested … regardless of our choice of maturity or moneyness," across six OTM put
  variants (5/10/20% OTM, one-month and one-year, delta-hedged) from Feb 1996 (p.5).
- Proximate cause: option prices imply volatilities and negative skewness that "tend
  to systematically exceed subsequent realizations" (p.4).
- Live peer group: the CBOE Eurekahedge Tail Risk Index has earned "around -2% per
  annum" since its 2008 inception, "but -8% per annum during the bullish 2010s"
  (p.10).

Source: [AQR white paper (PDF)](https://images.aqr.com/-/media/AQR/Documents/Insights/White-Papers/AQR-Tail-Risk-Hedging-Contrasting-Put-and-Trend-Strategies.pdf)

## Practitioner conventions (heuristics, not standards)

- **Annual premium budget**: commonly framed as 1–3% of AUM per year. This is a
  policy tolerance, not a market standard, and no regulator or exchange prescribes
  it. It is a *budget*, not a cost estimate — realised cost depends entirely on the
  IV paid.
- **Strike moneyness**: option-based tail hedging programs "sometimes use deeper OTM
  puts, say, 15-25% OTM" (AQR 2020, p.3 n.3). Deeper strikes cost less per contract
  and pay out in fewer scenarios; the trade-off is a choice, not an optimum.
- **Expiration and roll**: buy longer-dated, roll before expiry. AQR's longer-dated
  variant buys a one-year 20% OTM put and rolls the then-six-month contract into a
  new one-year contract every six months, explicitly "in order to maintain exposure
  to longer-dated puts" (p.5) — roughly a roll at half the original tenor. This
  skill's 90-DTE-buy / 30-DTE-roll default is the same shape at a shorter tenor.

## Why roll early — the correct rationale

A common but mistaken justification is "exponential theta acceleration." Theta
acceleration into expiry is an **at-the-money** phenomenon: at-the-money options
carry the most extrinsic value and lose it fastest near expiry, whereas options
"either deep-in-the-money or far out-of-the-money will have very little decay as
they have less time premium" ([OIC, *Theta*](https://www.optionseducation.org/advancedconcepts/theta)).
A 15% OTM put's absolute theta is small throughout its life.

The real reason to roll a tail hedge before expiry is **loss of convexity**: as the
contract shortens, a far-OTM put's gamma and vega collapse toward zero, so it stops
responding to the crash and the volatility spike it was bought to capture. Rolling
maintains exposure to longer-dated, still-convex contracts.

## Budget arithmetic

With a holding period of `dte_target - roll_dte` days:

```
rolls_per_year  = 365 / holding_days
tranche_budget  = portfolio_value * budget_pct / rolls_per_year
```

At the defaults (90 DTE bought, rolled at 30 DTE → 60-day hold → 6.08 rolls/year),
a 2% annual budget permits **0.329%** of portfolio value per tranche. Spending the
full 2% on each tranche instead realises ~12.1% of annual drag.

## Known limitations of the reference implementation

- Flat volatility per call; no smile or term structure inside the model.
- European exercise, cash settlement; no early exercise or assignment.
- Stress payoffs are terminal intrinsic values, so they are a **floor** — a crash
  before expiry leaves the put worth more than intrinsic.
- No bid/ask, commissions, exchange fees, margin or financing.
- A constant premium budget buys a varying quantity of protection: when volatility
  has already spiked, the same budget buys the fewest contracts.
