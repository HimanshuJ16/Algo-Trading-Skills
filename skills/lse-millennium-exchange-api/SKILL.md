---
name: lse-millennium-exchange-api
description: >-
  Use when validating an order for the London Stock Exchange order book before dispatch:
  the TIDM mnemonic format, the per-instrument trading currency including pence versus
  pounds, and the applicable tick band.
license: Apache-2.0
metadata:
  domain: algorithmic-trading
  subdomain: global-market-integration
  tags: lse, london-stock-exchange, millennium-exchange, gbx, tidm, uk-rts-11, tick-size, sets
  brokers_frameworks: "LSE Millennium Exchange (MIT201); LSE Reference Data Service (MIT401); UK RTS 11 — assimilated (EU) 2017/588; Python Decimal"
  version: "2.0.0"
  author: algo-trading-skills-contributors
---

## When to Use

Use this skill when building or auditing the last step before an order message leaves your
process for the **London Stock Exchange** order book (Millennium Exchange, via FIX or the
native binary gateway). It covers the checks that are yours to make client-side:

- Is this mnemonic actually a TIDM, or a vendor symbol (`SHEL.L`, `SHEL LN`) that will not
  resolve?
- Is the price expressed in the currency **this instrument** is quoted in?
- Is the price a legal increment for **this instrument**, not for an average LSE share?
- What is the order worth in pounds?

The two errors this skill exists to prevent are structurally different, and conflating them
is what makes them expensive:

| Error | What actually happens |
|---|---|
| **Price sent in pounds for a GBX-quoted line** | `26.50` instead of `2650` is not a mislabelled price, it is a price 100× too small. It does not round-trip: a buy that far below the book rests as a stale passive order, and a sell at 1% of value is a gift. |
| **Price off the instrument's tick** | Millennium Exchange rejects it on entry: "if the price of an order/quote is not a multiple of the tick size on entry it will be rejected" (MIT201 §5.5). No fill, no position, and a rejected message on a throttled session. |

## When NOT to Use

- **Not a transport, and not an acceptance.** Nothing here opens a session, logs on to a
  gateway, or sends an order. `ready_to_send` means "passed the checks modelled here",
  never "the Exchange has the order".
- **Not a reference-data service.** `DEFAULT_INSTRUMENTS` is a four-instrument worked
  example, and its liquidity bands are *inferred from published quote increments*, not read
  from an FCA publication. Load real records from the LSE Reference Data Service (MIT401)
  before trading on it.
- **Not the identifier you put on the wire.** Millennium Exchange identifies instruments by
  a unique `InstrumentID`, carried as FIX Tag 48 `SecurityID` (MIT201 §4.6). The TIDM is a
  display mnemonic; validating it does not give you a routable order.
- **Not an FX converter.** A USD- or EUR-quoted LSE line gets a notional in its own
  currency and `notional_gbp = None`. This module holds no rate and will not invent one.
- **Not a full pre-trade risk gate.** Price collars, dynamic and static circuit breakers,
  order value limits, minimum/maximum quantities, order-to-trade ratios and short-selling
  checks are separate controls, none of them implemented here.
- **Not for off-book trade reports.** Price format codes "have no relevance for the price
  field of manual trade reports" (MIT201 §5.5).

## Prerequisites

- Per-instrument reference data from the LSE Reference Data Service (MIT401): TIDM,
  `InstrumentID`, `Currency`, and the instrument's `Price Tick Table ID` with its
  `Min Value` / `Max Value` / `Tick Value` rows.
- **When the instrument's own tick table is not loaded:** its RTS 11 liquidity band, derived
  from the FCA's annual ADNT calculation published through FITRS. `liquidity_band_for_adnt()`
  maps a published ADNT onto bands 1–6.
- Python 3.7+. Standard library only (`decimal`, `dataclasses`, `enum`, `logging`).

## Workflow

1. **Resolve the instrument before validating anything about the order.** Currency and tick
   are properties of the instrument, not of the venue. `resolve_instrument()` raises on an
   unregistered TIDM rather than defaulting to GBX — an unknown symbol is missing reference
   data, not a GBX share.
2. **Check the mnemonic against the field the Exchange actually defines.** TIDM is
   `STRING(4)` (MIT401 §2.7) and is *not* restricted to A–Z: `BP.`, `BT.A`, `RR.` and `3IN`
   are all live mnemonics. A five-character symbol is almost always a vendor code.
3. **Compare the payload currency against the instrument's `Currency` field.** LSE is not a
   GBX-only venue: `Currency` is ISO 4217 "except that, for SEAQ compatibility, GBX has been
   retained" (MIT401 §2.7), and lines such as the iShares Physical Gold ETC (`IGLN`) are
   quoted in USD. A GBP payload on a GBX line is rejected on currency, before it can be
   priced 100× low.
4. **Take the tick from the instrument's price tick table when you have it.** That is the
   increment the matching engine enforces, and it may be static or dynamic (MIT201 §5.5).
   If a loaded table has no band covering the price, fail closed — do not extrapolate the
   top band.
5. **Fall back to the UK RTS 11 grid only with a liquidity band, and treat it as a floor.**
   The tick is a cell in a 19 price ranges × 6 liquidity bands table; Article 2(1) requires
   venues to apply a tick "equal to or greater than" that cell, so the grid can be too fine
   but never too coarse. Price alone does not determine the tick: at 3,385 GBX a band-6
   share ticks at 0.5 GBX and a band-1 share at 20 GBX.
6. **Flag a reference-data tick finer than the floor rather than rejecting it.** Since 28
   April 2023 UK RTS 11 Article 2(2A) lets a venue apply the tick of the third-country
   venue where the instrument was first admitted, when that tick is smaller. A finer tick is
   therefore legitimate for an overseas-primary line — and a symptom of stale data for
   anything else. The report carries `tick_below_rts11_floor` and a warning either way.
7. **Check positivity separately from the tick.** `Decimal("-3385.0") % Decimal("0.5")` is
   zero, so a negative price passes the modulo test on its own.
8. **Value the order in the quoted unit, then convert only pence.** `notional_quoted` is
   price × quantity in the quoting unit — pence for a GBX line. `notional_gbp` divides by
   100 for GBX, passes GBP through, and is `None` for every other currency.

> Full procedure: see `references/workflows.md`.
> Rule and specification citations: see `references/standards.md`.
> Printable pre-flight checklist: see `assets/checklist.md`.

## Common Pitfalls

- **Citing RTS 28 for tick sizes.** RTS 28 (Delegated Regulation (EU) 2017/576) is the
  best-execution top-five-venue report. Tick sizes are **RTS 11**, Delegated Regulation
  (EU) 2017/588, assimilated into UK law and hosted in the FCA Handbook technical standards.
  Building a control to the wrong instrument means the evidence you file cannot support it.
- **Deriving the tick from price alone.** This is the headline defect. The RTS 11 grid is
  two-dimensional; the second axis is the instrument's ADNT liquidity band. A price-only
  ladder gives Shell a 1.00 GBX tick when the Exchange quotes it in 0.5 GBX — every legal
  half-penny price is rejected by your own gateway before the Exchange ever sees it.
- **Treating the regulatory grid as the venue's tick.** RTS 11 is a floor. The binding value
  is the instrument's `Price Tick Table ID`, and a UK venue may legitimately be finer under
  Article 2(2A) or coarser under Article 2(1).
- **Assuming the liquidity band tracks your own trade counts.** The band comes from the
  FCA's published ADNT for the most relevant market in terms of liquidity, and it changes
  once a year, from the first Monday of April. A stock moves up and down the price rows
  intraday, but sideways across liquidity bands only on that date.
- **Validating TIDMs with `isalpha()`.** It rejects `BP.`, `BT.A` and `3IN` — real
  instruments, refused by your own validator.
- **Putting the TIDM on the wire.** Trading messages carry `InstrumentID` (Tag 48), not the
  mnemonic. The TIDM can also change, and when it does the instrument is deleted and
  re-added, so a TIDM cached across a corporate action can point at nothing.
- **Testing the tick in floating point.** `2650.35 / 0.05` is `53006.99999999999` and
  `205.3` is not exactly representable. Convert through the shortest repr into `Decimal`
  and test `price % tick == 0` exactly.
- **Reading a GBX notional as pounds.** `price × quantity` on a GBX line is pence. 1,000
  Shell at 3,384.5 GBX is £33,845, not £3,384,500.

## Verification

- Route a Shell order (`tidm="SHEL"`, `price=3384.5`, `quantity=1000`, `currency="GBX"`)
  ⟹ `LSE_ORDER_VALIDATED`, `applicable_tick_size == 0.5`, `notional_gbp == 33845.00`.
  The same price against the previous price-only ladder was rejected as off-tick.
- Route the same order at `price=3385.25` ⟹ `INVALID_TICK_SIZE`.
- Route it at `price=33.845, currency="GBP"` ⟹ `INVALID_CURRENCY`, with the 100× warning.
- Route `tidm="IGLN"` (USD-quoted ETC, no tick table) ⟹ `REFERENCE_DATA_REQUIRED`, not a
  guessed share tick.
- Route `tidm="SHEL.L"` ⟹ `INVALID_TIDM`; route `tidm="BT.A"` ⟹ accepted.
- Run `python -m unittest discover -s skills/lse-millennium-exchange-api/scripts`.

## Related Skills

- `exchange-tick-size-regime-tracking`
- `currency-pair-quoting-convention-normalization`
- `reference-data-symbol-mapping-across-vendors`
- `multi-currency-pnl-and-fx-conversion`
