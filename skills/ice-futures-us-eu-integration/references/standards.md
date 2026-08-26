# Standards for ICE Futures US & EU Integration

All levels below are as published on the retrieval date shown. ICE states that
these levels are **subject to change without prior notification**, and Market
Supervision may vary them intraday. Re-derive them from the primary sources
rather than trusting a cached copy.

## Engineering standards

| Requirement | Standard |
|---|---|
| Order-entry price control | The **Reasonability Limit** is the control that refuses a limit order. Gate order entry on the RL, never on the No Cancellation Range. |
| Reference price | RL and NCR are measured from the **Exchange-set anchor price**, not from the BBO, the mid, or the order price. If the anchor is unavailable, fail closed. |
| Directionality | Refuse a **buy above** `anchor + RL` and a **sell below** `anchor − RL`. Never apply a symmetric `abs()` band. |
| Limit units | RL and NCR MUST be carried in the contract's quoted price units, with source and retrieval date. Tick counts are not published and MUST NOT be hard-coded. |
| Instrument identity | FIX Tag 200 `MaturityMonthYear` (`YYYYMM`) is the unambiguous contract-month identifier. `<ROOT><MONTH><YY>` is a display label only. |
| Venue | FIX Tag 207 `SecurityExchange` carries the ISO 10383 MIC. `IFEU` and `IFUS` are **operating** MICs; regimes requiring a segment MIC need the segment. |
| Valuation | Notional MUST be computed in the contract's own quotation convention and reported with its currency. |
| Arithmetic | Price and tick comparisons MUST use `Decimal`. Positivity MUST be checked separately from tick alignment. |

## ICE price controls — primary sources

**ICE Futures U.S., Appendix I — Error Trade Policy** (Exchange rulebook).
<https://www.ice.com/publicdocs/rulebooks/futures_us/--Appendix_l_Error_Trade_Policy.pdf>
Retrieved 2026-08-25.

> "The ETS incorporates price Reasonability Limits to prevent 'fat finger' type
> errors that cap the amount the price may change in one trading sequence from
> the anchor price. … Limit orders to sell at prices below the lower
> Reasonability Limit and limit orders to buy at prices above the upper
> Reasonability Limit will not be accepted by the ETS. Any trade executed at a
> price outside of the No Cancellation Range … may be considered an alleged error
> trade."

This single passage establishes three of this skill's core rules: the RL is the
order-entry control, it is anchored on the anchor price, and it is directional.

**ICE Futures U.S. — Reasonability Limits and No Cancellation Ranges**, August 2026.
<https://www.ice.com/publicdocs/futures_us/no_cancellation_range_and_reasonablity_limits.pdf>
Retrieved 2026-08-25.

- Defines the **Anchor Price**: "may be the previous night's settlement price,
  the opening call price or the last traded price", based on the front contract
  month, with back months derived "by applying spread differentials against the
  front month Anchor Price".
- Levels are published as price differences per contract, not tick counts.
- "Market Supervision staff has the authority to expand the No Cancellation Range
  and Reasonability Limit for a product to two (2) times the levels shown above
  in volatile market conditions and without prior notice."
- "Reasonability Limits are applicable during the pre-open period for all IFUS
  futures contracts (except for Natural Gas, Power and Emissions contracts), at
  levels up to three times the levels shown."
- Published levels used in `scripts/`:

  | Contract | RL | NCR |
  |---|---|---|
  | Sugar No. 11 (SB) | $.0050 per lb | $.0020 per lb |
  | U.S. Dollar Index (DX) | 0.500 index points | 0.200 index points |

  Sugar No. 11 is quoted in cents per pound, so these are 0.50 and 0.20 in the
  quoted unit.
- The 3 × NCR cancellation preference is stated for **option** trades. No
  equivalent multiple is stated for IFUS futures, which is why the engine reports
  `OUTSIDE_NCR_EXCHANGE_DISCRETION` rather than asserting one.

**ICE Futures Europe — Trade Adjustment and Cancellation Policy**, September 2025.
<https://www.ice.com/publicdocs/futures/Trade_Adjustment_Policy.pdf>
Retrieved 2026-08-25.

Section 2 enumerates the Exchange's controls and separates them cleanly:

> "(i) Reasonability Limits — hard limits above and below the Exchange set anchor
> price outside of which orders are not accepted and, in most cases, trading is
> prevented. (ii) Interval and Tiered Price Limits — dynamically managed circuit
> breakers which prevent large price movements in a particular direction within a
> given time period."

Section 3 defines the NCR as parameters "above and below an Exchange set anchor
price for each Contract within which a disputed trade will stand", and Section 1
gives the resolution ladder: price adjustment outside the NCR, and "automatic
trade cancellation for trades executed more than three times the designated No
Cancellation Range from market value". The Exchange "retains the right to
temporarily double the NCR during a trading session".

**ICE Futures Europe — Price Controls workbook**.
<https://www.ice.com/publicdocs/futures/Price_Controls.xlsx>
Retrieved 2026-08-25.

Per-contract NCR, spread NCR, pre-open RL, RL, and IPL/TPL values. Levels used
in `scripts/`:

| Product ID | Code | Contract | NCR | RL | IPL |
|---|---|---|---|---|---|
| 254 | `B` | ICE Brent Futures | $0.50 | 0.75 | $1.00 (3 s recalculation, 5 s hold) |
| 425 | `T` | **ICE WTI Futures** | $0.50 | 0.75 | $1.00 (3 s recalculation, 5 s hold) |
| 28456 | `TFN` | IFEU Dutch TTF Natural Gas Futures | 0.4 | 0.8 | 1.6 (2 min recalculation, 15 s hold) |

The workbook is also the authority for the point that ICE product contract code
`T` is **ICE WTI Futures**, not Dutch TTF.

## Contract specifications — primary sources

All retrieved 2026-08-25 from ICE's product pages.

| Contract | Code(s) | MIC | Size | Quotation | Min. fluctuation | Series |
|---|---|---|---|---|---|---|
| ICE Brent Crude Futures | `B`, also `BRN`, `BC` | IFEU | 1,000 barrels | USD and cents per barrel | $0.01 = $10/lot | up to 156 consecutive months |
| ICE WTI Crude Futures | `T` (hub "WTI") | IFEU | 1,000 barrels | USD and cents per barrel | $0.01 = $10/lot | up to 108 consecutive months |
| ICE Futures Europe Dutch TTF Natural Gas Futures | `TFN` | IFEU | 1 MW per day in the contract period × 23, 24 or 25 hours | EUR and euro cents per MWh | €0.005/MWh | monthly, quarterly, seasonal, annual |
| Sugar No. 11 Futures | `SB` | IFUS | 112,000 lb | cents and hundredths of a cent per pound | 1/100 cent/lb = $11.20/lot | March, May, July, October |
| US Dollar Index Futures | `DX` | IFUS | USD 1,000 × index | index points to three decimals | 0.005 = $5 | March/June/September/December |

- <https://www.ice.com/products/219/Brent-Crude-Futures>
- <https://www.ice.com/products/213/WTI-Crude-Futures>
- <https://www.ice.com/products/82843860/ICE-Futures-Europe-Dutch-TTF-Natural-Gas-Futures>
- <https://www.ice.com/products/23/sugar-no-11-futures>
- <https://www.ice.com/products/194/US-Dollar-Index-Futures>

Brent's 156-month series is what makes the two-digit display code ambiguous: a
`BZ26` label fits both Dec 2026 and Dec 2039.

## FIX and ISO 10383

- **Tag 207 `SecurityExchange`**, type `Exchange` — "Market used to help identify
  a security" (FIX 4.4). ISO 10383 MICs are the conventional population.
- **Tag 200 `MaturityMonthYear`** — `YYYYMM`.
- **Tag 54 `Side`** — `1` Buy, `2` Sell.
- **Tag 55 `Symbol`** / **Tag 48 `SecurityID`** — the content ICE expects is
  session- and interface-specific. ICE identifies products by a numeric product
  ID (Brent 254, WTI 425, TFN 28456) alongside the product contract code, and
  publishes several codes for the same contract. Take the required population
  from the ICE FIX specification for the session you are certified against; the
  `<ROOT><MONTH><YY>` formatter in `scripts/` is a display convention and is not
  asserted to be valid Tag 55 content for any ICE gateway.
- **MIC hierarchy** (ISO 10383): `IFEU` and `IFUS` are operating MICs. IFEU
  segments include `IFEN` (oil and refined products), `IFUT` (European
  utilities), `IFLL` (financials), `IFLX` (agricultural) and `IFLO` (equity);
  IFUS segments include `IFED` (energy division) and `IMAG` (agriculture).
  Reporting regimes that call for the segment MIC are not satisfied by the
  operating MIC.
