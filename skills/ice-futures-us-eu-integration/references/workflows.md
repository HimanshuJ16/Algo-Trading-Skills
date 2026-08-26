# Workflows for ICE Futures US & EU Integration

Full procedure behind `SKILL.md`. Rule citations are in `standards.md`.

## 1. Resolve the contract

1. Look the order up by **ICE product contract code**, not by a colloquial name.
2. Verify the resolved contract's `name`, `currency` and `operating_mic` before
   valuing anything. ICE codes are terse and reused across divisions — `T` is
   ICE WTI Futures on IFEU, and Dutch TTF is `TFN`.
3. Reject an unknown code loudly, naming the codes you do support. Do not fall
   back to a default contract.

## 2. Validate the delivery month against the listed series

Each contract lists its own series:

| Contract | Listed months |
|---|---|
| `B` Brent | all twelve, up to 156 consecutive months |
| `T` ICE WTI | all twelve, up to 108 consecutive months |
| `TFN` Dutch TTF | monthly, plus quarterly / seasonal / annual periods |
| `SB` Sugar No. 11 | H, K, N, V (March, May, July, October) |
| `DX` US Dollar Index | H, M, U, Z (quarterly cycle) |

A month code the contract does not list is a malformed request, not a market
condition — raise rather than returning a status.

## 3. Format the identifiers

1. `<ROOT><MONTH><YY>` is a **display label**. Two-digit years collide across
   Brent's 156-month curve, and ICE publishes several codes per contract
   (Brent is `B`, `BRN` and `BC`).
2. Require a four-digit year. A two-digit year silently yields a malformed FIX
   Tag 200 such as `"2612"`.
3. Emit:
   - Tag 200 `MaturityMonthYear` = `YYYYMM` — the identifier to reason with.
   - Tag 207 `SecurityExchange` = the ISO 10383 MIC.
   - Tag 54 `Side` = `1` or `2`, never the word.
   - Tag 55 / Tag 48 — populate from the ICE FIX specification for your session.

## 4. Value the order

`notional = price × contract_size × currency_per_price_unit × quantity`

`currency_per_price_unit` converts one unit of the quoted price into the contract
currency per underlying unit:

| Contract | Quotation | contract_size | currency_per_price_unit | Currency |
|---|---|---|---|---|
| `B` / `T` | USD per barrel | 1,000 bbl | 1 | USD |
| `TFN` | EUR per MWh | **varies** | 1 | EUR |
| `SB` | US cents per pound | 112,000 lb | 0.01 | USD |
| `DX` | index points | 1,000 | 1 | USD |

Cross-check against the exchange's published tick value:
`tick_size × contract_size × currency_per_price_unit` must give USD 10 for Brent,
USD 11.20 for Sugar No. 11, and USD 5.00 for DX.

For TTF, the lot size is 1 MW per day in the contract period × 23, 24 or 25
hours. Supply it per delivery period; refuse to proceed without it rather than
substituting a constant.

## 5. Check the minimum price fluctuation

1. Compare with `Decimal`. Float division of `75.505 / 0.01` gives
   `7550.499999999999`, which forces a tolerance-based check to guess how much
   error to forgive.
2. Check positivity as its own step — `Decimal('-75.50') % Decimal('0.01')` is
   zero.
3. A wrong quotation convention usually surfaces here: `0.2250` for Sugar No. 11
   is not a whole number of 1/100-cent increments.

## 6. Run the Reasonability Limit check

1. Obtain the **Exchange-set anchor price**. It is the previous session's
   settlement, the opening call price or the last traded price for the front
   month, carried to back months by spread differentials.
2. If there is no anchor price, **fail closed**. Do not substitute the mid or the
   top of book — they are not the reference ICE uses.
3. Apply any widening multiplier explicitly (Market Supervision may double the
   levels; IFUS pre-open applies up to 3× for futures).
4. Compute `upper = anchor + RL × multiplier`, `lower = anchor − RL × multiplier`.
5. Apply directionally:
   - **BUY**: refuse if `price > upper`. Any price at or below `upper` is fine,
     however far below the market.
   - **SELL**: refuse if `price < lower`. Any price at or above `lower` is fine.
6. The boundary is inclusive: ICE refuses bids *above* the upper limit, so a bid
   *at* the limit is accepted.

## 7. Classify error-trade exposure

Measured as `|price − anchor|` against the NCR (widened by the same multiplier):

| Distance | Classification | Meaning |
|---|---|---|
| ≤ NCR | `WITHIN_NCR` | The trade stands. |
| > NCR, ≤ 3 × NCR (IFEU) | `OUTSIDE_NCR_PRICE_ADJUSTMENT` | Preferred resolution is price adjustment. |
| > 3 × NCR (IFEU) | `OUTSIDE_NCR_AUTO_CANCELLATION` | Automatic cancellation. |
| > NCR (IFUS futures) | `OUTSIDE_NCR_EXCHANGE_DISCRETION` | May be alleged an error trade; ICE Futures U.S. states no futures multiple. |

This is a bound, not a prediction: it assumes a fill at the limit price, whereas
a marketable order fills at the resting price.

## 8. Report and log

Emit a structured report carrying the FIX tags, the notional **with its
currency**, the reasonability band actually applied, the distance from the
anchor, the exposure classification, and a status. Log rejections at `WARNING`
with the contract identified.

Treat a pass as "local checks passed", not as acceptance. Not modelled here:
Interval and Tiered Price Limits, market and stop order protection limits,
minimum/maximum order value limits, instrument state, and session throttles.

## 9. Keep the reference data fresh

RL and NCR levels are "subject to change without prior notification". Store
`limits_source` and `limits_as_of` alongside every level, re-pull the IFEU Price
Controls workbook and the IFUS Reasonability Limits document on a schedule, and
alert when a stored level diverges from the published one.
