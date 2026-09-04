---
name: sequence-number-gap-detection-for-feeds
description: >-
  Use when downstream state is only correct if every feed message arrived exactly once
  and in order. Tracks one expected sequence per stream, buffers out-of-order frames,
  computes the missing ranges, and withholds trading authorization until repaired.
license: Apache-2.0
metadata:
  domain: algorithmic-trading
  subdomain: real-time-architecture
  tags: gap-detection, sequence-number, moldudp64, cme-mdp3, binance-depth-stream, packet-loss, order-book-sync, retransmission, snapshot-recovery
  brokers_frameworks: "Nasdaq MoldUDP64; Nasdaq TotalView-ITCH; CME MDP 3.0; Binance WebSocket Depth Streams; Python Dataclasses"
  version: "2.0.0"
  author: algo-trading-skills-contributors
---

## When to Use

Use this skill when a consumer builds state — an order book, a bar aggregator, a position view — from a **sequenced** feed, and that state is only correct if every message arrived exactly once and in order. One `SequenceGapDetector` instance tracks many independent sequence spaces at once and answers one question per stream: *does the state I built still match the publisher's?*

Feeds do not agree on how to number things, so two sequencing models are supported:

- **Point sequencing** — one sequence number per frame, advancing by one. Nasdaq MoldUDP64 numbers *messages* this way (the downstream header carries the sequence of the first message in the packet plus a Message Count, and following messages are implicitly numbered sequentially); CME MDP 3.0 numbers *packets*, per channel.
- **Range sequencing** — each frame covers an inclusive span of update IDs. Binance diff-depth events carry `U` (first update ID) and `u` (final update ID), and the documented continuity rule is that each event's `pu` equals the previous event's `u`. Pass `last_sequence_id` and the detector advances to `u + 1`.

The recovery path is the caller's and differs by venue. Where a retransmission or replay service exists (MoldUDP64 Re-request Server, CME TCP replay), feed the backfill to `reconcile_missing_frames`. Where none exists — Binance's documented answer to a continuity break is to re-fetch the snapshot and restart — call `resynchronize`.

Every protocol fact in this skill is cited to a primary specification in `references/standards.md`. Re-verify against the document version your firm is certified against.

## When NOT to Use

- **For redundant A/B multicast lines.** Arbitrating two copies of one stream, holding a gap for an arbitration window before declaring loss, and escalating through a venue's recovery tiers is `exchange-multicast-feed-handling`. That engine holds one packet sequence space per instance and owns the timers; this one holds many sequence spaces and owns none. A co-located UDP handler wants that skill, not this one.
- **Keyed per instrument on a channel-sequenced feed.** MoldUDP64 and CME MDP 3.0 number one sequence per channel or session, covering every instrument on it. Instantiating one `stream` per symbol against those feeds makes *every* message look like a gap. `stream` is the sequence space, not the ticker.
- **As a decoder.** Payloads are opaque here. Unpacking ITCH message blocks or CME SBE belongs to `binary-protocol-parsing-for-low-latency-feeds` and `nasdaq-totalview-itch-feed-parsing`.
- **As a staleness or liveness monitor.** A stream that stops entirely produces no gap, because no later sequence ever arrives to expose one. `observe_heartbeat` covers this only where the publisher heartbeats; otherwise pair with a wall-clock staleness check and `graduated-response-to-data-quality-degradation`.
- **As a full snapshot/delta reconciler.** This tracks continuity; rebuilding book state from a snapshot plus buffered deltas is `market-data-snapshot-plus-delta-reconciliation`.
- **On a latency-critical hot path in CPython.** Dict-based buffering, `Enum` comparisons and per-frame result objects are a correctness reference, not a colocated feed handler.

## Prerequisites

- The **sequence space identifier** for each stream (`FeedFrame.stream`), compared exactly — normalize case yourself. One per exchange channel/session, or per WebSocket stream.
- The **sequence number** decoded from the transport, and for a range-sequenced feed its **final** sequence number as well.
- A **recovery path**: a retransmission/replay client, a snapshot client, or both. Without one, this engine can only tell you the feed is broken.
- A **buffer bound** (`max_buffer_size`) sized from the stream's message rate times the longest recovery you intend to survive. Overflow is not a soft condition — it latches `RESET_REQUIRED`.
- Python 3.10+, standard library only. Validated on CPython 3.11.

## Workflow

1. **Seed the stream before the first frame, when you can.** `resynchronize(stream, next_sequence_id)` sets the expected sequence from a snapshot's `lastUpdateId + 1`, or from a stored session position. Otherwise the first frame ingested *becomes* the baseline.
   - **Decision point — an adopted baseline hides everything before it.** A client that starts mid-session and adopts sequence 4,000,000 as its baseline has silently missed the whole session and will never know. That is acceptable for a live-only consumer and unacceptable for anything reconstructing state from the open.

2. **Ingest every frame, in whatever order it arrives.** `ingest_frame` classifies it and releases only what is contiguous. `processed_frames` is always safe to apply in the order given; a frame whose predecessors are missing is buffered, never handed out.

3. **On a gap, request `missing_ranges` — not the whole span.**
   - **Decision point — already-buffered frames are not missing.** When 103 is buffered and 106 arrives, the missing set is `[101..102]` and `[104..105]`, not `[101..105]`. Venue recovery capacity is capped in ways that punish over-requesting: a MoldUDP64 re-request returns only the messages that completely fit one UDP packet, and CME caps how much one TCP replay request may return. Sequences spent re-fetching frames you already hold are sequences you do not get back.

4. **Stop trading the moment the state leaves `SYNCED`.** `is_trading_authorized(stream)` is the gate, and it is false for `DIRTY_SYNC_PENDING`, `RECOVERING`, `RESET_REQUIRED`, and for a stream never seen.
   - **Decision point — an unknown stream is not authorized.** Nothing has yet established that local state corresponds to anything the publisher sent.

5. **Backfill, then check whether it worked.** `reconcile_missing_frames` returns a `ReconciliationResult`, and the field that matters is `is_synced`.
   - **Decision point — partial recovery is the normal case, not the exception.** MoldUDP64 states plainly that further requests are needed for whatever did not fit. A caller that resumes on the first response resumes on a stream that is still broken. Loop on `remaining_ranges` until it is empty, and give up on a snapshot when the range is larger than the venue will replay.

6. **Distinguish the two failures backfill cannot fix.** Both latch `RESET_REQUIRED`, and only `resynchronize` clears it.
   - **Decision point — a buffer overflow is unrecoverable by definition.** Once a frame ahead of the gap has been dropped, backfilling the frames behind it cannot produce a correct book. The engine refuses to apply anything further rather than quietly resuming; `reconcile_missing_frames` raises `FeedResetRequiredError`.
   - **Decision point — a large backward jump is a restart, not a duplicate.** CME resets MsgSeqNum weekly and on a Channel Reset; a MoldUDP64 restart opens a new Session with its own numbering. From sequence numbers alone a restart is indistinguishable from a stale retransmission echo, so the engine reports `RESET_SUSPECTED` and refuses the frame. Confirm the venue's in-band restart signal, then `resynchronize`.

7. **Feed a heartbeat in where the publisher sends one.** `observe_heartbeat(stream, next_expected_sequence)` is the only way to see loss at the *tail* of a stream: if the last four messages before a quiet period are dropped, no later frame exists to expose them. MoldUDP64 heartbeats carry the next expected sequence for exactly this reason.

8. **Export `stats(stream)` to monitoring.** `outstanding_missing_count` is the metric `graduated-response-to-data-quality-degradation` consumes as `missing_sequence_count`.

> Full procedure: see `references/workflows.md`.
> Standards reference: see `references/standards.md`.
> Printable pre-flight checklist: see `assets/checklist.md`.

## Common Pitfalls

- **Trading on an unsynced book.** Every state except `SYNCED` means the local book may disagree with the venue in ways no price check will reveal. CME's own guidance on a detected gap is that all books maintained by the client system should be assumed no longer correct.
- **Letting the out-of-order buffer grow unbounded.** During a prolonged outage every subsequent frame is "ahead of the gap". A buffer that documents a cap but appends anyway exhausts memory in the exact scenario the cap exists for. The cap here drops the frame *and* latches `RESET_REQUIRED`, because a handler that silently discards the future while still applying the past is worse than one that stops.
- **Filing every low sequence number under "duplicate".** This is the failure that goes deaf silently: after a weekly reset or a session restart the whole new stream arrives *below* the old expected sequence, is discarded as stale, and the consumer sits on a frozen book emitting nothing. There is no error, no gap warning, and no missing data — just a feed that stopped meaning anything.
- **Assuming one retransmission request closes the gap.** The venue returns what fits and expects you to ask again. Resuming on a partial response is trading on a book with a hole in it.
- **Applying backfill after a buffer overflow.** The frames you dropped are gone; filling the earlier hole produces a book that is wrong from the overflow point onward and *looks* healthy.
- **Keying a channel-sequenced feed per symbol.** On MoldUDP64 or CME MDP 3.0 the messages for one instrument are scattered across a channel's sequence space, so a per-symbol tracker sees a gap at every single message and requests retransmission of the entire feed.
- **Treating a Binance depth event as a single sequence.** The event covers `U..u`. Advancing by one per event breaks continuity immediately; the correct check is `pu == previous u`, and the first event after a snapshot legitimately *straddles* `lastUpdateId` (`U <= lastUpdateId AND u >= lastUpdateId`) and must be applied, not discarded as stale.
- **Requesting retransmission from a venue that has none.** Binance's documented recovery is to re-fetch the snapshot and restart; there is no replay service to ask. `reconcile_missing_frames` has nothing to consume there — `resynchronize` is the whole recovery.
- **Sharing one detector instance across threads for the same stream.** The engine is not thread-safe; two threads ingesting one stream will interleave the expected-sequence update and the buffer drain.

## Verification

- **In-order baseline**: ingest 100 then 101 ⟹ `state == SYNCED`, one frame released per call, `expected_sequence == 102`, `is_trading_authorized` true.
- **Gap**: after 100, ingest 103 ⟹ `is_gap_detected` true, `disposition == BUFFERED`, `missing_ranges == ((101, 102),)`, `processed_frames == ()`, `state == DIRTY_SYNC_PENDING`, `is_trading_authorized` false.
- **Missing ranges exclude buffered frames**: after 100, 103, then 106 ⟹ `missing_ranges == ((101, 102), (104, 105))`.
- **Full backfill**: supply 101 and 102 ⟹ 101, 102 and the buffered 103 released in order, `is_synced` true, `state == SYNCED`.
- **Partial backfill**: supply only 101 ⟹ `is_synced` false, `state == RECOVERING`, `remaining_ranges == ((102, 102),)`.
- **Buffer bound**: with `max_buffer_size=2` and two frames buffered, a third ⟹ `DROPPED_BUFFER_FULL`, `state == RESET_REQUIRED`, buffer still 2, later frames ⟹ `DROPPED_RESET_REQUIRED`, and `reconcile_missing_frames` raises `FeedResetRequiredError`.
- **Restart**: with `sequence_reset_threshold=1000`, baseline 5,000,000 then sequence 1 ⟹ `RESET_SUSPECTED`, `RESET_REQUIRED`, nothing released; a jump one below the threshold ⟹ `DUPLICATE`.
- **Heartbeat**: after 100, `observe_heartbeat(stream, 105)` ⟹ `missing_ranges == ((101, 104),)` with an empty buffer; `observe_heartbeat(stream, 101)` ⟹ no gap.
- **Range sequencing**: `[100..104]` then `[105..109]` ⟹ `PROCESSED`, expected 110; `[100..104]` then `[107..109]` ⟹ missing `((105, 106),)`; after `resynchronize(stream, 1001)`, `[998..1005]` ⟹ `PARTIAL_OVERLAP` and applied, while `[990..1000]` ⟹ `DUPLICATE`.
- **Streams are independent**: a gap on one stream leaves another `SYNCED` and authorized.
- Run `python -m unittest discover -s skills/sequence-number-gap-detection-for-feeds/scripts` and confirm 62/62 pass.

## Related Skills

- `exchange-multicast-feed-handling`
- `market-data-snapshot-plus-delta-reconciliation`
- `binary-protocol-parsing-for-low-latency-feeds`
- `nasdaq-totalview-itch-feed-parsing`
- `graduated-response-to-data-quality-degradation`
- `websocket-reconnection-with-state-recovery`
