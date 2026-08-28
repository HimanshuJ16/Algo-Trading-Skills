---
name: nyse-arca-integrated-feed-handling
description: >-
  NYSE Arca Integrated Feed (XDP/Pillar) binary decoder for Add Order (100), Modify Order (101), Delete Order (102), Order Execution (103), Replace Order (104), and Add Order Refresh (106) messages, with per-symbol PriceScaleCode handling, sequence-gap detection, and L3 order book reconstruction.
domain: Market Microstructure & Latency
subdomain: Binary Feed Parsing & L3 Order Book Reconstruction
tags: ["nyse-arca", "xdp-protocol", "pillar", "integrated-feed", "binary-feed", "l3-order-book", "little-endian", "sequence-gap-detection"]
brokers_frameworks: ["NYSE Pillar Integrated Feed Client Specification v2.5", "NYSE Pillar Equities Common Client Specification v2.4k", "Python Struct Binary Unpacking", "Python Dataclasses"]
version: "2.0.0"
author: algo-trading-skills-contributors
license: Apache-2.0
---

## When to Use

Use this skill when ingesting raw binary direct market data from NYSE Arca (and the other
Pillar equities markets — NYSE, NYSE American, NYSE Chicago, NYSE National) over the
Integrated Feed, and you need a per-order (L3) book: every resting order individually
tracked by matching-engine `OrderID`, for queue-position modelling, depth reconstruction,
adverse-selection measurement, or historical book replay.

Reach for it specifically when you must decode XDP packet framing, apply the correct
per-symbol price scaling, and know — rather than assume — whether your book still matches
the exchange's.

## When NOT to Use

- **You only need top-of-book or aggregated depth.** The Integrated Feed's message volume
  and the L3 state machine are wasted effort; consume the NYSE BBO or a consolidated SIP
  feed instead.
- **You need trades, auctions, or imbalances.** This skill decodes the order-book message
  set only. Imbalance (105), Non-Displayed Trade (110), Cross Trade (111), Trade Cancel
  (112), Retail Price Improvement (114), Stock Summary (223) and Security Status (34) are
  out of scope.
- **You need transport or recovery.** Multicast joins, A/B line arbitration, and Pillar
  Request Server retransmission/refresh *requests* are not implemented here. See
  `sequence-number-gap-detection-for-feeds` and `exchange-multicast-feed-handling`.
- **Your venue is not on Pillar.** Layouts here are pinned to Pillar. The pre-Pillar Arca
  XDP spec used different message structures entirely (see Pitfalls).

## Prerequisites

- Entitlement to the NYSE Arca Integrated Feed and a UDP multicast packet source (or a
  captured replay file).
- The **specific spec revision your venue publishes**. This skill is pinned to
  *Pillar Integrated Feed Client Specification v2.5 (2022-05-16)* and *Pillar Equities
  Common Client Specification v2.4k (2024-07-25)*, recorded in `SPEC_VERSION`.
- The Symbol Index Mapping spin (Msg Type 3) for the channel, either captured at startup
  or seeded from the Symbol Index Mapping file — this is where `PriceScaleCode` and the
  ticker for each `SymbolIndex` come from.

## Workflow

1. **Seed symbol reference data before decoding any price.**
   Ingest the Msg Type 3 spin so every `SymbolIndex` has a ticker and a `PriceScaleCode`.
   The engine learns these from the wire; if a price is decoded for an unknown symbol it
   falls back to `default_price_scale_code` and counts the event in
   `prices_scaled_with_fallback`. **A non-zero fallback count means some prices may be
   off by a factor of 10 or 100 — treat it as a blocking defect, not a warning.**

2. **Decode the 16-byte packet header** (`PktSize`, `DeliveryFlag`, `NumberMsgs`,
   `SeqNum`, `SendTime`, `SendTimeNS`), little-endian.

3. **Branch on `DeliveryFlag` before touching book state.** This is a decision point, not
   a formality:
   - `1` (Heartbeat) — carries zero messages and **does not advance the sequence number**.
     Counting it as one consumed sequence manufactures a phantom gap every second.
   - `11` (Original) — the real-time stream; this is the only traffic gap detection applies to.
   - `12` (Sequence Number Reset) / `10` (Failover) — numbering restarts at 1. Reset the
     expected sequence; do **not** log a gap.
   - `13`/`15` (retransmission), `17`–`20` (refresh) — numbered on their own channels.
     Exclude them from the real-time gap counter or every refresh looks like a break.
   - `21` (Message Unavailable) — the retransmission cannot be served. The book is
     unrecoverable without a full refresh; mark it stale.

4. **Check the sequence.** Expected next = `SeqNum + NumberMsgs` of the previous
   real-time packet. On mismatch, record the gap **and mark the book stale**. A gap means
   an unknown number of Add/Modify/Delete messages were lost; the book is now wrong in
   ways no later message will correct. Do not resume trading off it until a Symbol Clear
   plus refresh has rebuilt it.

5. **Iterate exactly `NumberMsgs` messages, advancing by the wire `MsgSize`** — never by
   the size of your own struct. Read `MsgSize`/`MsgType` (4 bytes), decode only the fields
   your pinned spec defines, then jump `MsgSize` bytes. Bound every read against the
   packet end before unpacking.

6. **Apply order-book messages** (offsets from Integrated Feed v2.5):
   - **Add Order (100)**, MsgSize 39 — `SourceTimeNS`@4, `SymbolIndex`@8, `SymbolSeqNum`@12,
     `OrderID`@16 (8B), `Price`@24 (signed), `Volume`@28, `Side`@32, `FirmID`@33 (5B),
     `Reserved`@38. Insert into the book.
   - **Modify Order (101)**, MsgSize 35 — `Price`@24, `Volume`@28, `PositionChange`@32,
     `Side`@33, `Reserved`@34. Price and volume are the **new absolute values**, not
     deltas. `PositionChange == 1` means the order lost queue priority — invalidate any
     cached queue-position estimate for it.
   - **Delete Order (102)**, MsgSize 25 — `OrderID`@16, `Reserved`@24. Remove.
   - **Order Execution (103)**, MsgSize 42 — `OrderID`@16, **`TradeID`@24**, `Price`@28,
     `Volume`@32, `PrintableFlag`@36. Subtract `Volume` from the resting size; remove at
     zero. Remaining shares keep their **original** price, so never overwrite the resting
     price with the execution price.
   - **Replace Order (104)**, MsgSize 42 — `OrderID`@16, `NewOrderID`@24, `Price`@32,
     `Volume`@36, `Side`@40. Remove the old ID, insert the new one.
   - **Add Order Refresh (106)**, MsgSize 43 — note the extra leading `SourceTime`
     seconds field, so every subsequent offset shifts by 4 relative to Msg 100.

7. **Handle Symbol Clear (32) and Source Time Reference (2).** Symbol Clear means drop
   all state for that `SymbolIndex` and wait for the refresh. Msg Type 2 supplies the
   seconds component that Msg Types 100–104 omit; until one arrives, a full timestamp
   simply is not available.

8. **Classify book-desync evidence.** A Modify or Execution for an `OrderID` you have
   never seen, or an execution larger than the remaining size, means you already missed a
   message. Count these and degrade the report — do not silently absorb them.

9. **Generate the audit report.** `generate_report()` returns `FEED_PARSER_DEGRADED`
   whenever a gap, desync, skipped message, or stale book is on record.

> Full procedure: see `references/workflows.md`.
> Standards reference: see `references/standards.md`.
> Printable pre-flight checklist: see `assets/checklist.md`.

## Common Pitfalls

- **Hard-coding a `/10,000` price divisor.** XDP prices are `Numerator / 10^PriceScaleCode`,
  and `PriceScaleCode` is published *per symbol* in Msg Type 3. Pillar Equities runs
  symbols at scale codes 3, 4 **and** 6 (Common v2.4k §3.5.1). A fixed 10,000 divisor
  reports a scale-6 symbol at 100× its real price and a scale-3 symbol at 1/10 — with no
  parse error and no exception. This is the single most dangerous assumption in the feed.

- **Hard-coding message sizes, or unpacking with an exact-length struct.** The spec's own
  guidance is explicit: "clients should never hard code msg sizes in feed handlers…
  use the Msg Size field to determine where the next message in a packet begins"
  (Common v2.4k §3.1.1). Decode a *prefix* and advance by the wire `MsgSize`, so a venue
  publishing extra trailing fields still parses.

- **Assuming one layout per MsgType across spec versions.** Msg Type 100 is 31 bytes with
  a **4-byte** `OrderID` on the pre-Pillar Arca spec (v1.16b, 2016) and 39 bytes with an
  **8-byte** `OrderID` on Pillar v2.5. Msg Type 101 kept MsgSize 35 from v2.4a to v2.5
  while offset 33 changed from `Reserved` to `Side` — same length, different meaning.
  Pin the spec version and re-verify on every venue migration.

- **Forgetting `TradeID` in Msg Type 103.** `TradeID` sits at offset 24, between `OrderID`
  and `Price`. Omit it and every subsequent field shifts 4 bytes: you read `TradeID` as
  the price and the price as the executed quantity, then subtract that bogus quantity
  from the book. Order sizes silently collapse to zero and depth evaporates.

- **Synthesising a timestamp from `SourceTimeNS` alone.** Msg Types 100–104 carry only the
  nanosecond offset within the current second; the seconds come from the Source Time
  Reference message (Msg Type 2), published once a second per matching-engine partition
  (Common v2.4k §3.2). Treating the offset as an epoch timestamp dates every event to
  January 1970 and destroys any latency measurement built on it.

- **Ignoring Replace Order (104).** When an order is cancel/replaced the exchange sends
  **no** Delete — only a Replace (v2.5 §4). Skip Msg Type 104 and the original `OrderID`
  rests in your book for the remainder of the session, permanently overstating depth.

- **Counting a heartbeat as a consumed sequence number.** Heartbeat packets carry
  `NumberMsgs = 0` and do not increment the sequence (Common v2.4k §2.2). Advancing on
  them manufactures a gap alert every second and trains the desk to ignore real ones.

- **Treating refresh traffic as the real-time stream.** Refresh and retransmission packets
  carry their own numbering. Measuring them against the live channel's expected sequence
  produces a false gap on every recovery.

- **Parsing on after a sequence gap as if nothing happened.** A gap means lost
  Add/Modify/Delete messages. The book is now wrong and no later message repairs it. A
  parser that keeps reporting `SUCCESS` publishes a confidently incorrect book — worse
  than one that stops.

- **Letting a malformed datagram kill the handler.** `struct.unpack` on a truncated
  payload raises, and an unhandled raise on the multicast thread drops the whole feed.
  Bound every read, skip and count what you cannot trust.

## Verification

- Instantiate `NYSEArcaIntegratedFeedEngine`. Pack a spec-correct Add Order (MsgType 100,
  MsgSize 39) for `SPY` — `SymbolIndex` 101, `OrderID` 88001, raw price 4,500,000,
  500 shares, `Side` `'B'` — with `PriceScaleCode` 4 $\implies$ verify `price_usd == 450.00`
  and 500 resting shares. Feed the **same raw price** under `PriceScaleCode` 6
  $\implies$ verify $4.50$, proving the divisor is not hard-coded.
- Pack an Order Execution (MsgType 103, MsgSize 42) with `TradeID` 999999 and `Volume` 200
  $\implies$ verify 300 shares remain and `trade_id == 999999`. A decoder missing the
  `TradeID` field reads 999,999 as the executed size and empties the order.
- Pack a Replace Order (104) $\implies$ verify the old `OrderID` is gone, the new one rests,
  and the book size is unchanged.
- Send two packets with a sequence discontinuity $\implies$ verify a `SequenceGap` is
  recorded and `generate_report().status == 'FEED_PARSER_DEGRADED'`.
- Truncate a packet mid-payload $\implies$ verify no exception escapes, `messages_skipped`
  increments, and the report degrades.
- Run `python -m unittest discover -s skills/nyse-arca-integrated-feed-handling/scripts`.

## Related Skills

- `nasdaq-totalview-itch-feed-parsing`
- `binary-protocol-parsing-for-low-latency-feeds`
- `sequence-number-gap-detection-for-feeds`
- `market-data-snapshot-plus-delta-reconciliation`
- `historical-order-book-reconstruction-from-message-logs`
- `queue-position-modeling-for-passive-orders`
