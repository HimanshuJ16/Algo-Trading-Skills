---
name: deutsche-borse-xetra-api-integration
description: "Client-side pre-dispatch validation and T7 ETI request-header framing\
  \ for Deutsche B\xF6rse Xetra cash orders — RTS 11 tick size by liquidity band,\
  \ ETI field domains (TradingCapacity, Side, OrderOrigination), and scaled-integer\
  \ price encoding."
domain: Venue Integration & Protocols
subdomain: European Exchange Integration (Xetra/Eurex)
tags:
- xetra
- t7-eti
- deutsche-borse
- mifid-ii
- rts-11
- tick-size-regime
- binary-protocol
- european-equities
brokers_frameworks:
- "Deutsche B\xF6rse T7 ETI (Cash)"
- MiFID II RTS 11 (Delegated Regulation (EU) 2017/588)
- Python Dataclasses
version: "2.0.0"
author: algo-trading-skills-contributors
license: Apache-2.0
---

## When to Use

Use this skill when building or auditing an order path into Deutsche Börse Xetra
(MIC `XETR`) over the **T7 Enhanced Trading Interface (ETI)** — a little-endian
binary protocol with FIX 5.0 SP2 semantics. It covers the checks that must happen
on the client side, before a message leaves your process:

- Is the limit price on the **RTS 11** tick for this instrument's liquidity band?
- Are the ETI field values in their documented numeric domains (`Side`,
  `TradingCapacity`, `OrderOrigination`, short-code qualifiers)?
- Is the price encoded as the scaled integer ETI actually carries?
- Is the 24-byte ETI request header framed correctly, with a gap-free `MsgSeqNum`?

## When NOT to Use

- **Not a transport.** Nothing here opens a socket, logs on to a gateway, or sends
  an order. `ready_to_send` means "passed local validation", never "the exchange
  has it". Session establishment, throttling, heartbeats, and recovery are out of
  scope.
- **Not a full message encoder.** It frames the header, not the message body. Body
  layouts are release-specific — take them from the ETI Cash Message Reference for
  the release you are certified against.
- **Not a source of liquidity bands.** The RTS 11 band is per-instrument reference
  data you must supply from the venue's Reference Data file. This skill will not
  guess it, because it cannot be inferred from the price.
- **Not for Eurex derivatives.** Eurex runs T7 too, but with its own message
  reference, product identifiers, and account-type conventions.

## Prerequisites

- T7 ETI session and user credentials; `SenderSubID` is the T7 User ID.
- Instrument reference data: `SecurityID` (tag 48), `MarketSegmentID` (tag 1300),
  and the instrument's **RTS 11 liquidity band (1–6)**. The ISIN is for human
  readability — the wire identifies instruments numerically.
- Your firm's RTS 24 short codes, already uploaded to the venue's short/long code
  database with a valid-from date.
- The ETI Cash Message Reference for your target release.

## Workflow

1. **Resolve the liquidity band before pricing anything.** Read it from venue
   reference data. It changes annually: ESMA publishes ADNT figures and venues
   apply the new bands from the **first Monday of April** (RTS 11 Art. 3(4), as
   amended by Delegated Regulation (EU) 2023/960 — the original text said 1 April).
   On changeover, Xetra deletes resting orders whose limits no longer comply, so
   re-price or re-submit rather than assuming your book survived.
2. **Look up the tick in the RTS 11 Annex matrix — 19 price bands × 6 liquidity
   bands.** Never from price alone. At €62.50 the tick is €0.01 in band 6 but
   €0.50 in band 1; a price-only rule silently accepts prices the venue rejects for
   illiquid names.
3. **Validate field domains before the tick check.** An invalid quantity or side
   makes the tick question meaningless, so report the first real defect rather than
   a downstream symptom. `Side` (tag 54) is `1`/`2`, not `"BUY"`/`"SELL"`.
   `TradingCapacity` (tag 1815) is `1` Customer (Agency), `5` Principal
   (Proprietary), `6` Market Maker, `9` Riskless Principal, `10` Retail Customer
   (Agency) — numeric, and **not** the letters `P`/`A`/`M`.
4. **Encode the price as an integer with 8 implied decimals.** ETI `PriceType` is
   an 8-byte signed integer scaled by 10^8. Refuse a price that needs more than 8
   decimals instead of rounding it — rounding sends a price the caller did not ask
   for.
5. **Frame the header and advance `MsgSeqNum` only on success.** `BodyLen` is the
   whole message length *including the BodyLen field itself* (24 + body). A
   rejected order must not consume a sequence number, because a gap is a
   session-level fault.
6. **Pick a template that still exists.** The R14.0 change log schedules
   New Order Single (`10100`) and its short layout (`10125`) for decommissioning
   with ETI 14.1 in mid-2026, naming New Order Single or Multi Leg (`10138`) and
   `10139` as replacements. Confirm against the reference for your release.

> Full procedure: see `references/workflows.md`.
> Standards reference: see `references/standards.md`.
> Printable pre-flight checklist: see `assets/checklist.md`.

## Common Pitfalls

- **Treating the tick size as a function of price.** This is the single most
  expensive mistake here. RTS 11 indexes the tick by price band *and* liquidity
  band. A hard-coded price-only ladder accepts off-tick prices for illiquid
  instruments and reports the wrong rounding increment even where the accept/reject
  verdict happens to agree.
- **Float modulo for tick checks.** `-5.0 % 0.001 == 0.0` in Python, so a naive
  float check passes negative prices; and `0.1` as a float is
  `0.1000000000000000055…`, which is not a multiple of any tick. Use `Decimal`, and
  validate that the price is positive as its own check.
- **Sending `P`/`A`/`M` as the capacity.** Those are leading characters of the ETI
  `Account` field (tag 1, valid characters `1-9`, `A`, `G`, `M`, `P`), which books
  positions. The MiFID capacity is the numeric `TradingCapacity` (tag 1815). They
  are different fields with different purposes.
- **Assuming one "MiFID short code" field.** There are several distinct fields:
  `OrderOrigination` (1724, value `5` = order received from a direct access
  customer) flags DEA; `ExecutingTrader` (25123) with `ExecutingTraderQualifier`
  (25124, `22` Algo / `24` Human) and `PartyIdInvestmentDecisionMaker` with its
  qualifier (21222) carry the RTS 24 short codes. A short code only resolves if the
  matching long code was uploaded to the venue beforehand — unresolved combinations
  surface in the TR160/TR161/TR166 reports.
- **Burning a sequence number on a rejected order.** `MsgSeqNum` must increase by
  exactly one per request on a session.
- **Sending the ISIN as the instrument identifier.** T7 ETI uses the numeric
  `SecurityID` (tag 48) plus `MarketSegmentID` (tag 1300).
- **Conflating ETI with market data.** ETI is order entry. Level 2 depth comes from
  the separate T7 Market Data Interface (MDI) multicast feeds.
- **Copying field offsets across releases.** Offsets and field widths change —
  `OrderQty` moved from 4 to 8 bytes between Release 5.0 and Release 14.0. Only the
  header layout has held stable.

## Verification

- `rts11_tick_size("62.50", 6)` $\implies$ `Decimal("0.01")`;
  `rts11_tick_size("62.50", 1)` $\implies$ `Decimal("0.5")`. Both are read directly
  off the RTS 11 Annex row `50 ≤ price < 100`.
- Instantiate `DeutscheBorseXetraApiEngine(sender_sub_id=98765)`. Submit an order
  for `DE0007100000` at €62.50, qty 500, `liquidity_band=6`,
  `trading_capacity=5` $\implies$ `STATUS_OK`, `price_eti_int == 6_250_000_000`,
  `side_wire_value == 1`.
- The same order at €62.503 $\implies$ `INVALID_TICK_SIZE`. At €62.53 with
  `liquidity_band=1` $\implies$ `INVALID_TICK_SIZE` (tick €0.50).
- Negative or zero price, non-positive quantity, an unknown side, and a letter
  `trading_capacity` $\implies$ `INVALID_ORDER_FIELD`, and none of them consume a
  sequence number.
- `header.pack()` $\implies$ exactly 24 bytes, with `BodyLen` at offset 0 and
  `TemplateID` at offset 4, little endian.
- Run `python -m unittest discover -s skills/deutsche-borse-xetra-api-integration/scripts`.

## Related Skills

- `exchange-tick-size-regime-tracking`
- `mifid-ii-algo-trading-compliance-eu`
- `order-to-trade-ratio-fee-penalty-avoidance`
