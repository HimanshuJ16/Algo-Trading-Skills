# Workflows — nyse-arca-integrated-feed-handling

Deep procedure for decoding NYSE Arca Integrated Feed (XDP/Pillar) packets into an L3
order book. Field offsets and rule citations live in `standards.md`; this page is the
order of operations and the decision points.

Pinned to **Pillar Integrated Feed v2.5 (2022-05-16)** and **Pillar Equities Common
v2.4k (2024-07-25)**.

---

## 0. Before the first packet: pin the spec, seed the symbols

1. Confirm the spec revision your venue publishes and compare it against `SPEC_VERSION`.
   A mismatch is a blocking issue, not a warning — see `standards.md` § "Layout drift".
2. Ingest the Symbol Index Mapping spin (Msg Type 3) for every channel you subscribe to,
   either from the startup spin, from a Symbol Index Mapping Request (Msg Type 13) to the
   Pillar Request Server, or from the daily Symbol Index Mapping file.
3. Verify `prices_scaled_with_fallback == 0` after a warm-up window. Any non-zero value
   means at least one symbol's `PriceScaleCode` was never learned and its prices are
   scaled by a guess.

`SymbolIndex` values are per-market and cannot be used to cross-reference a security
between markets (Common v2.4k §4.3). Do not share a mapping table across venues.

---

## 1. Packet header

Decode 16 bytes little-endian: `PktSize`(2) `DeliveryFlag`(1) `NumberMsgs`(1) `SeqNum`(4)
`SendTime`(4) `SendTimeNS`(4).

Validation before anything else:

- Datagram shorter than 16 bytes → not an XDP packet; raise.
- `PktSize > 1400` → beyond the spec maximum; log, treat as suspect.
- `PktSize != len(datagram)` → log. If `PktSize` is the smaller of the two, bound the
  message loop at `PktSize` so trailing bytes are never parsed as an extra message.

---

## 2. Delivery-flag branch

This runs **before** sequence checking and before any book mutation.

```
DeliveryFlag == 1   Heartbeat        -> NumberMsgs is 0; return. Do NOT advance sequence.
DeliveryFlag == 11  Original         -> real-time stream; gap detection applies.
DeliveryFlag == 12  Sequence Reset   -> numbering restarts at 1; reset expected, no gap.
DeliveryFlag == 10  Failover         -> as above; a refresh spin follows.
DeliveryFlag in {13,15}  Retransmission -> own numbering; exclude from gap detection.
DeliveryFlag in {17,18,19,20} Refresh -> own numbering; exclude from gap detection.
DeliveryFlag == 21  Message Unavailable -> retransmission cannot be served.
                                          Book is unrecoverable; mark stale.
```

Getting this branch wrong is the most common source of false gap alerts: a handler that
advances the expected sequence on every heartbeat raises an alarm once per second, and one
that measures refresh packets against the live channel raises one on every recovery.

---

## 3. Sequence check

Sequence numbers are per channel and are not carried per message. The packet header holds
the sequence of the **first** message plus `NumberMsgs`, so:

```
expected_next = SeqNum + NumberMsgs        (for the packet just consumed)
message i in the packet has sequence SeqNum + i
```

On the first real-time packet, adopt `SeqNum + NumberMsgs` without judging it — you joined
mid-stream.

On mismatch:

- `received > expected` → **gap**: `received - expected` messages were lost.
- `received < expected` → **replay or duplicate**: the same numbers are arriving twice.

Either way, **mark the book stale**. This is the decision point that matters: a gap means
an unknown set of Add/Modify/Delete messages never arrived, so the book is now wrong in
ways no subsequent message will repair. Continuing to publish it as authoritative is the
failure mode this skill exists to prevent. Recovery is a Symbol Clear plus refresh (or a
retransmission request to the Pillar Request Server); see
`sequence-number-gap-detection-for-feeds`.

---

## 4. Message loop

Iterate exactly `NumberMsgs` times:

1. Bounds-check `offset + 4 <= end`; otherwise the packet is truncated — count and stop.
2. Decode `MsgSize`(2) `MsgType`(2).
3. **`MsgSize < 4`** → the cursor cannot advance; re-reading the same bytes for every
   remaining message would produce garbage. Abandon the packet.
4. **`offset + MsgSize > end`** → the message claims more bytes than remain. Count and stop.
5. Decode only the fields the pinned layout defines, from the **head** of the payload.
6. Advance `offset += MsgSize` — the wire value, never `sizeof(your struct)`.

### Why prefix decoding, not exact-length unpacking

Common v2.4k §3.1.1 states clients "should never hard code msg sizes" and §3 warns that
"the length of a message as actually published may differ from the length of the message
structure defined in the client specifications." A newer revision appending trailing
fields must not break an unmodified client.

So:

- payload **longer** than the pinned layout → decode the prefix, ignore the tail, advance
  by `MsgSize`. Correct.
- payload **shorter** than the pinned layout → this is a *different* variant (an older
  spec, or another market). Reject and count it. Re-interpreting it as the pinned layout
  would silently produce wrong prices and sizes — exactly the pre-Pillar Arca v1.16b case
  where `OrderID` is 4 bytes rather than 8.

---

## 5. Applying order-book messages

### Add Order (100)

Insert `OrderID -> {symbol, side, volume, price, firm}`. `Side` must be `'B'` or `'S'`;
anything else means the layout is wrong or the packet is corrupt — do not add the order.

A repeated `OrderID` is legal: a previously displayed order that routed away and returned
unexecuted with no residual is re-added under the same ID (IF v2.5 §2). It can also mask a
missed Delete, so log it at debug.

### Modify Order (101)

`Price` and `Volume` are the **new absolute values**, not deltas (IF v2.5 §3). Overwrite,
never subtract.

`PositionChange == 1` means the order lost its place in the queue. Any cached queue-position
estimate for that order is now invalid — see `queue-position-modeling-for-passive-orders`.
Per the spec, a price change always costs position; an unchanged price always keeps it.

`Volume == 0` leaves nothing resting; remove the entry.

A Modify for an `OrderID` you do not hold means you already missed its Add. Count it as a
desync event.

### Delete Order (102)

Remove the entry. A Delete for an absent `OrderID` is **not** necessarily a desync — a
fully executed order was already removed by the execution handler. Log at debug, do not
degrade the report on this alone.

Note two silent-removal cases the feed does **not** signal with a Delete:
- A replaced order (Replace Order 104 is sent instead).
- End-of-day close: Security Status `'X'` is sent and unexecuted orders are cancelled with
  no explicit Delete messages (IF v2.5 §4). A handler that never clears at session end
  carries stale orders into the next day.

### Order Execution (103)

`TradeID` sits at offset 24, **between** `OrderID` and `Price`. Decode it, or every field
after it shifts by four bytes.

Subtract `Volume` from the resting size. Remove at zero. Per IF v2.5 §5 the remaining
shares keep their **original** price, so never write the execution price back onto the
resting order.

`Volume > remaining` is impossible on a correctly tracked book. If you see it, you already
missed an Add or Modify: count a desync, remove the order, and mark the book stale.

`PrintableFlag == 0` marks executions not printed to the SIP, including auction trades —
exclude them when computing printed volume, or auction volume double-counts.

### Replace Order (104)

Remove `OrderID`, insert `NewOrderID` with the new price and volume, inheriting symbol,
side and attribution from the sitting order (IF v2.5 §6). **No Delete accompanies this** —
skipping Msg Type 104 leaks the original order into the book for the rest of the session.

### Add Order Refresh (106)

Same semantics as Add Order, but the layout carries an extra leading `SourceTime` seconds
field, shifting every subsequent offset by 4. It arrives on the refresh channels during a
requested refresh or after a Symbol Clear.

---

## 6. Control messages

### Source Time Reference (2)

Captures the seconds component for subsequent `SourceTimeNS` offsets on that partition.
Until one has been seen, messages 100–104 have **no** resolvable absolute timestamp.
Report the offset and a null timestamp; do not fabricate one from the nanoseconds alone.

### Symbol Index Mapping (3)

Learn `SymbolIndex -> (ticker, PriceScaleCode)`. If more than one mapping arrives for the
same symbol in a trading day, the spec's guidance is that the mapping applies from that
point — re-read Common v2.4k §3.6.3 before caching across the day.

### Symbol Clear (32)

Drop **all** state for that `SymbolIndex` and await the refresh (Common v2.4k §4.4).
Clearing the whole book instead of one symbol destroys unaffected symbols' state; clearing
nothing leaves pre-failover orders the exchange will never delete.

### Sequence Number Reset (1)

Start-of-day or post-failure restart. A full refresh spin follows; rebuild from it. Any
cached Source Time Reference second is stale after a reset.

---

## 7. Audit report

`generate_report()` must report `FEED_PARSER_DEGRADED` if **any** of these is on record:
sequence gaps, book-desync events, skipped messages, or a stale book. A parser that always
reports success is not an audit trail.

Surface alongside it: `prices_scaled_with_fallback` (unknown `PriceScaleCode`),
`unhandled_message_types` (informational — types outside this skill's scope), and the
`spec_version` the decode was performed under.

---

## 8. Out of scope

Transport (multicast group joins, A/B line arbitration, NIC timestamping), Pillar Request
Server retransmission/refresh **requests**, and the non-book message set — Imbalance (105),
Non-Displayed Trade (110), Cross Trade (111), Trade Cancel (112), Cross Correction (113),
Retail Price Improvement (114), Stock Summary (223), Security Status (34).

Security Status in particular matters for a production book: halts and the end-of-day `'X'`
status change what the absence of Delete messages means. Handle it before running this
decoder unattended across a session boundary.
