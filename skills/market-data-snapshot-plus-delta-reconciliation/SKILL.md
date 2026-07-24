---
name: market-data-snapshot-plus-delta-reconciliation
description: >-
  Use when initializing Level 2/3 order book streams to buffer delta updates, align initial REST snapshots by sequence ID, and detect sequence gaps for automated order book re-synchronization
domain: algorithmic-trading
subdomain: real-time-architecture
tags: ["real-time-architecture", "order-book", "l2-reconciliation", "snapshot-delta", "sequence-alignment"]
brokers_frameworks: ["Binance L2 API", "Coinbase Advanced L2", "Bybit L2", "Crypto/Forex Orderbooks"]
version: "1.0"
author: algo-trading-skills-contributors
license: Apache-2.0
---

## When to Use

Invoke this whenever maintaining a local Level 2 (L2) or Level 3 (L3) order book from real-time exchange streams. Exchange market data feeds require subscribing to a WebSocket delta stream, buffering incoming deltas while fetching a REST L2 snapshot, aligning the snapshot sequence ID ($S_{\text{snap}}$), discarding stale deltas ($S \le S_{\text{snap}}$), and applying subsequent deltas sequentially ($S_{i+1} = S_i + 1$). If a sequence gap is detected, the order book state is corrupt and must be flushed and re-synced to prevent false trading signals.

## Prerequisites

- Streaming WebSocket delta feed providing `sequence_id` (or `first_update_id` / `final_update_id`), `bids` (`[[price, size], ...]`), and `asks`.
- REST API endpoint for initial full L2 order book snapshot fetch.
- Local order book data structure (sorted dictionaries or tree maps for bids/asks).

## Workflow

1. **Subscribe & Buffer WebSocket Deltas**:
   - Subscribe to L2 WebSocket delta stream. Buffer incoming delta objects in a queue before fetching the REST snapshot.

2. **Fetch REST L2 Snapshot**:
   - Query REST endpoint for full L2 snapshot containing `last_update_id` ($S_{\text{snap}}$), `bids`, and `asks`.

3. **Initialize Book & Filter Stale Deltas**:
   - Initialize local order book bids (descending) and asks (ascending) from snapshot.
   - Discard all buffered deltas where `final_update_id` $\le S_{\text{snap}}$.

4. **Apply Sequential Deltas**:
   - For the first valid delta, verify $S_{\text{first\_update}} \le S_{\text{snap}} + 1 \le S_{\text{final\_update}}$.
   - For each subsequent delta, verify strict sequence continuity: $S_{\text{new\_first}} = S_{\text{prev\_final}} + 1$.
   - Update price level quantities (set price level to size, or delete if size $= 0$).

5. **Sequence Gap Recovery Protocol**:
   - If a sequence gap is detected ($S_{\text{new\_first}} > S_{\text{prev\_final}} + 1$), mark order book state as `CORRUPT`, clear book levels, and trigger immediate REST re-snapshot.

> Full step-by-step procedure with broker-specific detail: see `references/workflows.md`.
> Broker/framework coverage table for this skill: see `references/standards.md`.
> Printable pre-flight checklist: see `assets/checklist.md`.

## Common Pitfalls

- **Applying Deltas Out of Order**: Processing WebSocket deltas without verifying strict sequence continuity ($S_{i+1} = S_i + 1$).
- **Dropping Stale Delta Filter**: Failing to discard deltas that arrived before the snapshot timestamp, corrupting snapshot price levels.
- **Ignoring Zero-Size Deletions**: Failing to remove price levels when delta volume equals zero (`qty == 0.0`), resulting in ghost order book depth.

## Verification

- Initialize `OrderBookReconciler` with mock snapshot and verify order book top-of-book bids/asks match snapshot data.
- Apply sequential deltas and verify price level updates and zero-qty deletions update the book.
- Inject sequence gap ($S_{\text{new}} = S_{\text{old}} + 5$) and confirm state transitions to `CORRUPT` and triggers re-snapshot.
- Run unit test suite `python scripts/test_order_book_reconciler.py` and confirm 100% pass rate.

## Related Skills

- `producer-consumer-tick-pipeline`
- `order-book-depth-processing-l2-l3`
- `graceful-degradation-to-polling-fallback`
---
