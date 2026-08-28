# Workflows — sequence-number-gap-detection-for-feeds

The engine is a state machine per sequence space. Everything below is one stream; a
single `SequenceGapDetector` runs many of them independently.

## 0. Establish the sequence space

Decide what one `stream` key means **before** writing any ingestion code, because getting
it wrong is not a bug that shows up as an error.

| Feed | One stream key is | Sequencing |
|---|---|---|
| Nasdaq MoldUDP64 / ITCH | the MoldUDP64 Session (one multicast channel) | point, per message |
| CME MDP 3.0 | the channel | point, per packet |
| Binance diff-depth WebSocket | the symbol's stream, e.g. `btcusdt@depth` | range (`U`..`u`) |
| Vendor per-symbol WebSocket | the symbol's stream | point or range, per vendor |

Keys are compared exactly. Normalize case at the boundary; the engine will not fold
`btcusdt@depth` and `BTCUSDT@DEPTH` together, by design — silently merging two sequence
spaces is worse than tracking one twice.

## 1. Seed, or accept an adopted baseline

```python
detector = SequenceGapDetector(max_buffer_size=4096)

# Known start: a snapshot's lastUpdateId + 1, or a stored session position.
detector.resynchronize("btcusdt@depth", snapshot["lastUpdateId"] + 1)
```

Without a seed, the first frame ingested becomes the baseline and everything published
before it is neither detectable nor recoverable. That is a legitimate choice for a
live-only consumer and a defect for anything reconstructing state from the session open.

## 2. Ingest

```python
result = detector.ingest_frame(FeedFrame(stream, seq, payload, last_seq))
for frame in result.processed_frames:
    book.apply(frame.payload)          # contiguous, in order, safe
```

Dispositions:

| Disposition | Meaning | Caller action |
|---|---|---|
| `PROCESSED` | Contiguous; frame and any drained successors released | Apply |
| `PARTIAL_OVERLAP` | Range frame straddling the expected sequence | Apply (see §5) |
| `BUFFERED` | Ahead of the gap; held | Request `missing_ranges` |
| `DUPLICATE` | Already delivered, or already buffered | Discard |
| `DROPPED_BUFFER_FULL` | Buffer bound hit; frame discarded, stream latched | Snapshot resync |
| `RESET_SUSPECTED` | Large backward jump; frame refused | Confirm restart, then resync |
| `DROPPED_RESET_REQUIRED` | Stream already latched | Snapshot resync |

## 3. Gate the consumer

```python
if not detector.is_trading_authorized(stream):
    strategy.suspend()
```

Authorized means `SYNCED` and nothing outstanding. `DIRTY_SYNC_PENDING`, `RECOVERING`,
`RESET_REQUIRED` and an unknown stream are all unauthorized.

## 4. Recover

### 4a. Retransmission / replay (MoldUDP64, CME)

```python
ranges = result.missing_ranges              # from the ingest_frame that found the gap
for _ in range(MAX_RECOVERY_ROUNDS):
    if not ranges:
        break
    frames = venue.request_retransmission(stream, ranges)
    outcome = detector.reconcile_missing_frames(stream, frames)
    if outcome.is_synced:
        break
    ranges = outcome.remaining_ranges       # only what is still missing
    if outcome.remaining_sequence_count > venue.max_replay_span:
        break                               # too large to replay; snapshot instead

# Whether the loop closed the gap, exhausted its rounds, or gave up on the span,
# the authorization gate is the single arbiter of what happens next.
if not detector.is_trading_authorized(stream):
    snapshot = snapshot_client.fetch(stream)
    book.load(snapshot)
    detector.resynchronize(stream, snapshot.next_sequence_id)
```

Two properties this loop depends on:

- **Partial responses are normal.** MoldUDP64 returns only the messages that completely
  fit one UDP packet and expects further requests for the remainder; CME caps a replay
  request. `is_synced` is the only reliable "done".
- **Bound the loop.** A gap that keeps failing to close is a snapshot resync, not an
  infinite request stream. Venues cap recovery request volume; an unbounded retry loop
  against a recovery service is itself an outage.

### 4b. Snapshot only (Binance, and any venue without replay)

```python
snapshot = rest.depth(symbol, limit=5000)
book.load(snapshot)
detector.resynchronize(stream, snapshot["lastUpdateId"] + 1)
```

`resynchronize` discards everything buffered: those frames belong to the pre-snapshot
sequence space and downstream state is about to be rebuilt anyway.

## 5. Range-sequenced streams

For a Binance depth stream, `FeedFrame(stream, event["U"], event, event["u"])`.

The three documented cases map exactly onto three dispositions:

| Binance rule | Engine |
|---|---|
| "drop any event where `u` is <= lastUpdateId" | `DUPLICATE` |
| first processed event satisfies `U <= lastUpdateId AND u >= lastUpdateId` | `PARTIAL_OVERLAP`, applied |
| "each new event's `pu` should equal the previous event's `u`" | `PROCESSED` when `U == expected` |
| `pu` mismatch → "initialize the process from step 3" | gap → `resynchronize` |

## 6. Heartbeats and end of session

```python
if packet.message_count == 0:                      # MoldUDP64 heartbeat
    detector.observe_heartbeat(stream, packet.sequence_number)
```

Frame-driven detection cannot see loss at the tail of a stream: if the last messages
before a quiet period are dropped, no later frame exists to expose them. A heartbeat
carries the publisher's next expected sequence and closes that hole.

MoldUDP64's End of Session packets (Message Count `0xFFFF`) also carry the next expected
sequence and are the **deadline**: re-requests are accepted while they persist, and a
stream still showing outstanding ranges when they stop is permanently short those
messages. Treat any non-empty `missing_ranges` at that point as a data-integrity incident,
not a recoverable gap.

## 7. Monitor

```python
s = detector.stats(stream)
emit("feed.missing_sequences", s.outstanding_missing_count, stream=stream)
emit("feed.state", s.state.value, stream=stream)
emit("feed.gaps_detected", s.gaps_detected, stream=stream)
emit("feed.buffer_depth", s.buffered_frames, stream=stream)
```

`outstanding_missing_count` is the input
`graduated-response-to-data-quality-degradation` consumes as `missing_sequence_count`.
`buffered_frames` approaching `max_buffer_size` is the leading indicator of an imminent
`RESET_REQUIRED`; alert on it rather than on the latch.

## 8. What is deliberately not here

- **A/B line arbitration and the arbitration window.** Holding a gap before declaring the
  packets lost, deduplicating two copies of one stream, and escalating a venue's recovery
  tiers on a timer belong to `exchange-multicast-feed-handling`.
- **Timers of any kind.** This engine is driven purely by what the caller feeds it.
- **Payload decoding.** See `binary-protocol-parsing-for-low-latency-feeds`.
- **Liveness.** A stream that stops entirely produces no gap. Pair with a wall-clock
  staleness check.
