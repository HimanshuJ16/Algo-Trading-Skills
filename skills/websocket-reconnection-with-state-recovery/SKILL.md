---
name: websocket-reconnection-with-state-recovery
description: Use when a market-data or order-update WebSocket must reconnect and then
  prove its state still matches the venue's — bounded jittered backoff that never exceeds
  its own ceiling, deterministic re-subscription from desired state, and fail-closed
  sequence-gap recovery that withholds messages until the gap is provably filled or the
  stream is re-snapshotted.
domain: algorithmic-trading
subdomain: real-time-architecture
tags:
- real-time-architecture
- websocket
- reconnection
- exponential-backoff
- full-jitter
- state-recovery
- sequence-gap
- fail-closed
brokers_frameworks:
- Binance Spot WebSocket Streams
- Coinbase Advanced Trade WebSocket
- Python Standard Library
version: "2.0.0"
author: algo-trading-skills-contributors
license: Apache-2.0
---

## When to Use

Invoke this when a bot holds a long-lived WebSocket to a venue and the correctness of
downstream state — an order book, a position view, a bar aggregator — depends on having
seen every message exactly once, in order. Reconnects are not exceptional: Binance
documents that "a single connection to the API is only valid for 24 hours; expect to be
disconnected after the 24-hour mark", so a bot that runs for a week reconnects at least
seven times without anything ever going wrong.

This skill covers the reconnect *lifecycle* and the state question it raises: connection
state transitions, backoff that is bounded and jittered, re-subscription rebuilt from
desired state, and — the part that is usually missing — deciding whether the messages
arriving after the reconnect can be trusted at all.

The control it produces answers one question on every message: **is what I have built
still what the venue sent?** When the answer is no, it stops handing messages downstream
rather than letting a hole propagate into a book.

## When NOT to Use

- **As a general "REST-fetch the missing sequence numbers" design.** Most venues cannot
  do this and never claim to. Binance's documented answer to a depth-stream continuity
  break is to *discard the local book and restart from a fresh snapshot*; Coinbase
  Advanced Trade documents `sequence_num` so a consumer can *detect* dropped messages and
  offers no retransmission at all. Range gap-fill is only real where the venue exposes an
  **id-addressable history endpoint** — Binance `GET /api/v3/aggTrades` with `fromId` is
  the clean example. Everywhere else, `resynchronize()` after a re-snapshot is the whole
  recovery path. See `references/standards.md` §2.
- **For order book snapshot/delta assembly.** Buffering deltas behind a REST snapshot,
  aligning on `lastUpdateId`, and applying levels is
  `market-data-snapshot-plus-delta-reconciliation`. This skill hands that skill a stream
  it can trust, and takes over again when the socket drops.
- **As a multi-stream continuity engine.** One sequence space per channel with buffering
  of out-of-order frames, `missing_ranges` arithmetic, and venue retransmission tiers is
  `sequence-number-gap-detection-for-feeds`. This manager tracks a single watermark per
  symbol and is deliberately simpler.
- **To detect that a socket has gone quiet.** A frozen TCP connection delivers no message,
  so no gap is ever exposed. Liveness is a heartbeat/ping problem —
  `graceful-degradation-to-polling-fallback`.
- **As the subscription-correctness authority.** Avoiding double-subscription and
  duplicate tick delivery across SDK reconnects is
  `websocket-reconnect-without-duplicate-subscriptions`.
- **On a latency-critical hot path.** A lock held across ingestion, `Enum` state, and
  per-message list allocation is a correctness reference, not a colocated feed handler.
- **As the risk control itself.** `is_synchronized()` raises a flag; it does not flatten a
  position. Wire it into `capital-preservation-mode-for-degraded-conditions` or
  `kill-switch-and-drawdown-circuit-breakers`.

## Prerequisites

- A **monotonically increasing per-symbol sequence id** on the stream. Verify the scope
  before keying by symbol: a channel-sequenced feed numbers every instrument in one space,
  and keying it per symbol makes every message look like a gap.
- A **recovery path**, and honesty about which one you have:
  1. an id-addressable history endpoint (`fromId`-style) — range gap-fill works; or
  2. a snapshot endpoint — only `resynchronize()` works; or
  3. neither — this manager can tell you the stream is broken and nothing more.
- The venue's **published connection limits**. Binance allows 300 connection attempts per
  5 minutes per IP; a reconnect loop with no backoff spends that budget in seconds and is
  then locked out during the outage it is trying to survive.
- The venue's **history-endpoint page size and weight**, which bounds `max_gap_fill_size`.
  Binance `aggTrades` returns at most 1000 records per call at IP weight 4.
- Python 3.8+, standard library only. Validated on CPython 3.11.

## Workflow

1. **Register the desired subscription set, not the issued one.**
   - `register_symbol_subscription()` builds the authoritative set;
     `on_connection_established()` returns it sorted, so every reconnect re-subscribes from
     current state rather than replaying a log of past subscribe calls.
   - Set `requires_auth=True` for private/order-update streams: only then does the machine
     pass through `AUTHENTICATED`. A public market-data stream has no auth step and
     claiming one in a state diagram is fiction.

2. **Back off inside the ceiling, and jitter inside it too.**
   - `compute_next_backoff()` returns
     $\min(T_{\max}, T_{\text{base}} \times 2^k)$ with the jitter drawn *within* that
     value — AWS "Full Jitter", $\text{sleep} = \mathrm{random}(0, \min(cap, base \cdot 2^k))$.
   - **Decision point — jitter added *on top of* the cap is not a cap.** The common
     `delay = capped + uniform(0, capped * 0.5)` returns up to 45 s against a documented
     30 s ceiling, and it does not spread the herd: every client still waits at least the
     full exponential delay, so the reconnect burst is merely smeared, not flattened.
   - **Decision point — `jitter_factor` picks the AWS variant.** `1.0` is Full Jitter
     (fastest recovery, least contention). `0.5` is Equal Jitter, when you need a floor
     under the delay. `0.0` disables it — acceptable only for a single-client test.

3. **Distinguish a fault from a scheduled rotation.**
   - `on_connection_lost(..., scheduled=True)` for an expected eviction (the 24-hour
     lifetime, a maintenance window). It jitters but does not escalate the attempt counter.
   - **Decision point — escalating on a scheduled event corrupts the signal.** A bot that
     treats its daily rotation as failure #7 waits 30 s for a socket that was never broken,
     and a genuine outage arriving later is indistinguishable in the metrics.

4. **Reset the backoff on evidence, not on optimism.**
   - The attempt counter clears when a message is *processed end-to-end*, not when the
     socket opens. A venue that accepts a connection and drops it two seconds later would
     otherwise reconnect at the base delay forever.

5. **Classify every message before trusting it.**
   - `seq == watermark + 1` → pass through.
   - `seq <= watermark` → duplicate or stale replay. **Withheld, and the watermark is not
     moved.** Rewinding the watermark on a late frame fabricates a gap on the next message
     and re-emits already-applied updates into the book.
   - `seq > watermark + 1` → gap. Go to step 6.
   - **Decision point — a *large* backward jump is a publisher restart, not a duplicate.**
     Confirm the venue's restart signal and call `resynchronize()`; do not wait for the
     stream to catch back up to a watermark it has abandoned.

6. **Fill the gap only if you can prove you filled it.**
   - The fill callback is asked for exactly `[watermark+1, seq-1]` and its answer is
     validated: right symbol, right count, contiguous, ascending. Anything else — a short
     page, an empty list, a `None`, a raised `ConnectionError` — is a **failed** fill.
   - **Decision point — a partial fill is the normal failure, not the exotic one.** The
     page size is 1000 on Binance `aggTrades`; a gap wider than
     `max_gap_fill_size` is refused *before* the call, because paging an outage-sized hole
     burns rate-limit weight against a venue that is already degraded and still leaves the
     book wrong.
   - **Decision point — accepting an unproven fill is worse than reporting a gap.** The
     watermark then advances past data nobody ever saw, every later continuity check
     passes, and the book is permanently and silently wrong.

7. **Fail closed, then resynchronize.**
   - A failed fill latches the symbol: `is_synchronized(symbol)` goes false, the state
     stays `RECOVERING_GAP`, `unrecovered_gaps()` names the exact missing range, and every
     subsequent message for that symbol returns `[]` and is counted in
     `withheld_message_count`.
   - Recovery is the venue's documented one: fetch a fresh snapshot, rebuild local state,
     then call `resynchronize(symbol, snapshot_last_update_id + 1)`.
   - **Decision point — other symbols keep flowing.** A latch is per symbol;
     `is_synchronized()` with no argument is the portfolio-wide gate.

> Full procedure: see `references/workflows.md`.
> Venue facts, jitter definitions and defaults: see `references/standards.md`.
> Printable pre-flight checklist: see `assets/checklist.md`.

## Common Pitfalls

- **Adding jitter on top of the cap.** `capped + uniform(0, capped * f)` exceeds the
  ceiling it documents by a factor of `1 + f` and still leaves every client waiting the
  full exponential delay. Draw the randomness *inside* the cap.
- **Computing the delay after incrementing the attempt counter.** The first retry then
  waits `2 × base` and the configured base delay is never used by anything.
- **Letting the exponent grow without bound.** `base * 2**attempts` raises
  `OverflowError: int too large to convert to float` once the counter passes ~1024 — about
  nine hours of retries at a 30 s cadence, i.e. exactly the multi-hour outage the backoff
  exists for. Clamp the exponent, not just the result.
- **Reconnecting with no backoff at all.** Binance permits 300 connection attempts per
  5 minutes per IP; a tight loop exhausts that in seconds and converts a blip into a
  lockout.
- **Treating "reconnected" as "nothing was missed".** The gap window is real. Reconnection
  restores the *transport*; it says nothing about the *state*.
- **Accepting a partial gap fill.** Advancing the watermark on a short page hides the hole
  forever — the most dangerous failure here precisely because every later check passes.
- **Swallowing a gap when no fill path is configured.** Logging a warning and continuing to
  `STREAMING` produces a consumer that is confidently wrong. If there is no recovery path,
  the correct output is "unsynchronised", not "fine".
- **Letting a stale frame rewind the watermark.** It re-emits already-applied updates and
  fabricates a gap on the very next message, triggering a refetch of data you already hold.
- **Refetching an outage-sized range.** A four-hour hole is not a gap to fill; it is a
  re-snapshot. Paging it burns rate-limit weight against a venue that is already sick.
- **Holding an unbounded `processed_messages` list.** In a 24/7 feed handler that is a
  memory leak with a market-data-shaped growth curve.
- **Calling a blocking REST fill without a timeout.** The callback runs under the manager's
  lock; a hung HTTP request freezes ingestion for every symbol, not just the broken one.
- **Re-subscribing from an unordered set.** Frame order then varies run to run, which makes
  a reconnect bug irreproducible.

## Verification

- Run the unit suite and confirm every test passes:
  `python -m unittest discover -s skills/websocket-reconnection-with-state-recovery/scripts`
- Sweep `compute_next_backoff(attempt=k)` for $k \in [0, 40]$ and confirm every delay lies
  in $[0, T_{\max}]$ — never above the documented ceiling.
- With `jitter_factor=0.0`, confirm the ceiling sequence is exactly
  $1, 2, 4, 8, 16, 30, 30, \ldots$ for a 1 s base and 30 s cap.
- With `jitter_factor=1.0`, draw 200 delays at one attempt index and confirm they are
  spread across the interval rather than collapsed onto one instant.
- Drive 1100 consecutive `on_connection_lost()` calls and confirm no `OverflowError` and
  every delay still inside the cap.
- Call `on_connection_lost(scheduled=True)` and confirm the attempt counter does not move.
- Register three symbols in mixed case with padding and confirm
  `on_connection_established()` returns them normalised, deduplicated and sorted.
- Feed seq 100 then 104 with a fill callback that returns 101–103 and confirm the emitted
  list is exactly `[101, 102, 103, 104]` and `is_synchronized()` stays true.
- Repeat with a callback that returns one message short, an empty list, `None`, a reversed
  range, and one that raises `ConnectionError`; confirm each returns `[]`, latches
  `RECOVERING_GAP`, and reports the exact missing range in `unrecovered_gaps()`.
- With no fill callback configured at all, confirm a gap latches rather than passing
  through.
- Open a gap wider than `max_gap_fill_size` and confirm the callback is **not called**.
  Open one exactly at the limit and confirm it is.
- While latched, feed five more messages and confirm all return `[]` and
  `withheld_message_count` is 5; then `resynchronize()` and confirm the stream resumes at
  the snapshot position.
- Latch one symbol and confirm a second symbol keeps emitting, while the no-argument
  `is_synchronized()` stays false.
- Feed 100, 104, then a late 102, and confirm it is dropped, the watermark stays 104, and
  the following 105 is an ordinary advance with no second gap fill.
- Ingest 500 messages per symbol across four threads concurrently and confirm each is
  emitted exactly once with no fabricated gaps.
- Confirm `processed_messages` stops growing at `max_retained_messages`.

## Related Skills

- `websocket-reconnect-without-duplicate-subscriptions`
- `sequence-number-gap-detection-for-feeds`
- `market-data-snapshot-plus-delta-reconciliation`
- `graceful-degradation-to-polling-fallback`
- `producer-consumer-tick-pipeline`
- `tick-buffering-burst-handling`
- `historical-order-book-reconstruction-from-message-logs`
- `broker-status-page-monitoring-integration`
- `multi-broker-rate-limit-handling`
- `capital-preservation-mode-for-degraded-conditions`
- `kill-switch-and-drawdown-circuit-breakers`
