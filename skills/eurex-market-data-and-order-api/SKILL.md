---
name: eurex-market-data-and-order-api
description: Client-side pre-dispatch validation for Eurex derivatives orders on T7
  — the directional Price Reasonability Check against the opposite-side best price,
  contract minimum price change, ETI scaled-integer price/quantity encoding, and the
  24-byte T7 ETI request header with a gap-free MsgSeqNum.
domain: Venue Integration & Protocols
subdomain: European Derivatives (Eurex T7)
tags:
- eurex
- t7-eti
- t7-emdi
- futures-trading
- price-reasonability-check
- euro-stoxx-50
- euro-bund
- binary-protocol
brokers_frameworks:
- Eurex T7 ETI (Derivatives)
- T7 EMDI / MDI / RDI
- Python Dataclasses
version: "2.0.0"
author: algo-trading-skills-contributors
license: Apache-2.0
---

## When to Use

Use this skill when building or auditing an order path into Eurex (MIC `XEUR`)
over the **T7 Enhanced Trading Interface (ETI)** — a little-endian binary protocol
with FIX 5.0 SP2 semantics — fed by **T7 EMDI** price-level depth over UDP
multicast. It covers the checks that must happen on the client side, before a
message leaves your process:

- Would T7's **Price Reasonability Check** reject this limit price?
- Is the price on the contract's minimum price change?
- Are prices and quantities encoded as the scaled integers ETI carries?
- Is the 24-byte ETI request header framed correctly, with a gap-free `MsgSeqNum`
  and a template that still exists?

## When NOT to Use

- **Not a transport.** Nothing here opens a socket, logs on to a gateway, or sends
  an order. `ready_to_send` means "passed local validation", never "the exchange
  has it". Session logon, throttles, heartbeats and recovery are out of scope.
- **Not a FAST decoder.** EMDI carries FIX 5.0 SP2 semantics in FAST encoding.
  This module models the *book state* a decoder produces, not the wire decoding.
- **Not a full message encoder.** It frames the header, not the message body. Body
  offsets are release-specific — take them from the T7 ETI Derivatives Message
  Reference for the release you are certified against.
- **Not a source of price ranges.** The reasonability band is per-instrument
  reference data from the RDI `PriceRangeRules` message. This module will not
  guess it, because it cannot be inferred from the price.
- **Not for Xetra cash.** Deutsche Börse runs T7 for both, but the cash market has
  its own message reference, a different `TradingCapacity` domain, and the RTS 11
  tick regime instead of per-contract minimum price changes. See
  `deutsche-borse-xetra-api-integration`.
- **Not for off-book (TES) or strategy instruments.** Scope is outright simple
  instruments (`ProductComplex` 1). Off-book standardised futures strategies use a
  finer price gradation — for FESX, 0.01 index points since 24 June 2024, against
  1.0 on-book.

## Prerequisites

- A T7 ETI session and user; `SenderSubID` is the T7 User ID.
- Instrument reference data from the T7 RDI: `SecurityID` (tag 48),
  `MarketSegmentID` (tag 1300), and the instrument's `PriceRangeRules` table plus
  the product's `FastMarketPercentage`. The symbol is for human readability — the
  wire identifies instruments numerically.
- The T7 ETI Derivatives Message Reference for your target release, for body
  offsets and `BodyLen`.
- Python 3.9+. Standard library only.

## Workflow

1. **Keep an EMDI book you can trust, and know when you cannot.** EMDI is the
   *un-netted* interface — every order book change up to the configured depth, and
   every on-exchange trade individually. (MDI is the netted one; EOBI is
   order-by-order.) Depth incrementals carry a `MsgSeqNum` range per product: on a
   gap, take the message from the other live-live service (A/B carry identical
   content on different multicast addresses) before falling back to the snapshot
   feed, which links back via `LastMsgSeqNumProcessed`. A book with an unrecovered
   gap — or a crossed one — must not be used as a price reference at all.
2. **Validate field domains before anything that depends on them.** `Side` (tag 54)
   is `1`/`2`, not `"BUY"`/`"SELL"`. `TradingCapacity` (tag 1815) on Eurex
   derivatives is `1` Customer (Agency), `5` Principal (Proprietary), `6` Market
   Maker — the cash values `9` and `10` do not exist here.
3. **Check the contract's minimum price change in decimal arithmetic.** FESX is
   1 index point (EUR 10 per point); FGBL is 0.01 percent of par on a EUR 100,000
   nominal, so one full point is EUR 1,000 and one tick is EUR 10. Validate that
   the price is positive as its own check — float modulo says `-4851.0 % 1.0` is
   zero.
4. **Run the Price Reasonability Check the way T7 runs it — directionally.** The
   rejection condition is
   `Buy Limit > Reference + PriceRange(Reference)` or
   `Sell Limit < Reference − PriceRange(Reference)`.
   The reference is the **opposite-side best price** (best ask for a buy, best bid
   for a sell), not the mid, and the range is computed from the reference, never
   from the limit. A buy below the market and a sell above it never fail.
5. **Derive the range from the price range table, not from a constant.**
   `PriceRange = APR + |Reference| × PPR / 100`, with `APR`/`PPR` from the
   `PriceRangeRules` row containing the reference, scaled by
   `(1 + FastMarketPercentage / 100)` in fast or stressed markets, and never
   rounded.
6. **Decide what happens when there is no reference price.** The standard
   procedure needs both best prices with a spread inside the range; otherwise the
   non-standard procedure substitutes the last trade or theoretical price, or the
   previous day's settlement price. If none is available,
   `PriceValidityCheckType` (tag 28710) decides: `1` Optional accepts the order
   unchecked, `2` Mandatory rejects it. Choose deliberately — that is the only
   difference between the two values.
7. **Encode price and quantity as scaled integers.** ETI `PriceType` is an 8-byte
   signed integer with 8 implied decimals; `Qty` is the same width with 4. Refuse a
   value that needs more precision rather than rounding it.
8. **Frame the header and advance `MsgSeqNum` only on success.** `BodyLen` is the
   whole message *including* the `BodyLen` field itself — 280 bytes for template
   10138 on a simple instrument in Release 14.0, plus 8 per leg. The Session Logon
   is `MsgSeqNum` 1, so the first order request is 2. ETI has no sequence recovery:
   a gap or duplicate is rejected and the session disconnected, and every
   reconnection restarts at 1.
9. **Pick a template that still exists.** New Order Single (`10100`) and the other
   nine deprecated order management requests were removed from production with T7
   Release 14.1 on 18 May 2026. Use New Order Single or Multi Leg (`10138`), its
   short layout (`10139`), Replace (`10140`/`10141`) and Cancel (`10142`).

> Full procedure: see `references/workflows.md`.
> Message layouts, field domains and rule citations: see `references/standards.md`.
> Printable pre-flight checklist: see `assets/checklist.md`.

## Common Pitfalls

- **Testing reasonability symmetrically against the mid.** This is the expensive
  one, and it is wrong twice over: T7 references the opposite-side best price, and
  the check is directional. An `abs(price − mid) > band` gate rejects deep passive
  orders the venue would have accepted — exactly the resting liquidity a market
  maker is trying to post — while giving no protection the venue does not already
  give on the aggressive side.
- **Hard-coding a reasonability band.** There is no published universal band for
  any Eurex product. The range comes from the instrument's `PriceRangeRules` table
  and moves with the reference price, the product's fast-market percentage, and
  the exchange's own parameter changes.
- **Computing the range from the limit price.** T7 computes it from the reference
  price. Using the limit price makes the accepted region depend on how wrong the
  order already is.
- **Building against template 10100.** It was removed from production on
  18 May 2026. Code that still frames it produces a message the gateway rejects,
  and the failure looks like a session fault rather than an obsolete template.
- **Float modulo for the tick check.** `-4851.0 % 1.0 == 0.0`, so a naive float
  check passes negative prices. Use `Decimal`, and check positivity separately.
- **Letting a float reach the wire encoding.** `int(0.29 * 1e8)` is `28999999` —
  one wire unit below the intended price, because 0.29 is not exactly
  representable. Convert through `Decimal` and refuse values that will not scale
  exactly.
- **Burning a sequence number on a rejected order.** `MsgSeqNum` must increase by
  exactly one per request. ETI has no recovery mechanism: a gap is a disconnect,
  and the reconnection starts again at 1 with all non-persistent orders and quotes
  already mass-cancelled.
- **Retrying an order because the request timed out.** Reuse the original
  `ClOrdID` and resolve the order's state through the venue. A retry under a fresh
  identifier is a second position.
- **Sending the symbol as the instrument identifier.** T7 ETI uses the numeric
  `SecurityID` (tag 48) with `MarketSegmentID` (tag 1300). `FESX_202609` is a label
  for humans.
- **Treating an Execution Report as the trade.** Information in Execution Reports
  (8, U8) is indicative; the legally binding confirmation is the Trade Capture
  Report (AE) on the trade broadcast. Reconcile positions against those, not
  against public EMDI prints.
- **Pre-checking against an auction book.** T7 performs the Price Reasonability
  Check exclusively in instrument state Continuous. Market orders are bounded by
  the separate Market Order Matching Range instead.
- **Copying field offsets across releases.** Offsets and widths change between T7
  releases; only the 24-byte request header has held stable.

## Verification

- `PriceRangeTable` reproduces the worked examples published with the formula: for
  the table `(0–1: APR 0.10, PPR 0)`, `(1–5: APR 0, PPR 10)`, `(5+: APR 0.50, PPR 0)`,
  reference prices `0.27`, `3.50` and `7.80` give ranges `0.10`, `0.35` and `0.50`,
  and `−2.40` gives `0.24`. With `FastMarketPercentage` 100, `3.50` gives `0.70`.
- With best bid 4850 and best ask 4851 and a flat 50-point range: a BUY at 4901
  passes and at 4902 fails; a SELL at 4800 passes and at 4799 fails; a BUY at 4750
  **passes** — the regression a symmetric band introduces.
- The standard-procedure reference for a BUY is 4851 and for a SELL 4850. Neither
  is the mid (4850.5).
- Widen the spread to 4800/4900 so it exceeds the range: with no alternative
  reference price the check cannot be performed; with a last trade price of 4855
  the non-standard procedure uses 4855.
- `price_to_eti_int("4851")` $\implies$ `485_100_000_000`;
  `price_to_eti_int(0.29)` $\implies$ `29_000_000` while `int(0.29 * 1e8)` is
  `28_999_999`. `qty_to_eti_int(10)` $\implies$ `100_000`.
- `audit_eurex_tick_size("-4851", "1")` $\implies$ `False`, though
  `-4851.0 % 1.0 == 0.0`.
- Instantiate `EurexMarketDataAndOrderApiEngine(sender_sub_id=55443)`. A BUY of 10
  FESX at 4851 $\implies$ `STATUS_OK`, `contract_value_eur == 485100`,
  `side_wire_value == 1`, `eti_header.template_id == 10138`. At 4851.5 $\implies$
  `INVALID_TICK_SIZE`.
- `header.pack()` $\implies$ exactly 24 bytes: `BodyLen` 280 at offset 0,
  `TemplateID` 10138 at offset 4, `MsgSeqNum` at 16, `SenderSubID` at 20, little
  endian. The first framed request carries `MsgSeqNum` 2, because the Session Logon
  is 1.
- Rejected orders consume no sequence number, and a repeated `ClOrdID` is refused.
- A crossed local book is refused as a reasonability reference; a locked one is
  not.
- Run `python scripts/test_eurex_market_data_and_order_api.py` and confirm a 100%
  pass rate.
- Against simulation only: send one validated order and confirm T7 accepts the
  template, the `BodyLen` and the sequence number. A framing bug that unit tests
  cannot see is one where the body length does not match the release you are
  certified against.

## Related Skills

- `deutsche-borse-xetra-api-integration`
- `order-placement-idempotency`
- `sequence-number-gap-detection-for-feeds`
- `exchange-multicast-feed-handling`
- `synthetic-continuous-futures-contract-construction`
- `mifid-ii-algo-trading-compliance-eu`
