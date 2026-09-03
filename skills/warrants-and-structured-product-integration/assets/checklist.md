# Warrants & Structured Product Pre-Flight Checklist

Derived from the Verification section of `SKILL.md`. Every box is checkable
against a number, a log line or a raised exception.

## Term-sheet onboarding

- [ ] **Entitlement ratio inverted.** The exchange quotes *warrants per share*;
      `entitlement_ratio` is *shares per warrant*. A term sheet's "10" is `0.1`.
      Confirm via `entitlement_ratio_from_conversion_ratio()`, and sanity-check
      that `warrant_price / entitlement_ratio` is the same order of magnitude as
      the underlying.
- [ ] **Product routed to the right model.** `pricing_model` on the valuation
      reads `BLACK_SCHOLES_MERTON` for covered warrants and
      `CBBC_INTRINSIC_PLUS_FUNDING` for Turbos / CBBCs. A CBBC marked with
      Black-Scholes is under-hedged.
- [ ] **CBBC call price on the correct side of the strike.** Bull: $B \ge K$.
      Bear: $B \le K$. A violation raises — treat it as a reference-data defect.
- [ ] **CBBC category declared** as `CATEGORY_N` or `CATEGORY_R`. It decides
      whether an MCE pays anything at all.
- [ ] **CBBC funding rate populated** from the launch announcement. Left at
      `0.0`, the contract prices at pure intrinsic — below every market quote.
- [ ] **Settlement mechanism confirmed** (cash vs physical) against the term
      sheet, not assumed from the default.
- [ ] **Issuer credit exposure recorded.** These are non-collateralised
      obligations; the engine does not model issuer default.

## Pricing and Greeks

- [ ] **Dividend yield supplied** for any underlying that pays one. $q = 0$ on a
      dividend-paying equity or ETF overstates the call, its delta and the hedge.
- [ ] **Greeks reconcile to finite differences.** Bump spot, vol and days and
      confirm delta, gamma, vega and theta reproduce the numerical derivative of
      the price. This is the check that catches a mis-signed theta term.
- [ ] **Greek units understood before aggregation.** Theta is per **calendar
      day**; vega is per **one volatility point**. Summing a per-year theta into
      a per-day book total is a 365× error.
- [ ] **CBBC gamma and vega read as zero, not as risk.** Both are identically
      zero under the delta-one convention. Barrier convexity is sized from the
      distance to the call price.
- [ ] **Gearing computed against a tradable price.** Pass `market_price` when the
      theoretical price is near or below the minimum tick, and check
      `gearing_basis_price` on the result to see which basis was used.
- [ ] **Effective gearing $\le$ simple gearing** and invariant to the entitlement
      ratio. A gearing that changes when $R_{\text{ent}}$ changes means the ratio
      has been applied twice.

## Delta hedging

- [ ] **`position_warrants` signed for the book actually held.** Positive long,
      negative issued. Cross-check `warrant_book_delta_shares` against an
      independent position record before routing — a wrong sign does not raise,
      it doubles the exposure.
- [ ] **Hedge direction sanity-checked.** Long call warrants → `SELL` underlying.
      Issued call warrants → `BUY`. Issued put warrants → `SELL`.
- [ ] **`rebalance_threshold_shares` set to the underlying's board lot**, not
      left at the default of one share, or the engine emits sub-lot orders the
      exchange will reject.
- [ ] **Hedge orders pass the desk's pre-trade risk layer.** The engine has no
      routing authority and enforces no exposure limits.

## Mandatory Call Event and expiry

- [ ] **Call price monitored on every tick**, not on the valuation schedule. The
      trigger is inclusive: touching the call price calls the contract.
- [ ] **Full hedge unwound on the MCE tick.** A partial unwind leaves an outright
      position opened at the worst point of the session.
- [ ] **Provisional residual not booked as a receivable.** The exchange fixes a
      bull contract on the *lowest* price of the calling session and the next, so
      the trigger-tick figure is a ceiling.
- [ ] **Settled residual recomputed** with
      `mandatory_call_residual_value(contract, settlement_price)` once the MCE
      valuation period closes, and reconciled against the provisional.
- [ ] **Category N recoveries booked at zero.** No buffer exists between call
      price and strike.
- [ ] **Expired lines routed to settlement, not to pricing.**
      `days_to_expiry <= 0` returns `EXPIRED` at intrinsic with zero Greeks.

## Negative controls

- [ ] Non-positive or non-finite spot, strike, entitlement ratio or market price
      raises `WarrantEngineError`.
- [ ] Zero or NaN implied volatility raises rather than dividing by zero.
- [ ] Negative dividend yield or funding rate raises.
- [ ] Non-integer `days_to_expiry` and fractional or boolean `position_warrants`
      raise.
- [ ] `AUTOCALLABLE_NOTE` raises instead of returning a price.
- [ ] The full suite passes:
      `python -m unittest discover -s skills/warrants-and-structured-product-integration/scripts`
