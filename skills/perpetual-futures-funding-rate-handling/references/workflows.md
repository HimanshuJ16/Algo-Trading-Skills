# Workflows for Perpetual Futures Funding Rate Handling

Scope: **linear (USDT/USDC-margined) perpetual swaps**, discrete settlement venues
(Binance, Bybit, OKX). See `standards.md` for the sourced venue table, including the
inverse-contract and continuous-funding (Deribit) exceptions this procedure does not cover.

## 0. Assemble the inputs from the venue

1. Pull the position: `positionAmt`, `positionSide`, `entryPrice` (Binance
   `GET /fapi/v2/positionRisk` or equivalent).
2. Pull the print: `lastFundingRate`, `nextFundingTime`, `markPrice`
   (`GET /fapi/v1/premiumIndex`). `nextFundingTime` is epoch milliseconds — convert
   with `funding_timestamp_from_epoch_ms`.
3. Pull the interval: `fundingIntervalHours` (`GET /fapi/v1/fundingInfo`). An absent
   entry means the venue default (8h) applies; **do not** treat "absent" as "always 8"
   without checking, because a symbol at the cap can be on hourly settlement.
4. Confirm the rate is a per-interval **decimal**. `0.0001` is `+0.01%`. If the field
   you are reading is already a percent, divide before constructing the update.

## 1. Resolve direction

- `side` decides direction; `position_qty` supplies magnitude.
- `positionSide="BOTH"` (Binance one-way mode) carries no direction — derive
  `LONG`/`SHORT` from the sign of `positionAmt` before constructing the position.
- A `LONG` with a negative quantity is a contradiction, not a short. Resolve it
  upstream; the engine raises rather than picking an interpretation.

## 2. Notional value and signed funding payment

- `notional = |position_qty| × mark_price` (quote currency).
- `payment = direction × notional × funding_rate`, where `direction` is `+1` for a
  long and `−1` for a short.
- Positive payment = outflow (fee paid). Negative = inflow (funding received).
- Use the **mark price at the funding timestamp**. Not the last trade price, and not
  the entry price — funding is charged on marked value regardless of open P&L.

## 3. Annualized carry (two numbers, two meanings)

- `periods_per_year = 8760 / funding_interval_hours` (1095 at 8h, 2190 at 4h,
  8760 at 1h).
- Simple: `APR = direction × rate × periods_per_year × 100%`.
- Compounded: `APY = ((1 + direction × rate) ^ periods_per_year − 1) × 100%`.
- Both assume the single print repeats unchanged for a year. That is a comparison
  device for policy limits, not a projection. Real rates mean-revert, flip sign and
  are capped.
- Quote the APR for a single held interval. Quote the APY only when the carry is
  genuinely being rolled — and then do not label it an APR.

## 4. Adverse drag audit

- Breach requires **both**: the position is paying (`payment > 0`) **and**
  `APR > max_adverse_funding_apr × 100`.
- The comparison is strict: exactly on the ceiling is not a breach.
- Funding income never breaches, however large.
- A shorter interval can turn a passing rate into a breach on the same print — this
  is the intended behaviour, not a bug.

## 5. Timing check (optional, requires an explicit clock)

- Pass a timezone-aware `now_utc` to obtain `hours_to_next_funding`.
- A negative value means the print is stale — the settlement it refers to has already
  passed. Refresh before acting; the report says so in `audit_notes`.
- Liability attaches to the position held **at** the timestamp with no proration on
  these venues. Binance documents up to ~15 seconds of deviation in the actual charge
  time, so an exit timed to the second is not a control.

## 6. Report and act

- `FundingRateReport` carries: notional, rate as a percent, signed payment, APR, APY,
  the interval and periods actually used, `hours_to_next_funding`, status and an
  advisory `recommended_action`.
- `recommended_action` is advisory. This module places no orders. Route an actual
  unwind through an independent risk control.

## 7. Rejections — and why each one is a rejection rather than a default

| Input | Behaviour | Reason |
|---|---|---|
| Unrecognised `side`, or `BOTH` | Raise | Guessing flips the sign of a real cash flow. |
| `LONG` with negative quantity | Raise | The two direction signals disagree; either could be the typo. |
| Zero quantity | Raise | A flat position has no funding liability; a zero report reads as "no funding due" and hides the fact that nothing was audited. |
| Non-finite rate or price | Raise | `NaN > 0` is `False`, so a corrupt rate would otherwise be classified as *income*. |
| `funding_interval_hours <= 0` | Raise | Coercing to 1 silently produces an APR 8× the truth. |
| Symbol mismatch | Raise | Cross-instrument arithmetic computes cleanly and means nothing. |
| Rate beyond the plausibility guard | Raise | Far above every published venue cap; almost certainly a percent value not divided by 100. |
| Naive `now_utc` | Raise | A local clock shifts time-to-funding by the host's UTC offset. |
