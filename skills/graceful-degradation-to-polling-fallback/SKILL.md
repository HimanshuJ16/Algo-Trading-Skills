---
name: graceful-degradation-to-polling-fallback
description: Use when a live strategy depends on a streaming market data feed and needs
  to detect that the stream is dead (not merely quiet), fail over to rate-limit-aware
  REST polling, deduplicate ticks across the handover, and declare itself blind when
  neither source is delivering
domain: algorithmic-trading
subdomain: real-time-architecture
tags:
- real-time-architecture
- feed-degradation
- polling-fallback
- websocket-failover
- high-availability
- tick-deduplication
- rate-limit-safety
brokers_frameworks:
- All Market Data Feeds
- WebSockets
- REST APIs
version: "2.0.0"
author: algo-trading-skills-contributors
license: Apache-2.0
---

## When to Use

Invoke this when an algorithmic trading bot depends on a streaming market data feed
while it holds open positions. A WebSocket can stop delivering without ever firing an
`on_close` event — the TCP connection stays nominally established while nothing arrives.
TCP will not rescue you: RFC 1122 §4.2.3.6 requires keep-alives to "default to off" and,
when enabled, to "default to no less than two hours", so a frozen socket can hang for the
rest of the session. This skill builds the detector, the REST fallback, the handover
deduplication, and the escalation path when both fail.

The control it produces answers three questions continuously: is the stream alive, what
feeds the strategy if it is not, and does the caller know when *nothing* is feeding it.

## When NOT to Use

- **As a way to make the handover lossless.** It is not, and cannot be, against a quote
  or ticker endpoint. A REST snapshot returns the *current* state, not the trades that
  printed while the stream was down. Every print in the gap is gone, so volume, VWAP,
  trade counts and any bar built from tick arrivals are wrong across a handover unless
  you backfill from a historical-trades endpoint. The manager reports the size of the
  hole (`FeedStatus.last_degradation_gap_seconds`) precisely so you can backfill or
  invalidate rather than carry on silently.
- **To detect that a market is quiet.** Trade silence and feed death look identical from
  the tick stream alone. Distinguishing them is the job of the transport heartbeat, not
  of this or any other timeout.
- **To fail over between vendors or venues.** This is one venue's stream degrading to the
  same venue's REST. Ranking a primary/secondary/tertiary vendor hierarchy is
  `vendor-outage-fallback-data-source-hierarchy`; two vendors legitimately disagree on
  price and timing, and treating that as degradation produces constant false failover.
- **As gap detection inside a healthy stream.** A feed that is delivering but skipping
  sequence numbers looks perfectly alive here. Use
  `sequence-number-gap-detection-for-feeds`.
- **As the risk control itself.** Detecting that you are blind is not the same as
  protecting capital while blind. Wire `is_blind()` into
  `capital-preservation-mode-for-degraded-conditions` or
  `kill-switch-and-drawdown-circuit-breakers`; this skill raises the flag, it does not
  act on it.
- **As a cross-symbol rate limiter.** The throttle here is a per-instrument floor. Budget
  across a universe belongs in `multi-broker-rate-limit-handling`.

## Prerequisites

- A streaming feed **and** a REST quote/ticker endpoint on the same venue, plus the
  venue's published rate limit for that endpoint — the limit is the design input, not an
  afterthought.
- A liveness signal from the transport: a WebSocket Ping/Pong pair (RFC 6455 §5.5.2–5.5.3)
  or the venue's own heartbeat frame. Without one, the only silence you can measure is
  trade silence, and the whole control degrades to guesswork on illiquid instruments.
- A per-tick **identity** — exchange trade id or sequence number — wherever the venue
  supplies one. Alpaca returns `i` on every trade; without an identity the manager cannot
  distinguish two genuine trades in the same instant from a repeat of one.
- Exchange timestamps normalised to epoch seconds in a single, documented timezone. Kite's
  REST quote returns `"YYYY-MM-DD HH:MM:SS"` with no offset — parsing IST as UTC puts the
  watermark 5½ hours out and silently discards the whole session.
- A decision, written down before go-live, about what the strategy does while degraded and
  while blind.

## Workflow

1. **Measure liveness on the transport, not on trades.**
   - Call `on_websocket_heartbeat()` from the pong / heartbeat handler and
     `ingest_websocket_tick()` on data. Either one refreshes liveness.
   - **Decision point — size `silence_timeout_seconds` from the venue's heartbeat
     cadence, never from how often the instrument trades.** Binance spot streams ping
     every 20s; Kite sends a 1-byte heartbeat every couple of seconds. A 3-second window
     against a 20-second ping degrades a perfectly healthy Binance socket on every quiet
     interval. Pass `heartbeat_interval_seconds` and the constructor rejects a window
     narrower than twice the cadence.
   - If the venue has no heartbeat, say so explicitly in your configuration notes: you are
     then detecting *trade* silence, and an illiquid instrument will fail over for the
     ordinary reason that nobody traded it.

2. **Degrade once, and record the hole.**
   - `check_feed_health()` transitions `HEALTHY_WEBSOCKET → DEGRADED_POLLING` when silence
     exceeds the threshold, records the gap, and does not re-fire while degraded.
   - **Decision point — the gap is a data-integrity event, not just an availability one.**
     Before resuming any volume- or trade-count-derived indicator, backfill the window
     from a historical-trades endpoint or mark those indicators unusable.

3. **Poll REST inside the venue's published limit.**
   - `poll_rest_fallback(fetch_fn)` enforces `min_poll_interval_seconds` locally and
     tags results `REST_POLLING`.
   - **Decision point — the naive "poll every 500 ms" is over the limit at every venue in
     the coverage table.** Kite allows 1 request/second on `/quote`. Alpaca throttles at
     200 requests/minute per account. Binance is weight-based and escalates repeat
     offenders from a 2-minute to a 3-day IP ban. Getting banned *while the stream is
     already down* is how a fallback turns an outage into a blackout.
   - **Decision point — per-symbol polling scales linearly with the universe.** At one
     request per symbol per second, four symbols already exceed Alpaca's account budget.
     Every venue here offers a batch endpoint; batch upstream and dispatch into per-symbol
     managers.

4. **Deduplicate on identity, not on a bare timestamp.**
   - A tick is accepted when its timestamp is ahead of the watermark, or equal to it with
     an unseen `identity`.
   - **Decision point — a strict `timestamp >` test is silent data loss on any venue whose
     timestamps are coarser than its trade rate.** Kite's `last_trade_time` has
     one-second resolution, so `>` keeps one tick per second and discards the rest.
     Alpaca's nanosecond RFC-3339 timestamps do not fit exactly in a float64, so trades
     microseconds apart can compare equal.
   - Ticks arriving *behind* the watermark are counted in `stale_tick_count`. A rising
     count means real prints are being dropped at the handover — usually a REST snapshot
     that jumped ahead of buffered WebSocket ticks.

5. **Hand back only on a demonstrably live stream.**
   - Recovery needs `required_stabilization_ticks` consecutive observations whose
     inter-arrival gaps are all inside the silence window. A tick arriving after a gap
     starts the run over.
   - **Decision point — a plain counter is not enough.** Five stragglers dribbling in
     10 seconds apart from a still-broken feed will satisfy any bare count and hand a dead
     stream back to the strategy. Recency is what makes the anti-flapping claim true.
   - Stop the polling worker only after the mode has actually returned to
     `HEALTHY_WEBSOCKET`.

6. **Escalate when the fallback fails too.**
   - After `max_consecutive_poll_failures` failed polls the mode becomes `BLIND_NO_DATA`
     and `is_blind()` returns true.
   - **Decision point — `None` from a polling call is not evidence of anything.** It means
     "nothing new", which a quiet market and a dead venue produce identically. Gate order
     entry on `is_blind()`, not on the absence of ticks.
   - **Decision point — `is_blind() == False` is not a clean bill of health.** It is also
     false throughout `DEGRADED_POLLING`, where prices are snapshots and the trade series
     has a hole in it. Read `get_status().feed_mode` for the three-way distinction and
     decide separately what a strategy may do while merely degraded.

> Full procedure: see `references/workflows.md`.
> Venue coverage, limits and standards: see `references/standards.md`.
> Printable pre-flight checklist: see `assets/checklist.md`.

## Common Pitfalls

- **Timing out on trade silence.** A quiet instrument and a dead socket are
  indistinguishable from the tick stream. Fixing a 3-second window to trade arrival pins
  every illiquid symbol into permanent polling, and permanent polling is what gets the
  account rate-limited.
- **Measuring elapsed time on the wall clock.** `time.time()` moves when NTP steps it. A
  backward step makes the measured silence negative and suppresses degradation entirely;
  a forward step fabricates one. Elapsed time belongs on `time.monotonic()`; wall-clock
  timestamps stay in the exchange clock domain and are never compared against it.
- **Comparing a broker timestamp to a local one.** Defaulting a tick's timestamp to local
  receipt time pushes the watermark ahead of exchange time by the network latency, and
  every genuine tick behind it is then silently discarded until exchange time catches up.
- **Deduplicating on a bare timestamp.** See Workflow step 4 — on a second-resolution
  feed this throws away most of the session.
- **Treating the handover as lossless.** The trades printed during the outage are not in
  the snapshot. Resuming a VWAP or volume bar across the gap produces a number that looks
  plausible and is wrong.
- **Polling without a throttle.** The fallback path has its own failure mode, and it is an
  IP ban that arrives exactly when the stream is already down.
- **Counting throttled calls as failures.** A caller polling in a tight loop would then
  declare itself blind while the fallback is working perfectly.
- **Recovering on a bare tick count.** Stragglers from a broken feed accumulate into a
  false "stabilised" verdict; require the run to be recent as well as consecutive.
- **Double-feeding during the overlap.** Between the stream resuming and the poller being
  stopped, both paths push into the pipeline. The shared watermark is what keeps that
  safe — one deduplication state for both sources, not one per source.
- **Unsynchronised state.** The socket read thread, the polling worker and the health loop
  all mutate the mode and the watermark. An unguarded read-modify-write lets a duplicate
  tick through, which is the exact failure this control exists to prevent.
- **Fanning out the wrong symbol.** A per-instrument manager fed another instrument's
  ticks corrupts its watermark. Treat it as a caller bug and raise, do not absorb it.
- **Swallowing every REST error identically.** A 429 needs backoff, a 401 needs a human,
  a 503 needs a retry. Collapsing them into `return None` hides which one you have.
- **Believing `None` means "no news".** It also means "the venue is gone". Only
  `is_blind()` distinguishes them.

## Verification

- Run the unit suite and confirm every test passes:
  `python -m unittest discover -s skills/graceful-degradation-to-polling-fallback/scripts`
- Drive the manager with an injected clock: advance past the silence window and confirm
  `DEGRADED_POLLING`; advance to exactly the threshold and confirm it does *not* degrade.
- Call `on_websocket_heartbeat()` every 2s for a minute with no ticks and confirm the mode
  stays `HEALTHY_WEBSOCKET` — then stop the heartbeats and confirm it degrades.
- Ingest two ticks with the same timestamp and different `identity` values and confirm
  both are returned; repeat one identity and confirm the second is deduplicated.
- Feed a REST snapshot at $t+5$ then buffered WebSocket ticks at $t+1 \ldots t+4$ and
  confirm `stale_tick_count` reports the dropped prints rather than hiding them.
- Poll in a tight loop and confirm exactly one call reaches the fetcher per
  `min_poll_interval_seconds`, that `throttled_poll_count` rises, and that
  `poll_failure_count` stays at zero.
- Raise `ConnectionError` from the fetcher `max_consecutive_poll_failures` times and
  confirm `is_blind()`, then return a valid quote and confirm it drops back to
  `DEGRADED_POLLING`.
- Deliver stabilisation ticks spaced further apart than the silence window and confirm the
  mode stays degraded no matter how many arrive.
- Ingest concurrently from many threads and confirm each distinct tick is accepted exactly
  once and a repeated identity exactly once.
- Construct with a 3s window against a 20s `heartbeat_interval_seconds` and confirm the
  constructor raises rather than shipping the misconfiguration.

## Related Skills

- `websocket-reconnect-without-duplicate-subscriptions`
- `producer-consumer-tick-pipeline`
- `tick-buffering-burst-handling`
- `multi-broker-rate-limit-handling`
- `capital-preservation-mode-for-degraded-conditions`
- `kill-switch-and-drawdown-circuit-breakers`
- `vendor-outage-fallback-data-source-hierarchy`
- `sequence-number-gap-detection-for-feeds`
- `market-data-snapshot-plus-delta-reconciliation`
- `graceful-degradation-priority-during-partial-outage`
- `clock-skew-correction-for-tick-timestamps`
