# Deep Workflow Reference — websocket-subscription-reconciliation-after-reconnect

This file holds the full technical procedure referenced by `SKILL.md`. Load this when
actually implementing the skill, not just when deciding whether it applies.

## Full Procedure

### 1. Hold desired state outside the connection

Keep an authoritative set of the symbols the bot *intends* to receive
(`WebSocketReconnectEngine.desired_symbols`). It is mutated by strategy logic, never by
connection events, and it outlives every socket object. Every resubscription is derived
from it by a single snapshot (`snapshot_desired()`), so a reconnect can only ever produce
the current desired set — no more, no fewer.

The set is guarded by a lock. The SDK delivers `on_close` / `on_error` on its network
thread while the strategy thread is still calling `subscribe()`, and the reconnect path
reads the set and its size together.

### 2. Detect the disconnect that never announces itself

A dropped TCP connection with no Close frame surfaces as RFC 6455 close code **1006**, and
a *half-open* connection surfaces as nothing at all: the socket stays open, `recv` blocks
forever, and no reconnect logic ever fires. The only in-protocol detector is the Ping/Pong
exchange (RFC 6455 §5.5.2–5.5.3), so:

- Send pings on an interval, or require the venue's own heartbeat, and track the last
  inbound frame time on `time.monotonic()`.
- Treat "no inbound frame for N intervals" as a disconnect and tear the socket down
  yourself. Measuring staleness on the wall clock instead lets an NTP correction fabricate
  or mask a stall.
- Call `on_disconnect(reason)` once per outage. Repeat notifications for the same drop are
  ignored deliberately: SDKs commonly fire both an error and a close callback, and letting
  the second one overwrite the timestamp shrinks both the recorded gap and the backfill
  window that is derived from it.

### 3. Establish what the SDK already does before writing a line of resubscribe code

This is the single most common source of duplicate subscriptions. `pykiteconnect`'s
`KiteTicker` stores `subscribed_tokens` and calls `resubscribe()` itself from `_on_open`
on every connect after the first; an application that also resubscribes from its own
reconnect handler sends everything twice. Read the SDK's reconnect path, then choose one
owner of resubscription and disable the other. See `references/standards.md` for the
per-venue detail.

### 4. Tear down the old session before opening a new one

Some venues admit only one live session. Alpaca's connection limit is plan-dependent and
is commonly **1**, so a reconnect that races the abandoned session is rejected with error
**406 `connection limit exceeded`** — the reconnect loop then backs off against a limit
that only its own zombie socket is holding. On IBKR's TWS API, market data lines are a
quota (100 by default) and an uncancelled line stays consumed, so a bulk resubscription
can be truncated by the quota rather than by any error the client notices.

Close the old socket explicitly, drop the SDK object rather than reusing it, and only then
dial.

### 5. Back off with a bounded, randomised delay

`calculate_backoff(attempt)` grows exponentially from `base_delay`, caps at `max_delay`,
applies a symmetric `jitter_pct` band, then clamps the result back inside
`[MIN_BACKOFF_SEC, max_delay]`.

The clamp is the part that is easy to get wrong: applying jitter *after* the cap and
returning that value directly lets the delay exceed the stated maximum by the full jitter
percentage, so `max_delay` stops being a maximum. The exponent is also capped
(`MAX_BACKOFF_EXPONENT`), because `base_delay * 2 ** (attempt - 1)` raises `OverflowError`
once `attempt` passes roughly 1025 — reachable in a long outage with a counter that only
resets on success, and a crash there kills the reconnect loop precisely when it is needed.

RFC 6455 §7.2.3 asks for randomisation on reconnect after abnormal closure. A symmetric
band decorrelates clients; full jitter (uniform over `[0, cap]`) spreads a large fleet
further, at the cost of sometimes reconnecting sooner than the backoff schedule implies.

### 6. Re-authenticate, then resubscribe — in that order

Alpaca returns **401 `not authenticated`** for a subscribe sent before auth and **404
`auth timeout`** if auth never arrives. A reconnect handler that jumps straight to
resubscription on a stream that requires per-connection auth produces a connected socket
carrying no data.

### 7. Resubscribe fresh from desired state

Issue one subscribe derived from the snapshot. Never replay an append-only log of past
subscribe calls: each reconnect would re-apply everything an earlier reconnect already
restored, and the subscription count grows with every cycle of a long-running session.

### 8. Reconcile the acknowledgement — a send is not a subscription

`subscribe_fn` returning means bytes were written, nothing more. IBKR's own API guidance
is that a client should not proceed assuming the connection is fine when an issued request
has not produced its expected callback.

Pass the venue's confirmation into `reconcile_subscriptions(confirmed)`:

- `missing` — desired symbols the broker did not confirm. This is a silent coverage gap:
  the stream looks healthy and simply never carries those instruments.
- `unexpected` — symbols the broker holds that are no longer desired. These are duplicate
  or stale subscriptions consuming a quota slot and delivering ticks nothing is consuming.

Alpaca makes this exact: every subscribe is answered with the session's entire current
subscription list. Where a venue acknowledges asynchronously, call
`reconcile_subscriptions()` from the ack handler; where it answers synchronously, return
the confirmed collection from `subscribe_fn` and `on_reconnect()` reconciles it inline and
records the result on the `ReconnectEvent`.

### 9. Backfill the gap *after* resubscribing

`on_reconnect()` resubscribes first, then calls
`backfill_fn(symbols, disconnect_wall, resubscribed_wall)`.

The ordering is the correctness point. Backfilling first bounds the REST request at a
moment *before* the stream is live, leaving a second, silent gap between the end of the
backfill window and the first live tick. Resubscribing first makes the window overlap the
live stream instead — and overlap is recoverable (the deduplicator absorbs it) while a gap
is not.

A backfill failure is recorded on the event as `backfill_error` rather than raised, but a
non-empty `backfill_error` means the gap is unfilled: treat it as missing data, not as a
warning.

### 10. Watch for subscriptions that expire without a disconnect

Connection-triggered resubscription cannot see a subscription that dies while the socket
stays healthy. On IBKR's Client Portal Web API, `smd` market-data requests terminate after
10 minutes and must be re-issued. Where a venue does this, drive resubscription from a
subscription-lifetime timer as well as from connection events, and treat a symbol that has
produced no tick for longer than its expected quiet period as a reconciliation trigger.

### 11. Measure and log the gap on a monotonic clock

`gap_duration_sec` is `time.monotonic()` arithmetic; the wall-clock stamps are recorded
separately and used only for the REST window and the audit record. Wall-clock arithmetic
across an outage is corrupted by exactly the event most likely to happen during one — an
NTP step correcting a drifted host — and a stepped clock yields a negative or wildly
inflated gap that then sizes the backfill request.

`reconnect_history` is a bounded deque so a long-lived process on a flapping link cannot
grow it without limit.

### 12. Deduplicate downstream as a second line of defence

`TickDeduplicator` keys on `(symbol, timestamp, seq_num)` over a bounded sliding window,
under a lock. Supply a real per-message sequence number wherever the feed carries one:
with `seq_num=None` the key degenerates to `(symbol, timestamp)`, and on a feed whose
timestamp resolution is coarser than its tick rate — Kite's `exchange_timestamp` is
one-second — two genuinely different ticks collide and the second is dropped as a false
duplicate. Dropping real ticks to suppress imaginary duplicates is a worse failure than
the one being defended against.

## Known Failure Modes

- **Both sides resubscribing.** The SDK restores subscriptions on reconnect *and* the
  application does too, because nobody read the SDK's reconnect path.
- **Append-only subscription logs.** Replaying the subscribe log on reconnect, so the
  subscription count and duplicate tick volume grow with every cycle.
- **Silent half-open sockets.** No close frame, no error, no reconnect — the bot believes
  it is streaming and the strategy simply sees a flat market.
- **"Reconnected" read as "nothing missed".** Proceeding without accounting for the window
  between disconnect and resubscription.
- **Backfill window closed too early.** Backfilling before resubscribing, leaving an
  unfilled sliver that never appears in any log.
- **Zombie session holding the only connection slot.** The reconnect is rejected by the
  venue's connection limit because the old socket was never closed.
- **Quota-truncated resubscription.** More desired symbols than the entitlement allows, so
  part of the set never resubscribes and no error is surfaced to the strategy.
- **Unbounded reconnect state.** Reconnect history and deduplicator state growing for the
  life of the process.

## Production Implementation Reference

- Reference code: `scripts/reconnect_manager.py` (`WebSocketReconnectEngine`,
  `TickDeduplicator`, `SubscriptionReconciliation`, `ReconnectEvent`).
- Automated unit tests: `scripts/test_reconnect_manager.py`.
