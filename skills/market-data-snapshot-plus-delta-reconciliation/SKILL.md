---
name: market-data-snapshot-plus-delta-reconciliation
description: Use when initializing Level 2/3 order book streams to buffer delta updates,
  align initial REST snapshots by sequence ID, and detect sequence gaps for automated
  order book re-synchronization
domain: algorithmic-trading
subdomain: real-time-architecture
tags:
- real-time-architecture
- order-book
- l2-reconciliation
- snapshot-delta
- sequence-alignment
brokers_frameworks:
- Binance L2 API
- Coinbase Advanced L2
- Bybit L2
- Crypto/Forex Orderbooks
version: "1.1.0"
author: algo-trading-skills-contributors
license: Apache-2.0
---

## When to Use

Invoke this whenever maintaining a local Level 2 (L2) or Level 3 (L3) order book from real-time exchange streams. Exchange market data feeds require subscribing to a WebSocket delta stream, buffering incoming deltas while fetching a REST L2 snapshot, aligning the snapshot sequence ID ($S_{\text{snap}}$), discarding stale deltas ($S \le S_{\text{snap}}$), and applying subsequent deltas sequentially ($S_{i+1} = S_i + 1$). If a sequence gap is detected, the order book state is corrupt and must be flushed and re-synced to prevent false trading signals.

## When NOT to Use

- **Feeds with no per-book update ID.** Coinbase Advanced Trade's `sequence_num` is a *per-connection message counter*, documented for detecting dropped or out-of-order WebSocket messages. It orders the message stream but does not version the order book, so there is nothing to align a REST snapshot against — use it as a gap detector, not as $S_{\text{snap}}$.
- **Venues that push their own snapshot.** Bybit v5 `orderbook` and Coinbase Advanced Trade `level2` deliver the snapshot over the WebSocket on subscribe. Resynchronization there means handling the venue's snapshot message (for Bybit, `u == 1` signals a service restart and an unconditional local overwrite), not issuing a REST fetch.
- **Sequenced UDP / multicast feeds with retransmission services** (Nasdaq ITCH over MoldUDP64, CME MDP 3.0). Recovery is a retransmission or replay request against the venue's recovery channel, not a REST re-snapshot — see `sequence-number-gap-detection-for-feeds`.
- **L1 / top-of-book or trade-print feeds.** These carry no incremental book state to reconcile.
- **As a reconnection handler.** This skill assumes a live subscription. Re-establishing the socket, re-subscribing, and rebuilding subscription state belong to `websocket-reconnection-with-state-recovery`; this skill takes over once deltas are flowing again.

## Prerequisites

- Streaming WebSocket delta feed providing `sequence_id` (or `first_update_id` / `final_update_id`), `bids` (`[[price, size], ...]`), and `asks`.
- REST API endpoint for initial full L2 order book snapshot fetch. **Venue-dependent**: Binance spot and USD-M futures require this REST fetch, but Coinbase Advanced Trade `level2` and Bybit v5 `orderbook` push the snapshot over the WebSocket itself — check `references/standards.md` before building a REST snapshot path the venue does not expose.
- Local order book data structure (sorted dictionaries or tree maps for bids/asks).
- A bounded buffer size for pre-snapshot deltas, so a snapshot that never arrives fails loudly instead of exhausting feed-handler memory.

## Workflow

1. **Subscribe & Buffer WebSocket Deltas**:
   - Subscribe to L2 WebSocket delta stream. Buffer incoming delta objects in a queue before fetching the REST snapshot.

2. **Fetch REST L2 Snapshot**:
   - Query REST endpoint for full L2 snapshot containing `last_update_id` ($S_{\text{snap}}$), `bids`, and `asks`.

3. **Initialize Book & Filter Stale Deltas**:
   - Initialize local order book bids (descending) and asks (ascending) from snapshot.
   - Discard all buffered deltas where `final_update_id` $\le S_{\text{snap}}$.

4. **Validate Snapshot Freshness Against the Buffer**:
   - Inspect the first *surviving* buffered delta. It must straddle the snapshot: $S_{\text{first\_update}} \le S_{\text{snap}} + 1 \le S_{\text{final\_update}}$.
   - If it starts later ($S_{\text{first\_update}} > S_{\text{snap}} + 1$), the snapshot predates the delta stream and the updates in between were never observed by either source. Do **not** mark the book synchronized — discard the snapshot, keep the buffer, and fetch a fresher snapshot (Binance spot procedure, step 4).
   - Classify the cause before recovering: a stale *snapshot* is repaired by re-fetching the snapshot alone; a gap *inside the buffer* means the WebSocket stream itself dropped messages, so the subscription must be restarted and re-buffered.

5. **Apply Sequential Deltas**:
   - Run buffered deltas through the same continuity check as live deltas — a buffer applied blindly hides exactly the gap the live path would have caught.
   - For each delta, verify strict sequence continuity: $S_{\text{new\_first}} = S_{\text{prev\_final}} + 1$.
   - Update price level quantities (set price level to size, or delete if size $= 0$). Quantities are absolute level sizes, never increments.

6. **Sequence Gap Recovery Protocol**:
   - If a sequence gap is detected ($S_{\text{new\_first}} > S_{\text{prev\_final}} + 1$), mark order book state as `CORRUPT` and clear book levels so no stale depth can be read, then trigger an immediate re-snapshot.
   - Keep buffering deltas throughout the re-sync, retaining the offending delta as the new buffer head. Deltas dropped while the book is `CORRUPT` destroy the evidence step 4 needs to prove the replacement snapshot is fresh enough.

> Full step-by-step procedure with broker-specific detail: see `references/workflows.md`.
> Broker/framework coverage table for this skill: see `references/standards.md`.
> Printable pre-flight checklist: see `assets/checklist.md`.

## Common Pitfalls

- **Applying Deltas Out of Order**: Processing WebSocket deltas without verifying strict sequence continuity ($S_{i+1} = S_i + 1$).
- **Dropping Stale Delta Filter**: Failing to discard deltas that arrived before the snapshot timestamp, corrupting snapshot price levels.
- **Ignoring Zero-Size Deletions**: Failing to remove price levels when delta volume equals zero (`qty == 0.0`), resulting in ghost order book depth.
- **Accepting a Snapshot Older Than the Buffer**: Applying a REST snapshot whose `last_update_id` falls before the first buffered `first_update_id`. Every later continuity check then passes, so the book is permanently wrong and never reports a gap — the most dangerous failure mode here precisely because it is silent.
- **Trusting the Buffer Because It Was Buffered**: Applying buffered deltas without continuity checks. The buffer is exactly as gap-prone as the live stream; it just fails earlier and more quietly.
- **Dropping Deltas During Re-Sync**: Discarding messages while the book is `CORRUPT`. The replacement snapshot then has nothing to be freshness-checked against, so a stale re-snapshot is accepted as healthy.
- **Unbounded Pre-Snapshot Buffering**: Letting the delta queue grow without limit while waiting for a snapshot that never arrives, exhausting memory in the feed handler process.
- **Trading a Crossed Local Book**: Publishing top-of-book where best bid $\ge$ best ask. Venues do not disseminate crossed books, so a crossed *local* book is proof of a missed or misapplied delta, not an arbitrage opportunity.
- **Inconsistent Price Parsing**: Keying levels by `float` while parsing the venue's decimal price strings inconsistently, so one price level splits into two dict keys and neither is ever deleted.

## Verification

- Initialize `OrderBookReconciler` with mock snapshot and verify order book top-of-book bids/asks match snapshot data.
- Apply sequential deltas and verify price level updates and zero-qty deletions update the book.
- Inject sequence gap ($S_{\text{new}} = S_{\text{old}} + 5$) and confirm state transitions to `CORRUPT`, book levels are cleared, and the offending delta is retained as the new buffer head.
- Buffer a delta stream starting at $S_{\text{first\_update}} = 1010$, then apply a snapshot with $S_{\text{snap}} = 1000$; confirm the snapshot is **rejected** rather than silently accepted, and that re-applying with $S_{\text{snap}} = 1009$ then succeeds.
- Buffer two non-contiguous deltas (1001, then 1005) and confirm `apply_snapshot` rejects the buffer instead of applying it.
- Drive a full gap $\rightarrow$ re-buffer $\rightarrow$ fresh-snapshot cycle and confirm the book returns to `SYNCHRONIZED` with the post-gap deltas applied.
- Run unit test suite `python -m unittest discover -s skills/market-data-snapshot-plus-delta-reconciliation/scripts` and confirm 100% pass rate.

## Related Skills

- `producer-consumer-tick-pipeline`
- `order-book-depth-processing-l2-l3`
- `sequence-number-gap-detection-for-feeds`
- `websocket-reconnection-with-state-recovery`
- `graceful-degradation-to-polling-fallback`
---
