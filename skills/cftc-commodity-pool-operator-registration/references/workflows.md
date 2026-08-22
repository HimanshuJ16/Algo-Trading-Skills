# Workflows for CFTC 4.13(a)(3) De Minimis Threshold Monitoring

## 1. Pre-trade hook integration

- The OMS calls `CftcCpoComplianceEngine.evaluate_trade()` **before** submitting
  any order in a commodity interest (futures, options on futures, swaps, retail
  forex). Use `check_trade_compliance()` where a plain boolean is all the order
  path can carry; it wraps `evaluate_trade()` and fails closed on invalid input.
- Securities and cash bonds are not commodity interests: pass
  `is_commodity_interest=False` and they bypass both numerators. They still
  affect the denominator through the liquidation value you supply.
- Evaluate per order rather than caching a verdict. Each test is determined at
  the time the most recent position was established, so every new position is
  measured against the headroom available at that moment.

## 2. Liquidation value feed

- Liquidation value = cash + market value of securities + unrealized P&L on all
  open positions, per 4.13(a)(3)(ii)'s "after taking into account unrealized
  profits and unrealized losses".
- Feed it fresh. A stale or subscription-capital denominator overstates
  headroom; the engine cannot detect this and will approve trades on it.
- If liquidation value is non-positive, no headroom exists: the engine blocks
  new exposure but still permits risk-reducing trades.

## 3. Margin aggregate (test A)

- Aggregate, across all open commodity interest positions: initial margin,
  option premiums, and the required minimum security deposit for retail forex
  (17 CFR 5.1(m)).
- For an option in-the-money at purchase, the in-the-money amount may be
  excluded from this numerator — see `standards.md` for the caveat on the
  190.01 cross-reference.
- Add the signed margin delta of the proposed trade, then compare against
  `0.05 × liquidation_value`.

## 4. Notional aggregate (test B)

- Aggregate notional per the instrument-specific formulas in `standards.md`.
  Options use delta-adjusted strike notional; retail forex uses USD value at
  establishment net of offsetting transactions; cleared swaps follow Part 45.
- The engine sums gross unless you net upstream within what
  4.13(a)(3)(ii)(B) permits (same underlying commodity across DCMs/FBOTs; swaps
  cleared on the same DCO).
- Add the signed notional delta of the proposed trade, then compare against
  `1.00 × liquidation_value`.

## 5. Signed-delta convention

- Positive delta = exposure added (open/increase). Negative delta = exposure
  released (close/offset), given as the magnitude released.
- Direction of the position is **not** encoded in the sign. A new short future
  adds positive notional exactly like a new long, because the aggregate is a
  gross exposure figure.
- A delta that would drive a projected aggregate below zero raises `ValueError`
  — the position book and the proposed trade disagree, and silently clamping
  would mask a reconciliation bug in the exposure feed.

## 6. Decision logic

- If neither projected aggregate exceeds its current value, allow the trade
  unconditionally. This holds even when the pool currently fails both tests:
  blocking unwinds would leave the pool stuck outside the exemption.
- Otherwise, if liquidation value ≤ 0, block new exposure.
- Otherwise, allow if projected margin ≤ 5% of liquidation value **or**
  projected notional ≤ 100% of liquidation value. Passing either test satisfies
  4.13(a)(3)(ii).
- Block only when both tests fail. Comparisons are made cross-multiplied
  (`aggregate ≤ threshold × liquidation_value`) so a position sitting exactly on
  a threshold is not rejected by floating-point rounding.

## 7. After a block

- Persist the `ComplianceDecision` (both ratios, both projected aggregates,
  which test passed, reason). It is the contemporaneous record of the moment the
  position would have been established.
- Remediation is a compliance decision, not an automated one: size the order
  down into the remaining headroom, unwind existing exposure first, rely on a
  different exemption or exclusion, or register as a CPO. Do not wire an
  automatic override into the order path.
- Track the 4.13(b)(4) annual affirmation separately — the arithmetic in this
  engine says nothing about whether the notice of exemption is still live.
