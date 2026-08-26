# Workflows for LSE Millennium Integration

The full procedure behind `## Workflow` in `SKILL.md`. Every step runs client-side, before a
message is written to the session.

## 1. Load instrument reference data first

Currency and tick are properties of the instrument, not of the venue, so nothing about an
order can be validated until its reference-data record is resolved.

From the LSE Reference Data Service (MIT401), for each tradable instrument you route:

- `TIDM` — `STRING(4)` display mnemonic.
- `InstrumentID` — the identifier carried on trading messages, FIX Tag 48 `SecurityID`.
- `Currency` — `STRING(4)`, ISO 4217 plus the retained `GBX` code.
- `Price Tick Table ID` — the key into the Price Tick File.
- `ADNT` and `Liquid` — the instrument's average daily number of transactions and liquidity
  flag, needed only if you use the RTS 11 grid as a fallback.

From the Price Tick File (MIT401 §2.12), for each `Price Tick Table ID`: `Decimals` and the
`Min Value` / `Max Value` / `Tick Value` rows. `Min Value` is the lower band bound and
`Max Value` the upper; a static regime is a single row, a dynamic regime several.

Register the records with `register_instrument()`. `resolve_instrument()` raises
`UnknownInstrumentError` for an unregistered TIDM — an unknown symbol is missing reference
data, and defaulting it to a GBX share is how a USD line gets priced in pence.

Refresh cadence: reference data changes on corporate actions, ISIN changes, **trading
currency changes** and country-of-register changes (MIT201 §8.8). Liquidity bands change once
a year, from the first Monday of April following the FCA's ADNT publication.

## 2. Validate the mnemonic against the field the Exchange defines

`normalise_tidm()` upper-cases, strips, and enforces:

- Length ≤ 4 — the Exchange defines `TIDM STRING(4)`.
- Characters drawn from `A–Z`, `0–9` and `.` — TIDMs are **not** alphabetic-only. `BP.`,
  `BT.A`, `RR.` and `3IN` are live mnemonics. An `isalpha()` check refuses real instruments.

A five-character symbol is almost always a vendor code: a RIC (`SHEL.L`), a Bloomberg ticker
(`SHEL LN`) or a composite. Rejecting it here is the point — but remember that passing this
check gives you a *display* mnemonic. What goes on the wire is `InstrumentID`, and a TIDM can
change, in which case the instrument is deleted and re-added (MIT201 §4.6). Cache by
`InstrumentID`, not by mnemonic.

## 3. Match the payload currency to the instrument's currency

The LSE order book is not GBX-only. `Currency` is per-instrument reference data, ISO 4217
"except that, for SEAQ compatibility, GBX has been retained" (MIT401 §2.7). The iShares
Physical Gold ETC (`IGLN`) is quoted in USD.

A mismatch is rejected as `INVALID_CURRENCY`. The GBP-on-a-GBX-line case carries an extra
warning, because it is not a labelling error: a price scaled for pounds is 100× too small,
which on the buy side rests far below the book and on the sell side offers the position at
one per cent of its value.

## 4. Take the tick from the instrument, not from the price

`active_tick_size()` resolves in this order:

1. **The instrument's price tick table.** This is the increment the matching engine enforces.
   If a table is loaded but no band contains the price, the call raises rather than
   extrapolating the top band — a price above every published band means the table is stale.
2. **The UK RTS 11 floor**, only if no table is loaded *and* the instrument carries a
   liquidity band. `rts11_floor_tick(price, band)` reads the Annex cell for the price row and
   band column, with the price in the unit the instrument is quoted in (pence for GBX).
3. **Nothing.** With neither a table nor a band, the call raises
   `LiquidityBandRequiredError`. An ETF, ETC or certificate does not borrow a share's tick.

Two properties of the grid matter and are easy to get wrong:

- **It is two-dimensional.** At 3,385 GBX a band-6 share ticks at 0.5 GBX and a band-1 share
  at 20 GBX — a 40× difference at the same price. Any ladder keyed on price alone is wrong
  for most instruments.
- **It is a floor.** Article 2(1) requires a tick "equal to or greater than" the cell, so the
  venue's tick can be coarser and, under the UK's Article 2(2A), finer for an instrument
  first admitted on a third-country venue.

When both a reference-data tick and a liquidity band are present, the engine computes the
floor as a cross-check. A finer tick sets `tick_below_rts11_floor` and emits a warning rather
than rejecting: legitimate under Article 2(2A), a stale-data symptom otherwise.

## 5. Test the tick exactly, and test positivity separately

Convert every price through `Decimal` — floats are read via their shortest repr, so `205.3`
becomes `Decimal("205.3")` and not `205.30000000000001136868377216160297393798828125`. Then
test `price % tick == 0`.

Positivity is checked on its own, before the modulo, because `Decimal("-3385.0") % Decimal("0.5")`
is zero: a negative price passes the tick test. MIT201's order `Price` field requires a value
"greater than zero and a multiple of the instrument's 'tick'" — two conditions, not one.

Quantity is checked for integrality and positivity in the same pass. A float quantity, a zero
or a negative is a payload construction bug, and a negative quantity would otherwise produce a
negative notional in an otherwise "validated" report.

## 6. Value the order in the quoted unit, convert only what can be converted

- `notional_quoted = price × quantity`, exact, in the quoting unit. For a GBX line this is
  **pence**.
- `notional_gbp` divides by 100 for a GBX line, passes a GBP line through, and is `None` for
  any other currency, rounded half-up to the penny.

There is no FX rate in this module and none is inferred. A USD-quoted LSE line has a USD
notional; converting it is the caller's job, with a rate whose timestamp the caller can
defend.

## 7. Report generation

`validate_and_route_order()` returns an `LseOrderReport` and never raises for a bad payload,
so the order path logs and routes a verdict rather than trapping exceptions. The report
carries the status, the tick and where it came from (`tick_size_source`), the RTS 11 floor and
liquidity band used for the cross-check, both notionals, and any warnings.

`report.ready_to_send` is true only for `LSE_ORDER_VALIDATED`, and means only that the checks
modelled here passed. Exchange-side controls — price monitoring and circuit breakers, order
value limits, quantity limits, throttles, short-selling checks — are all still ahead of the
order.
