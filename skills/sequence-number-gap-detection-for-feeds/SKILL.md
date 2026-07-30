---
name: sequence-number-gap-detection-for-feeds
description: Use when consuming exchange market data feeds (WebSockets, ITCH, UDP
  multicast) to detect sequence number gaps, trigger gap-fill re-transmission requests,
  and mark orderbook feeds as dirty until state reconciliation completes.
domain: algorithmic-trading
subdomain: real-time-architecture
tags:
- real-time-architecture
- sequence-gaps
- feed-monitoring
- packet-loss
- orderbook-sync
- retransmission
brokers_frameworks:
- Sequence Gap Detector
- Python Real-Time Engine
version: '1.0'
author: algo-trading-skills-contributors
license: Apache-2.0
---

## When to Use

Invoke this skill when processing high-frequency WebSocket, UDP multicast, or binary market data streams where packets may drop or arrive out-of-order. Missing a single sequence number in an orderbook feed invalidates book depth integrity. This skill tracks expected sequence IDs ($S_{\text{expected}} = S_{\text{last}} + 1$), buffers out-of-order frames, flags the orderbook as out-of-sync, and requests missing sequence ranges before resuming live processing.

## Prerequisites

- Market data feed providing incrementing integer sequence numbers per channel/symbol.
- Historical tick/packet re-transmission mechanism (REST API or multicast re-transmission server).

## Workflow

1. **Track Monotonic Sequence Numbers**:
   - Maintain $S_{\text{expected}} = S_{\text{last}} + 1$ for each feed channel.

2. **Evaluate Incoming Frame Sequence $S$**:
   - $S = S_{\text{expected}}$: Process in-order frame and increment $S_{\text{expected}}$.
   - $S > S_{\text{expected}}$: Sequence gap detected. Buffer frame in out-of-order queue and flag feed state as `DIRTY_SYNC_PENDING`.
   - $S < S_{\text{expected}}$: Stale/duplicate frame. Log and discard.

3. **Issue Re-Transmission / Gap-Fill Request**:
   - Request missing sequence range $[S_{\text{expected}}, S - 1]$ from historical storage or re-transmission endpoint.

4. **Reconcile State & Resume**:
   - Ingest missing gap frames, drain out-of-order buffer in sequence, and clear `DIRTY_SYNC_PENDING` flag.

> Full procedure: see `references/workflows.md`.
> Standards reference: see `references/standards.md`.
> Printable pre-flight checklist: see `assets/checklist.md`.

## Common Pitfalls

- **Trading on Out-of-Sync Orderbook**: Placing limit or market orders while sequence gap recovery is actively pending.
- **Unbounded Out-of-Order Buffer Growth**: Allowing out-of-order queue size to grow indefinitely during sustained network outages.
- **Ignoring Per-Symbol Channels**: Conflating multi-symbol sequence numbers when the exchange maintains per-symbol sequence counters.

## Verification

- Process in-order sequences (1, 2, 3) and verify `SYNCED` state.
- Inject gap sequence (1, 2, 6), verify `DIRTY_SYNC_PENDING` state and gap range request [3, 4, 5].
- Run `python scripts/test_gap_detector.py` and confirm 100% pass rate.

## Related Skills

- `websocket-reconnection-with-state-recovery`
- `orderbook-l2-l3-reconstruction`
- `market-data-snapshot-plus-delta-reconciliation`
---
