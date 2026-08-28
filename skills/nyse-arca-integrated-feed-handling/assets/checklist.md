# Pre-Flight Checklist — NYSE Arca Integrated Feed (XDP/Pillar) Handler

Sign off before an L3 book built from this feed drives any trading decision.
Rule citations: `references/standards.md`.

## Spec pinning

- [ ] The spec revision the venue publishes is recorded, and matches `SPEC_VERSION`
      (pinned: Integrated Feed v2.5, Common v2.4k).
- [ ] Layouts were re-verified after the most recent venue/spec migration.
      *Msg 100 was 31 bytes with a 4-byte OrderID pre-Pillar and is 39 bytes with an
      8-byte OrderID on v2.5. Msg 101 kept MsgSize 35 while offset 33 changed from
      Reserved to Side — length alone does not identify a layout.*

## Framing

- [ ] All binary fields decoded **little-endian** (`<`), never `>` or native.
- [ ] Packet header read as 16 bytes; `PktSize` understood to **include** the header.
- [ ] Message header read as 4 bytes; `MsgSize` understood to **include** the header.
- [ ] The loop advances by the wire `MsgSize`, never by a hard-coded or struct-derived size.
- [ ] A payload **longer** than the pinned layout still decodes (trailing fields ignored).
- [ ] A payload **shorter** than the pinned layout is rejected and counted, not re-interpreted.
- [ ] `MsgSize < 4` abandons the packet instead of re-reading the same bytes.
- [ ] Every read is bounds-checked against the packet end; a truncated datagram is skipped
      and counted, not raised onto the multicast thread.
- [ ] Exactly `NumberMsgs` messages are iterated — not just the first.

## Prices

- [ ] Price is computed as `Numerator / 10^PriceScaleCode`, **per symbol**.
- [ ] `PriceScaleCode` is sourced from Symbol Index Mapping (Msg Type 3, offset 24),
      not assumed.
- [ ] `prices_scaled_with_fallback == 0` after warm-up.
      *Non-zero means a symbol's scale code was never learned — its prices may be wrong by
      a factor of 10 or 100. Pillar Equities runs scale codes 3, 4 and 6.*
- [ ] Price fields decoded as **signed** 32-bit integers.

## Message layouts

- [ ] Msg 100: `OrderID` is **8 bytes** at offset 16; `FirmID` is **5 bytes** at 33.
- [ ] Msg 103: **`TradeID` at offset 24 is decoded**, so `Price`@28 and `Volume`@32 land
      correctly. *Omit it and the executed size is read from the wrong field.*
- [ ] Msg 101: `Price`/`Volume` applied as **absolute new values**, not deltas.
- [ ] Msg 101: `PositionChange == 1` invalidates any cached queue-position estimate.
- [ ] Msg 103: the resting order keeps its **original** price; the execution price is not
      written back.
- [ ] Msg 104 (Replace Order) is handled. *A replaced order receives no Delete — skipping
      104 leaks the old OrderID into the book for the session.*
- [ ] Msg 106 (Add Order Refresh) accounts for its extra leading `SourceTime` field.
- [ ] `Side` is validated as `'B'` or `'S'`; anything else keeps the order off the book.

## Timestamps

- [ ] `SourceTimeNS` is treated as a nanosecond **offset**, not an epoch timestamp.
- [ ] The seconds component comes from Source Time Reference (Msg Type 2).
- [ ] Before the first Msg Type 2, timestamps are reported as unavailable rather than
      fabricated. *A synthesised timestamp dates every event to January 1970.*

## Feed integrity

- [ ] Expected next sequence = `SeqNum + NumberMsgs` of the previous real-time packet.
- [ ] Heartbeats (`DeliveryFlag = 1`, `NumberMsgs = 0`) do **not** advance the sequence.
- [ ] Sequence Reset (12) and Failover (10) restart numbering without logging a gap.
- [ ] Refresh (17–20) and retransmission (13, 15) traffic is excluded from gap detection.
- [ ] `DeliveryFlag = 21` (Message Unavailable) marks the book unrecoverable.
- [ ] A detected gap marks the book **stale** and blocks downstream consumption.
- [ ] Symbol Clear (Msg Type 32) clears **only** the named `SymbolIndex`, then awaits refresh.

## Desync detection

- [ ] Modify or Execution for an unknown `OrderID` is counted as a desync event.
- [ ] Execution volume exceeding remaining size is counted, and the order removed.
- [ ] A Delete for an absent order is **not** counted as a desync (full executions are
      already removed).
- [ ] End-of-day is handled: Security Status `'X'` cancels unexecuted orders with **no**
      Delete messages, so the book must be cleared at session end.

## Audit

- [ ] `generate_report()` returns `FEED_PARSER_DEGRADED` on any gap, desync, skipped
      message, or stale book — never an unconditional success.
- [ ] The report records `spec_version`, `sequence_gaps`, `messages_skipped`,
      `book_desync_events`, `prices_scaled_with_fallback`, and `unhandled_message_types`.
- [ ] One engine instance per channel. *Sequence numbers are per channel; the engine is
      not thread-safe.*
- [ ] Anomalies are logged through the module logger, not printed.
