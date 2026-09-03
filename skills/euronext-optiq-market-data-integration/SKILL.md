---
name: euronext-optiq-market-data-integration
description: Use when building or auditing a Euronext Optiq Market Data Gateway (MDG)
  feed handler - parsing the Market Data Packet Header and SBE message framing, detecting
  Packet Sequence Number gaps across the A/B multicast lines, maintaining the aggregated
  limit book, and gating quoting on the venue's Book State and Order Entry Qualifier.
domain: Venue Integration & Protocols
subdomain: European Market Data (Euronext Optiq)
tags:
- euronext
- optiq-mdg
- sbe-binary
- multicast-feed
- packet-sequence-number
- line-arbitration
- l2-order-book
- market-microstructure
brokers_frameworks:
- Euronext Optiq MDG
- Simple Binary Encoding (SBE)
- Python struct / dataclasses
version: "2.0.0"
author: algo-trading-skills-contributors
license: Apache-2.0
---

## When to Use

Use this skill when writing or reviewing a feed handler for **Euronext Optiq Market Data Gateway (MDG)** — the UDP multicast market data feed for Euronext Amsterdam, Brussels, Dublin, Lisbon, Milan, Oslo and Paris. It covers the parts of the protocol that are stable across SBE template versions and that a handler gets wrong in ways that silently corrupt a book: the fixed 16-byte packet header, message framing, Packet Sequence Number continuity across the A and B lines, price scaling, aggregated-limit book maintenance, and the trading-state gate that decides whether quoting is safe.

All wire facts here come from the Euronext *Optiq MDG Messages — Interface Specification* v6.362.3 (9 Feb 2026, SBE template version 362). See `references/standards.md` for the field-level citations.

## When NOT to Use

- **As a substitute for the SBE template XML.** Only the packet header (section 4.2) and the Frame + SBE header (section 4.3) are fixed-layout. Message *blocks* and their repeating sections are defined by the SBE template Euronext publishes per segment and version, are versioned with `sinceVersion`/`deprecated` attributes, and must be decoded from that template. Hard-coding a block layout breaks on the next template release. This module deliberately does not do it.
- **For market-by-order books.** Cash central-order-book instruments are also published order-by-order via Order Update (1002), keyed on Order Priority with explicit priority-loss semantics. This module maintains aggregated limits (Market Update, 1001) only.
- **For Optiq order entry.** MDG is market data only; orders go over the Optiq Order Entry Gateway (OEG), a separate interface and separate certification.
- **For MDG Lite.** MDG Lite is TCP with a different 15-byte header and publishes no Packet Sequence Number, so the gap-detection logic here does not apply to it.
- **As a latency-optimised production handler.** This is a correctness reference in Python: dict-based book, per-packet allocation. A colocated handler needs a preallocated, cache-friendly structure.

## Prerequisites

- The **SBE template XML** for your segment and the SBE version you are certified against, plus the feed configuration file identifying the A and B multicast groups per channel.
- **Price/Index Level Decimals** for every instrument you subscribe to, from Standing Data (1007) or the Standing Data file. There is no per-message decimal count and no safe default.
- Symbol Index (uint32) for each instrument — the feed's identifier. ISIN and trading code arrive in Standing Data, not in book updates.
- A snapshot-channel consumer, because a packet lost on both lines is recoverable only from the snapshot.
- LZ4 block-mode decompression if you subscribe to a compressed (100 Mbps) or snapshot channel.

## Workflow

1. **Parse the Market Data Packet Header (16 bytes, little-endian)**:
   - `uint64` Packet Time (ns since epoch UTC), `uint32` Packet Sequence Number, `uint16` Packet Flags, `uint16` Channel ID.
   - **Decision point — read the flags before the body.** Bit 0 says the body is LZ4-compressed (the header never is); bits 1–3 are the MDG restart counter; bits 4–6 are the high-order bits that extend the PSN to 35 bits. Ignoring bits 4–6 makes the sequence appear to wrap to zero and manufactures a false gap.

2. **Check continuity on the Packet Sequence Number, not the Market Data Sequence Number**:
   - The PSN is per channel and increments by 1. The MDSN is managed per aggregator, so on any single channel it increments *unevenly* and repeats as `0` inside snapshots — using it for gap detection produces constant false positives.
   - **Decision point — classify before reacting.** A PSN below the last seen one is a reordered or duplicated UDP packet, not a gap; drop it and keep the high-water mark. A PSN above `last + 1` is a real gap: recover those packets from the other line, and only if both lines dropped them, resynchronize from the snapshot channel.
   - **Decision point — a change in flag bits 1–3 is an MDG restart, not a rollback.** The PSN restarts at 1 and a book retransmission follows. Treat the book as unusable and rebuild it; do not interpret the low PSN as reordering.

3. **Walk the packet body message by message**:
   - Each message is `uint16` Frame (total message length, this header included) + 8-byte SBE header (Block Length, Template ID, Schema ID, Schema Version) + block + optional repeating sections. A repeating-section header is 2 bytes: length byte then count byte.
   - The Frame lengths must sum to the body length; if they do not, the packet is corrupt — discard the whole packet rather than the tail.

4. **Scale prices with the instrument's decimals**: `price = integer / 10^(Price/Index Level Decimals)`. Quantities, ratios and amounts have their own decimal fields. A null price is `-2^63`, **not** zero — zero is a legitimate price on some order books.

5. **Maintain the aggregated limit book from Market Update (1001)**:
   - New/updated bid or offer → insert or replace the limit at that price. **A quantity of 0 is the deletion signal** for that price. Update type 254 (Clear Book) drops every limit.
   - **Decision point — never mix BBO updates with full-depth limits in one book.** Euronext states plainly that processing both makes the book appear crossed. Pick the BBO channel or the full-depth channel and build from one of them.

6. **Gate quoting on Market Status Change (1005), not on inference**:
   - Book State: 1 Inaccessible, 2 Closed, 3 Call, 4 Uncrossing, 5 Continuous, 6 Halted, 7 Continuous Uncrossing, 8 Suspended, 9 Reserved. Order Entry Qualifier: 0 Disabled, 1 Enabled, 2 Cancel and Modify Only, 3 Cancel Only.
   - **Decision point — the two fields answer different questions.** Book State says whether the engine is matching; Order Entry Qualifier says whether the venue will accept your message at all. A market maker needs both, and both are optional on the wire — absent means unchanged, `255` means null.
   - **Decision point — a crossed book means different things in different phases.** During Call the engine collects orders without matching, so a crossed book is normal; during Continuous it is evidence that the book is wrong (a missed packet, or BBO mixed with depth) and quoting must stop.

7. **Emit the audit record**: `OptiqMarketDataAuditReport` carries the book state, the qualifier, synchronization status, crossed flag and the derived mid/spread/imbalance, with `is_quoting_allowed` as the conjunction of all four safety conditions.

> Full procedure: see `references/workflows.md`.
> Standards reference: see `references/standards.md`.
> Printable pre-flight checklist: see `assets/checklist.md`.

## Common Pitfalls

- **Detecting gaps on the Market Data Sequence Number.** It is a per-aggregator sequence: a client subscribed to a subset of an aggregator's channels sees it jump by arbitrary amounts, and snapshot messages can share MDSN `0`. Only the PSN in the packet header is contiguous per channel.
- **Quoting into a book with a known hole.** A gap on both lines leaves the book stale in a way no later update repairs — the missing packet may have deleted the limit you are now quoting against. Freeze quoting until a snapshot or book retransmission rebuilds it; a "resume when updates look normal again" heuristic resumes against a corrupt book.
- **Treating a low PSN after an MDG restart as packet reordering.** After failover the PSN restarts at 1 and flag bits 1–3 increment. A handler that only compares sequence numbers concludes "old packet, ignore" and then silently ignores the entire post-restart session.
- **Mis-scaling prices.** Price/Index Level Decimals is per instrument and arrives only in Standing Data. Assuming 2 decimals for an instrument quoted with 4 reports EUR 785.00 as EUR 78 500.00 and every derived signal with it. Issue Price and Strike Price use their own decimal fields.
- **Confusing the null price with zero.** Optiq sends `-2^63` for priceless (Market / Market-to-Limit) orders and when clearing a side. Coercing it to `0.0` inserts a phantom limit at zero that becomes the best bid.
- **Missing that quantity 0 means delete.** A handler that ignores zero-quantity updates keeps executed or cancelled limits forever, so the book only ever grows and the top of book stays permanently stale.
- **Building the book from BBO and depth messages together.** They describe the same book at different granularities; interleaving them produces a crossed book, as the specification warns explicitly.
- **Assuming Call phase means "no orders".** Order entry is generally *enabled* during Call — orders are collected without matching. Whether to quote into an auction is a strategy decision; do not encode it as a protocol fact in either direction.
- **Ignoring the packet-flag high bits on the PSN.** The PSN is 32 bits in the header and 35 bits in effect. A handler that reads only the header field sees the counter return to zero on rollover and declares a four-billion-packet gap.
- **Reusing one engine instance across instruments or channels.** The PSN sequence is per channel and the price decimals are per instrument; sharing state mixes two books and two sequences.

## Verification

- Instantiate `EuronextOptiqMarketDataEngine(price_decimals=4)`, call `mark_book_synchronized()`, then `apply_market_status_change(book_state=5, order_entry_qualifier=1)`. Apply LVMH (`FR0000121014`) limits — bid `7_850_000` @ 500, ask `7_855_000` @ 200 — and verify the report gives best bid EUR 785.00, best ask EUR 785.50, mid EUR 785.25, spread EUR 0.50, imbalance `+0.4286`, and `is_quoting_allowed` true.
- Apply `apply_market_status_change(book_state=6)` (Halted) and verify `trading_status == "HALTED"` and `is_quoting_allowed` false; repeat for Reserved (9) and Suspended (8).
- Feed packet headers with PSN 101 then 105 and verify `gap_size == 3` and that the book is marked unsynchronized and quoting blocked until `mark_book_synchronized()` is called again.
- Feed PSN 5000 then PSN 1 with flag bits 1–3 incremented and verify the restart is reported as a restart, not as reordering.
- Negative checks: a packet shorter than 16 bytes, a Frame shorter than 10 bytes or longer than 1384, a body whose Frames overrun it, a negative quantity, an unknown side, an unknown Book State value, and constructing the engine without `price_decimals` must each raise.
- Run `python -m unittest discover -s skills/euronext-optiq-market-data-integration/scripts` and confirm a 100% pass rate.

## Related Skills

- `sequence-number-gap-detection-for-feeds`
- `exchange-multicast-feed-handling`
- `market-data-snapshot-plus-delta-reconciliation`
- `binary-protocol-parsing-for-low-latency-feeds`
- `eurex-market-data-and-order-api`
- `deutsche-borse-xetra-api-integration`
