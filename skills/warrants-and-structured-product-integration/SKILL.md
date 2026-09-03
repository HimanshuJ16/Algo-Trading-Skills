---
name: warrants-and-structured-product-integration
description: >-
  Use when pricing, risk-managing or delta-hedging listed covered warrants and Turbo warrants / CBBCs on HKEX, Euronext, SGX or Borsa Italiana. Prices covered warrants with Black-Scholes-Merton and Turbo warrants / CBBCs with the issuer's delta-one intrinsic-plus-funding-cost convention, scales every price and Greek by the entitlement ratio, evaluates Mandatory Call Events including Category N vs Category R residual value, computes HKEX simple and effective gearing, and sizes signed delta-hedge rebalances.
domain: Multi-Asset Derivatives & Structured Products
subdomain: Warrants & Exotic Derivatives Integration
tags: ["warrants", "covered-warrants", "cbbc", "turbo-warrants", "entitlement-ratio", "effective-gearing", "mandatory-call-event", "delta-hedging"]
brokers_frameworks: ["hkex-warrants", "euronext-warrants", "sgx-warrants", "borsa-italiana", "Black-Scholes-Merton", "Python Standard Library (math)"]
version: "2.0.0"
author: algo-trading-skills-contributors
license: Apache-2.0
---

## When to Use

Use this skill when a position, a hedge or a mark involves a **listed warrant**:
a covered call/put warrant issued by a bank, or a **Turbo warrant / CBBC**
(Callable Bull/Bear Contract). Typical callers are a market maker hedging an
issued book, a systematic strategy taking geared directional exposure through
warrants, or a risk engine that has to mark and monitor a warrant line.

The skill exists because two properties of these instruments break naive
option-pricing code:

- **The entitlement ratio** rescales every price and every Greek. Ten warrants
  to one share means the warrant's delta is $0.1$ shares, not $1$.
- **A Turbo / CBBC is not a vanilla option.** It is a knock-out barrier product
  that the market prices as *intrinsic value plus funding cost* with a delta of
  approximately one underlying unit. Pricing it with Black-Scholes $N(d_1)$
  systematically under-hedges the book, because $N(d_1) < 1$ always.

**Read these two conventions before passing any data in.**

- **`entitlement_ratio` is the reciprocal of the exchange quote.** HKEX quotes
  the entitlement ratio as *warrants per share* — "the number of products
  required to be converted into a unit of the underlying asset at the strike
  price on the expiry date". A term sheet reading *"Entitlement ratio: 10"* is
  `entitlement_ratio = 0.1` here. Pasting the exchange's `10` straight in is a
  **100×** error in price, delta and hedge size, and nothing downstream flags it —
  every number stays positive and plausible. Use
  `entitlement_ratio_from_conversion_ratio(10.0)` rather than inverting by hand.
- **`position_warrants` is signed.** Positive = warrants held long; negative =
  warrants sold or issued. The delta-neutral target is
  $-N_{\text{warrants}} \times \Delta$: a long call-warrant position is hedged by
  *selling* the underlying, an issued one by *buying* it. Version 1.1.0 returned
  $+N \times \Delta$, which inverts the hedge for a long holder and **doubles**
  the exposure it was meant to remove.

## When NOT to Use

- **For an autocallable note or any path-dependent structured product.** An
  autocallable needs a coupon and observation schedule and a barrier model, not
  a single-strike valuation. `WarrantType.AUTOCALLABLE_NOTE` exists only so that
  passing one raises `WarrantEngineError` — version 1.1.0 let it fall through to
  the put branch and returned a plausible price with a *negative* delta.
- **For barrier-proximity risk on a CBBC.** The delta-one convention returns
  $\Gamma = \text{Vega} = 0$ by construction. The real product is a barrier
  option whose gamma spikes violently as spot approaches the call price. Size
  that risk from the distance-to-call and the MCE monitor, never from these
  Greeks.
- **As a smile-aware pricer for covered warrants.** One flat $\sigma$, no skew,
  no term structure. Calibrate with `options-implied-volatility-surface-construction`
  and treat this engine's output as a flat-vol reference point, not a mark.
- **For company-issued (dilutive) equity warrants.** Those create new shares on
  exercise and need a dilution adjustment this engine does not model. Covered
  warrants and CBBCs are third-party issued and carry no dilution.
- **For issuer credit risk.** Covered warrants and CBBCs are non-collateralised
  obligations of the issuing bank. See
  `counterparty-credit-risk-for-otc-derivatives`.
- **At or past expiry as a pricing call.** `days_to_expiry <= 0` returns an
  `EXPIRED` valuation at intrinsic with zero Greeks. An expired warrant is a
  settlement problem — see `physical-vs-cash-settlement-handling`.

## Prerequisites

- Python 3.9+ and the standard library only (`math`, `dataclasses`, `enum`,
  `typing`, `logging`). No third-party dependencies.
- Term-sheet data per line: warrant type, `strike_price`, `entitlement_ratio`
  (**shares per warrant**), `days_to_expiry`, and for a Turbo / CBBC the
  `barrier_price` (call price), `cbbc_category` and `funding_rate_annual` from
  the launch announcement.
- Market data as annualized decimals: underlying spot, `risk_free_rate`,
  `dividend_yield`, and — for covered warrants only — `implied_volatility > 0`.
- For gearing that matches the exchange's published figure, the warrant's own
  **traded** price, passed as `market_price`.

## Workflow

1. **Build the contract from the term sheet, inverting the entitlement ratio.**
   - **Decision point — is the ratio you were handed warrants-per-share or
     shares-per-warrant?** Exchange and issuer documents quote the former. Run it
     through `entitlement_ratio_from_conversion_ratio()`. A silent 100× error
     here reaches the order router as a 100× hedge.
   - For a Turbo / CBBC, `cbbc_category` is **mandatory**: it decides whether a
     Mandatory Call Event pays a residual value or nothing. The engine refuses to
     price a CBBC left at `NOT_APPLICABLE` rather than assuming one.
   - The engine enforces the term-sheet invariant that a bull CBBC's call price
     is $\ge$ its strike and a bear's is $\le$ its strike. A violation is a
     reference-data error, not a pricing input.
2. **Price with `price_warrant(spot, contract, market_price=None)`** and read
   `pricing_model` on the result to confirm which model produced the mark.
   - **Decision point — a Mandatory Call Event is checked *before* expiry.** A
     CBBC can be called on its last trading day, and the call terminates the
     contract either way. The engine returns `KNOCKED_OUT`, not `EXPIRED`.
   - Covered warrants take Black-Scholes-Merton with the continuous dividend
     yield $q$; a covered warrant on a dividend-paying underlying priced at
     $q = 0$ overstates the call and its delta.
   - Turbos / CBBCs take
     $P = R_{\text{ent}}\left[(S-K)^{+} + K \cdot f \cdot n/365\right]$ with
     $\Delta = R_{\text{ent}}$. Leaving `funding_rate_annual` at `0.0` prices the
     contract at pure intrinsic, below every real market quote.
3. **Read gearing off the right price.**
   - Simple gearing is $S \cdot R_{\text{ent}} / P$; effective gearing is
     $\text{Simple} \times |\Delta_{\text{raw}}|$, which is the price elasticity
     $(S/P)\,\partial P/\partial S$. The entitlement ratio appears **once**, not
     twice — putting it in both factors squares it.
   - **Decision point — when the theoretical price is below the minimum tick,
     gearing computed against it is meaningless.** Pass the traded
     `market_price`; the result records which basis it used in
     `gearing_basis_price`. Version 1.1.0 floored the price at `0.0001` instead
     and reported gearings of 100,000×.
4. **Size the hedge with
   `calculate_delta_hedge_signal(valuation, position_warrants, current_shares, rebalance_threshold_shares)`.**
   - **Decision point — sign `position_warrants` for the book you actually
     hold.** The target is $-N \times \Delta$. A wrong sign does not raise; it
     silently doubles the exposure. Cross-check `warrant_book_delta_shares`
     against an independent position record before routing.
   - Set `rebalance_threshold_shares` to the underlying's **board lot**, not the
     default of one share, or the engine will generate sub-lot orders the
     exchange rejects. See `minimum-fill-size-and-lot-rounding-logic`.
5. **Monitor the call price on every tick, not on a valuation schedule.**
   - `is_mandatory_call_triggered()` is inclusive at the barrier: touching the
     call price triggers the call.
   - On a call, the valuation's delta is `0.0`, so the hedge signal targets zero
     shares and unwinds the whole hedge in one instruction. Anything still held
     after a call is naked directional exposure, not a hedge.
   - **Decision point — the residual value in the valuation is provisional.**
     The exchange settles a bull CBBC on the *lowest* underlying price over the
     MCE valuation period (the session in which the call occurred and the one
     after), so the realized residual is at most the provisional figure.
     Recompute with `mandatory_call_residual_value(contract, settlement_price)`
     once that period closes, and do not book the provisional number as a
     receivable.

> Full procedure: see `references/workflows.md`.
> Standards reference: see `references/standards.md`.
> Printable pre-flight checklist: see `assets/checklist.md`.

## Common Pitfalls

- **Pasting the exchange's entitlement ratio in un-inverted.** A term sheet's
  "10" entered as `entitlement_ratio = 10.0` instead of `0.1` scales the price,
  the delta and the hedge by 100×. Every output stays positive and plausible.
- **Pricing a Turbo / CBBC with vanilla Black-Scholes.** HKEX and the HK issuers
  describe CBBCs as delta-one, in-the-price products whose price is intrinsic
  value plus funding cost. A vanilla $N(d_1)$ delta is always below one, so an
  issued book hedged on it is permanently under-hedged — and the shortfall is
  largest exactly where the position is largest.
- **Signing the hedge as $+N \times \Delta$.** That is the hedge for a *short*
  book. Applied to a long warrant position it buys underlying against long calls
  and doubles the delta instead of removing it.
- **Leaving underlying shares on after a Mandatory Call Event.** The CBBC's delta
  goes to zero the instant the call price is touched; the hedge behind it does
  not. Whatever is still held is an outright position taken at the worst moment
  in the underlying's session.
- **Booking the provisional MCE residual as the recovery.** A bull CBBC settles
  on the *lowest* underlying price of the valuation period, so the trigger-tick
  estimate is an upper bound. Category N contracts recover nothing at all — their
  call price equals their strike, so there is no buffer.
- **Reading a CBBC's gamma or vega as risk.** Both are identically zero under the
  delta-one convention. The real barrier product's convexity is concentrated at
  the call price, where this model shows none.
- **Confusing simple with effective gearing.** Simple gearing ignores delta. A
  deep out-of-the-money warrant can show high simple gearing and near-zero
  effective gearing, because it barely responds to the underlying at all.
- **Computing gearing off a sub-tick theoretical price.** A model price of
  $10^{-5}$ produces a five-figure gearing that describes nothing tradable. Pass
  the traded price.
- **Pricing a covered warrant on a dividend-paying underlying at $q = 0$.** It
  overstates the call, its delta and therefore the hedge. HK warrants are
  routinely written on dividend-paying equities and ETFs.

## Verification

- **Canonical anchor**: with $R_{\text{ent}} = 1$, $q = 0$, the engine reproduces
  the standard worked example
  $\text{BS}(100, 100, 1\text{y}, 5\%, 20\%) = 10.450583572185565$ (call) and
  $5.5735260222569727$ (put) to 12 decimal places.
- **Put-call parity**: $C - P = R_{\text{ent}}(Se^{-qT} - Ke^{-rT})$ to 12 decimal
  places, which detects a mis-signed dividend or discount term.
- **Greeks vs. finite differences**: every Greek matches a Richardson-extrapolated
  central difference of the *price* — delta, gamma and vega to 9 decimal places,
  theta to 6 — at out-of-the-money, at-the-money and in-the-money strikes for
  both calls and puts.
- **Theta regressions**: call theta uses $N(d_2)$, not $N(d_1)$; put theta *adds*
  the rate term $+rKe^{-rT}N(-d_2)$. Version 1.1.0 got both wrong and overstated
  put decay by roughly 60%.
- **CBBC convention**: a bull CBBC's price equals
  $(S-K)R_{\text{ent}} + K f (n/365) R_{\text{ent}}$ by hand-computed arithmetic;
  its delta is exactly $R_{\text{ent}}$, confirmed by a $1.00$ move in spot moving
  the price by exactly $R_{\text{ent}}$; its theta equals the daily funding
  accrual, confirmed against a one-day roll-down.
- **Gearing identity**: effective gearing equals the numerically differentiated
  elasticity $(S/P)\,\partial P/\partial S$ to 8 decimal places, and is invariant
  to the entitlement ratio.
- **MCE**: the barrier is inclusive on both sides; Category R pays
  $\max(0, S_{\text{settle}} - K)R_{\text{ent}}$ against the **strike**, Category
  N pays zero, and a settlement below the trigger tick pays less than the
  provisional figure.
- **Hedge sign**: long call warrants produce `SELL`, issued call warrants `BUY`,
  issued put warrants `SELL`; a called or expired warrant targets zero shares and
  unwinds the full hedge.
- **Negative checks**: non-positive or non-finite `spot_price`, `strike_price`,
  `entitlement_ratio` or `market_price`; zero or NaN `implied_volatility`;
  negative `dividend_yield` or `funding_rate_annual`; non-integer
  `days_to_expiry`; a bull CBBC with its call price below its strike (and the
  bear mirror); a CBBC with no declared category; a fractional or boolean
  `position_warrants`; and `AUTOCALLABLE_NOTE` must all raise
  `WarrantEngineError`.

Run the suite and confirm a 100% pass rate:

```bash
python -m unittest discover -s skills/warrants-and-structured-product-integration/scripts
```

## Related Skills

- `greeks-based-portfolio-hedging-automation`
- `options-greeks-real-time-portfolio-aggregation`
- `real-time-greeks-recalculation-on-market-moves`
- `options-implied-volatility-surface-construction`
- `american-vs-european-style-option-exercise-handling`
- `physical-vs-cash-settlement-handling`
- `counterparty-credit-risk-for-otc-derivatives`
- `minimum-fill-size-and-lot-rounding-logic`
- `hong-kong-exchange-hkex-orion-api`
- `quanto-options-and-cross-currency-derivative-structures`
- `variance-swap-and-volatility-derivative-pricing`
- `total-return-swap-synthetic-exposure`
