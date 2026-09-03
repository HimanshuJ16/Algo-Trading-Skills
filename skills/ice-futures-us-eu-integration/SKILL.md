---
name: ice-futures-us-eu-integration
description: >-
  Client-side pre-dispatch validation for ICE Futures Europe (IFEU) and ICE Futures U.S. (IFUS)
  outright futures orders — the directional Reasonability Limit check against the Exchange-set
  anchor price, per-contract minimum price fluctuation, quotation-convention-aware notional
  valuation, and post-trade No Cancellation Range exposure.
domain: Global Market Integration & FX
subdomain: Commodity & Energy Derivatives Gateway
tags: ["ice-futures", "brent-crude", "sugar-no-11", "fix-protocol", "ifeu", "ifus", "reasonability-limits", "no-cancellation-range"]
brokers_frameworks: ["ICE Futures Europe", "ICE Futures U.S.", "FIX 4.2 / 4.4", "Python Dataclasses"]
version: "2.0.0"
author: algo-trading-skills-contributors
license: Apache-2.0
---

## When to Use

Use this skill when building or auditing an order path into **ICE Futures Europe**
(ISO 10383 operating MIC `IFEU`) or **ICE Futures U.S.** (`IFUS`) for outright
futures — Brent Crude (`B`), ICE WTI (`T`), Dutch TTF Natural Gas (`TFN`),
Sugar No. 11 (`SB`), US Dollar Index (`DX`). It covers the checks that belong on
the client side, before a message leaves your process:

- Would ICE's **Reasonability Limit** refuse this limit order?
- Is the price on the contract's minimum price fluctuation?
- What is this order actually worth, in the currency and quotation convention the
  contract is quoted in?
- If it fills here, is the trade inside the **No Cancellation Range**, or exposed
  to price adjustment or cancellation?

ICE runs three distinct price controls, and conflating them is the most common
and most expensive mistake in this area:

| Control | When it acts | What it does |
|---|---|---|
| **Reasonability Limits (RL)** | Order entry | Hard limits above and below the Exchange-set anchor price. A **buy above** the upper limit or a **sell below** the lower limit is **not accepted**. |
| **Interval / Tiered Price Limits (IPL/TPL)** | Continuous trading | Dynamic circuit breakers. A bid or offer breaching the limit puts the market into a **hold period** — it does not reject your order outright. |
| **No Cancellation Range (NCR)** | **After** the trade | A trade inside the NCR stands. Outside it, IFEU's preferred resolution is price adjustment, and beyond 3 × NCR automatic cancellation — both at the Exchange's discretion. NCR **never rejects an order**. |

## When NOT to Use

- **Not a transport.** Nothing here opens a socket, logs on to a gateway, or
  sends an order. `ready_to_send` means "passed the checks modelled here", never
  "ICE has the order". Session management, throttles and recovery are out of scope.
- **Not a reference-data service.** The bundled catalog is a worked example of
  five contracts. ICE changes RL and NCR levels *without prior notification*, so
  every entry carries `limits_source` and `limits_as_of`. Refresh them from the
  ICE Futures Europe Price Controls workbook and the ICE Futures U.S.
  Reasonability Limits & No Cancellation Ranges document before relying on them.
- **Not a source of the anchor price.** The RL and NCR reference is an
  Exchange-set anchor price, not the mid and not the top of book. This module
  will not infer it, because it cannot be inferred from the order book.
- **Not an IPL/TPL model.** Interval and Tiered Price Limits, market and stop
  order protection limits, and minimum/maximum order value limits are separate
  ICE controls this module does not implement.
- **Not for options, spreads, strategies or off-exchange trades.** Scope is
  outright futures. Options carry a theoretical-value-based NCR and RL; calendar
  spreads have their own NCR and a Calendar Spread Limit Order Range; ICEBlock
  off-exchange transactions are outside the IFUS Error Trade Policy entirely.
- **Not a position-limit or MiFID reporting tool.** Note in particular that
  `IFEU` and `IFUS` are ISO 10383 **operating** MICs. Both have segment MICs
  (IFEU: `IFEN` oil and refined products, `IFUT` European utilities, `IFLL`
  financials, `IFLX` agricultural, `IFLO` equity; IFUS: `IFED`, `IMAG` and
  others). Regimes that require the segment MIC need the segment, not the
  operating MIC carried here for FIX routing.

## Prerequisites

- Per-contract ICE reference data: product contract code and numeric product ID,
  currency, quotation convention, minimum price fluctuation, lot size,
  Reasonability Limit and No Cancellation Range, and the listed contract series.
- The Exchange-set **anchor price** for the contract month you are pricing.
- The ICE FIX specification for the session you are certified against, for the
  actual content of Tag 55 / Tag 48.
- Python 3.9+. Standard library only (`decimal`, `dataclasses`, `logging`).

## Workflow

1. **Resolve the contract by its ICE product contract code, and check the code
   means what you think.** ICE codes are terse and reused across divisions: `T`
   is **ICE WTI Futures**, not Dutch TTF (which is `TFN`), and `T` is also the
   Feed Wheat code in the agricultural division. Confirm the resolved contract's
   name and currency before valuing anything.
2. **Reject a delivery month the contract does not list.** `DX` lists only the
   March/June/September/December quarterly cycle; `SB` lists March, May, July and
   October; Brent lists all twelve, up to 156 consecutive months.
3. **Format the identifiers, and know which one is load-bearing.** The
   `<ROOT><MONTH><YY>` code (`BZ26`) is a vendor-style display label. It is *not*
   an ICE wire identifier, and across Brent's 156-month curve the two-digit year
   is ambiguous — `BZ26` fits Dec 2026 and Dec 2039. FIX Tag 200
   `MaturityMonthYear` (`YYYYMM`) is the unambiguous one; Tag 207
   `SecurityExchange` carries the MIC; Tag 55 / Tag 48 content comes from the ICE
   FIX spec, not from a formatter.
4. **Value the order in the contract's own quotation convention.** Sugar No. 11
   is quoted in **US cents per pound** on 112,000 lb; Brent in **USD per barrel**
   on 1,000 bbl; TTF in **EUR per MWh**; DX in **index points** on a USD 1,000
   multiplier. Notional is
   `price × contract_size × currency_per_price_unit × quantity`, and the currency
   is part of the answer — a TTF notional is EUR, not USD.
5. **Refuse to guess a lot size that varies.** A TTF lot is 1 MW per day in the
   contract period × 23, 24 or 25 hours, so its MWh per lot depends on the
   delivery period and on daylight saving transitions. Supply it explicitly per
   contract month rather than hard-coding one number.
6. **Check the minimum price fluctuation in decimal arithmetic, and check
   positivity separately.** `Decimal('-75.50') % Decimal('0.01')` is zero, so a
   negative price passes the tick test on its own.
7. **Run the Reasonability Limit the way ICE runs it — directionally, from the
   anchor price.** The upper limit is `anchor + RL` and the lower is
   `anchor − RL`. A **buy above the upper** limit or a **sell below the lower**
   limit is refused; a deep passive buy below the market, or a far offer above
   it, is accepted. If no anchor price is available, fail closed — do not
   substitute the mid or the top of book.
8. **Allow for the Exchange widening the band.** Market Supervision may double
   the NCR and RL in volatile conditions without notice, and IFUS applies
   Reasonability Limits during the pre-open at up to three times the published
   levels for futures other than Natural Gas, Power and Emissions (IFEU publishes
   a separate pre-open column instead). Pass the multiplier explicitly; do not
   bake it into the published level.
9. **Report the error-trade exposure separately from the accept/reject verdict.**
   An order can pass the Reasonability Limit and still fill outside the No
   Cancellation Range. The exposure is measured from the anchor price and assumes
   a fill at the limit price — a marketable order fills at the resting price
   instead, so treat it as a bound, not a prediction.

> Full procedure: see `references/workflows.md`.
> Rule citations and published limit levels: see `references/standards.md`.
> Printable pre-flight checklist: see `assets/checklist.md`.

## Common Pitfalls

- **Treating the No Cancellation Range as an order-entry control.** This is the
  headline error. NCR is a *post-trade* error-trade parameter: it decides whether
  a trade that already happened can be broken. Gating order entry on the NCR
  rejects orders ICE would accept — the RL for Brent is USD 0.75 while the NCR is
  USD 0.50 — and gives no protection against the "fat finger" the RL exists to
  catch.
- **Measuring the limit from the BBO or the mid.** Both RL and NCR are measured
  from an **Exchange-set anchor price** — the previous session's settlement, the
  opening call price or the last trade, carried to back months by spread
  differentials. It is not the top of book, and it does not move with every tick.
- **Checking the limit symmetrically.** `abs(price − reference) > band` rejects
  deep passive bids and high passive offers that ICE accepts — exactly the
  resting liquidity a market maker means to post — while adding nothing on the
  aggressive side that the directional check does not already give.
- **Expressing the limits in ticks.** ICE publishes RL and NCR in **price units**
  per contract (Brent NCR USD 0.50 / RL 0.75; Sugar No. 11 NCR $.0020 / RL
  $.0050 per lb; DX NCR 0.200 / RL 0.500 index points; TFN NCR 0.4 / RL 0.8).
  There is no universal tick count, and a hard-coded default is a fabricated
  threshold.
- **Assuming a code means the same contract everywhere.** ICE product contract
  code `T` is ICE WTI Futures on IFEU. Reading it as Dutch TTF routes a
  EUR/MWh gas order into a USD/barrel crude contract at a plausible-looking
  price.
- **Hard-coding a TTF multiplier.** A monthly TTF lot runs from roughly 672 to
  745 MWh depending on the month and the daylight saving transition, and
  quarterly, seasonal and annual contracts are multiples of that. One constant is
  wrong for every period but one.
- **Feeding a contract the wrong quotation convention.** Sugar No. 11 is quoted
  in cents per pound. Passing the dollars-per-pound form (0.2250 instead of
  22.50) against a cents-based catalog overstates notional by 100×. Encoding the
  exchange's own tick is what turns that into a rejection instead of a silent
  mis-valuation.
- **Labelling every notional in USD.** TTF settles in EUR. A field named
  `notional_value_usd` holding a EUR figure will be summed into a USD exposure
  somewhere downstream.
- **Trusting cached limit levels.** ICE states these levels are "subject to
  change without prior notification", and Market Supervision may widen them
  intraday. Carry the source and retrieval date with the value.
- **Retrying an order because the request timed out.** ICE may already have the
  order. Resolve its state through the venue and reuse the original client order
  ID; a retry under a fresh identifier is a second position. See
  `order-placement-idempotency`.
- **Reading a reasonability pass as an execution guarantee.** Passing local
  checks says nothing about IPL hold periods, instrument state, throttles, or
  the Exchange's discretion to vary limits without notice.

## Verification

- Brent Dec 2026, anchor USD 75.40, RL USD 0.75 ⟹ band 74.65–76.15. A BUY at
  76.15 passes and at 76.16 is refused; a SELL at 74.65 passes and at 74.64 is
  refused; a BUY at 60.00 **passes** — the regression a symmetric band
  introduces. The same 76.16 is refused as a BUY and accepted as a SELL.
- Tick values reproduce ICE's published figures independently: Brent
  `0.01 × 1,000` ⟹ USD 10; Sugar No. 11 `0.01 × 112,000 × 0.01` ⟹ USD 11.20;
  DX `0.005 × 1,000` ⟹ USD 5.00.
- Brent 10 lots at 75.50 ⟹ contract value USD 75,500, notional USD 755,000.
  Sugar No. 11 at 22.50 cents/lb ⟹ USD 25,200 per lot.
- Sugar No. 11 at `0.2250` ⟹ `INVALID_TICK_SIZE`, because the dollars-per-pound
  form is not a whole number of 1/100-cent increments.
- TTF with no `contract_size` ⟹ `ValueError`; at 35.000 EUR/MWh, Dec 2026 (744
  MWh) ⟹ EUR 26,040 per lot and Nov 2026 (720 MWh) ⟹ EUR 25,200.
- `format_ice_symbol("B", "Z", 26)` ⟹ `ValueError`, not the malformed Tag 200
  `"2612"`. `format_ice_symbol("B", "Z", 2026)` and `(..., 2126)` produce the
  same display code and different Tag 200 values.
- `DX` with month code `F`, `SB` with `Z`, quantity `0` or `-10`, side
  `"BANANA"`, price `"NaN"` ⟹ `ValueError`/`TypeError`, not an approved order.
- Anchor price omitted ⟹ `NO_ANCHOR_PRICE`, `ready_to_send` False.
- Brent at 75.90 against anchor 75.40 ⟹ `WITHIN_NCR` (exactly 0.50); at 74.80 ⟹
  `OUTSIDE_NCR_PRICE_ADJUSTMENT`; at 60.00 ⟹ `OUTSIDE_NCR_AUTO_CANCELLATION`.
  Sugar No. 11 outside its NCR ⟹ `OUTSIDE_NCR_EXCHANGE_DISCRETION`, because ICE
  Futures U.S. states the 3 × NCR cancellation preference for options, not
  futures.
- Run `python -m unittest discover -s skills/ice-futures-us-eu-integration/scripts` and confirm a 100%
  pass rate.
- Against simulation only: submit one validated order and confirm ICE accepts the
  instrument identification and the price. A symbology error that unit tests
  cannot see is one where Tag 55 does not match what your ICE session expects.

## Related Skills

- `futures-contract-roll-automation`
- `exchange-tick-size-regime-tracking`
- `order-placement-idempotency`
- `eurex-market-data-and-order-api`
- `cme-globex-futures-api-integration`
- `synthetic-continuous-futures-contract-construction`
