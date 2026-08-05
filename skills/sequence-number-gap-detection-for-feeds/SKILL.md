---
name: sequence-number-gap-detection-for-feeds
description: >-
  Production-grade monotonic sequence tracker, out-of-order buffer manager, and order book sync guard engine detecting packet drops in market data feeds (Nasdaq ITCH, CME MDP 3.0, Binance WebSocket) and triggering retransmission recovery.
domain: Market Data & Messaging Protocols
subdomain: Feed Gap Detection & State Reconciliation
tags: ["gap-detection", "sequence-number", "itch-protocol", "udp-packet-loss", "order-book-sync", "retransmission"]
brokers_frameworks: ["Nasdaq ITCH Protocol", "MoldUDP64", "Python Dataclasses"]
version: "1.0.0"
author: algo-trading-skills-contributors
license: Apache-2.0
---

## When to Use

Use this skill when processing high-frequency market data feeds (Nasdaq ITCH, CME MDP 3.0, Binance WebSocket) where packets can be dropped, delayed, or delivered out-of-order due to network UDP loss or congestion. Undetected sequence gaps corrupt local order book state, resulting in phantom orders, incorrect price quotes, and bad trade execution. This engine tracks expected sequence IDs per channel, buffers out-of-order frames, detects missing ranges, and reconciles state via TCP retransmission or snapshot services.

## Prerequisites

- Feed frame payload (`FeedFrame`: `symbol`, `sequence_id`, `payload`).
- Max out-of-order buffer size (`max_buffer_size`: default 1000 frames).

## Workflow

1. **Monotonic Sequence Inspection**:
   - Compare incoming `sequence_id` with expected sequence ($S_{\text{expected}}$).
   - If $S_{\text{incoming}} == S_{\text{expected}}$: process frame, increment sequence, and drain contiguous buffered frames (`FeedSyncState.SYNCED`).
2. **Gap Detection & Out-of-Order Buffering**:
   - If $S_{\text{incoming}} > S_{\text{expected}}$: flag gap, identify missing range [$S_{\text{expected}} .. S_{\text{incoming}} - 1$], buffer incoming frame, and transition to `FeedSyncState.DIRTY_SYNC_PENDING`.
3. **Stale Frame Suppression**:
   - If $S_{\text{incoming}} < S_{\text{expected}}$: ignore stale / duplicate frame.
4. **Retransmission Reconciliation**:
   - Ingest missing frames from retransmission endpoint; drain out-of-order buffer and restore `FeedSyncState.SYNCED`.

> Full procedure: see `references/workflows.md`.
> Standards reference: see `references/standards.md`.
> Printable pre-flight checklist: see `assets/checklist.md`.

## Common Pitfalls

- **Trading on Unsynced Order Books**: Processing trading signals while the feed state is `DIRTY_SYNC_PENDING`, executing trades against corrupted order books.
- **Unbounded Out-of-Order Buffers**: Allowing out-of-order message buffers to grow indefinitely during prolonged network outages, causing memory exhaustion.
- **Ignoring Secondary Feed Arbitration**: Requesting TCP retransmissions without first checking if the missing packet is available on a secondary redundant UDP multicast feed (Feed B).

## Verification

- Instantiate `SequenceGapDetector`. Ingest in-order sequence 100 and 101 $\implies$ verify `state = FeedSyncState.SYNCED`. Ingest sequence 103 (missing 101, 102) $\implies$ verify `is_gap_detected=True`, `missing_range=(101, 102)`, and `DIRTY_SYNC_PENDING` state. Reconcile missing frames 101 and 102 $\implies$ verify frames 101, 102, and 103 processed contiguous and state restored to `SYNCED`.
- Run `python scripts/test_gap_detector.py`.

## Related Skills

- `binary-protocol-parsing-for-low-latency-feeds`
- `smart-order-router-failover-on-venue-outage`
---
