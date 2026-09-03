---
name: websocket-reconnect-without-duplicate-subscriptions
description: Use when writing reconnection logic for a broker market-data WebSocket, to
  restore exactly the intended subscription set after a drop — reconciling the broker's
  own acknowledgement instead of assuming the subscribe succeeded, backfilling the gap
  window in the right order, and deduplicating ticks replayed across the reconnect
  boundary
domain: algorithmic-trading
subdomain: real-time-architecture
tags:
- real-time-architecture
- websocket
- resubscription
- duplicate-prevention
- gap-backfill
- connection-recovery
brokers_frameworks:
- Zerodha Kite Connect v3 WebSocket
- Alpaca Market Data Stream
- IBKR Client Portal Web API
- Fyers Data WebSocket
version: "2.0.0"
author: algo-trading-skills-contributors
license: Apache-2.0
---

## When to Use

Invoke this whenever a bot's WebSocket client has auto-reconnect logic — which any
production bot must have, since network blips and broker-side restarts are routine rather
than exceptional. Naive reconnect implementations fail in one of two directions: they
resubscribe to instruments that are already subscribed (duplicate ticks downstream, or a
duplicate session the venue rejects), or they fail to restore the full set (a silent
coverage gap that surfaces weeks later as "why did the strategy miss that move").

One fact drives most of the workflow below, and it contradicts the shape most reconnect
code assumes:

**A subscribe that was sent is not a subscription that exists.** The socket write
succeeding tells you nothing about whether the venue accepted the symbols, whether it
accepted all of them, or whether a stale session is still holding the entitlement. IBKR's
own API guidance is that a client should not proceed assuming the connection is fine when
an issued request has not produced its expected callback. Alpaca answers every subscribe
with the session's *entire* current subscription list, precisely so it can be checked.
Reconcile; do not assume.

## When NOT to Use

Do **not** use this skill for: connection *state-machine* design and sequence-number gap
recovery (see `websocket-reconnection-with-state-recovery`); detecting missing sequence
numbers within a healthy stream (see `sequence-number-gap-detection-for-feeds`); deciding
whether to fall back to REST polling when reconnection keeps failing (see
`graceful-degradation-to-polling-fallback`); or order-submission idempotency, which is a
different problem with different rules (see `order-placement-idempotency`).

## Prerequisites

- A single authoritative list of "instruments the bot intends to be subscribed to", held
  independently of the WebSocket connection object and mutated only by strategy logic.
- A liveness mechanism — venue heartbeats, or your own Ping/Pong plus a staleness timer on
  a monotonic clock. Without one, a half-open TCP connection is undetectable and none of
  the reconnect logic below ever runs.
- Knowledge of what the broker SDK already does on reconnect. `pykiteconnect`'s
  `KiteTicker` resubscribes by itself; adding your own resubscribe on top sends everything
  twice.
- The venue's concurrent-connection limit, symbol cap, and (where applicable) subscription
  expiry — all three can truncate a resubscription without raising anything the strategy
  sees. See `references/standards.md`.
- A REST historical/quote endpoint, if the strategy needs the gap window filled rather
  than merely recorded.

## Workflow

1. **Keep desired state outside the connection.** The connection is disposable and is
   recreated on every reconnect; the desired-symbol set persists across all of them and is
   the only source of truth for what should be subscribed. Guard it — the SDK delivers
   close and error callbacks on its network thread while strategy code is still mutating
   the set.

2. **Detect the drop that announces itself and the one that does not.** A clean close and
   an abnormal one (RFC 6455 code 1006, no Close frame) both surface as events. A
   *half-open* connection surfaces as nothing: the socket stays open, no error fires, and
   the bot goes silently dark believing it is streaming. Track the last inbound frame on a
   monotonic clock and treat prolonged silence as a disconnect you must cause yourself.
   Record the outage once — SDKs routinely fire both an error and a close callback for a
   single drop, and letting the second reset the timestamp shrinks the gap you go on to
   backfill.

3. **Decide who owns resubscription before writing any.** Read the SDK's reconnect path
   first. If it restores subscriptions itself, do not also restore them; if it does not,
   you must. Two owners is the most common cause of the exact duplication this skill
   exists to prevent.

4. **Tear the old session down before dialling.** Do not reuse the SDK object across
   reconnects: some clients keep subscription bookkeeping keyed to the old socket and then
   ignore new subscribe calls as redundant. Where the venue admits only one session —
   Alpaca's limit is commonly 1, answered with error 406 — an abandoned socket makes the
   reconnect fail against a limit that only your own zombie connection is holding.

5. **Back off with a bounded, randomised delay.** Exponential growth, capped, jittered,
   then clamped back inside the cap — jitter applied after the cap and returned directly
   means `max_delay` is not actually a maximum. Cap the exponent too: a naive
   `base * 2 ** (attempt - 1)` raises `OverflowError` once the attempt counter passes
   ~1025, killing the reconnect loop during exactly the long outage it exists for.

6. **Re-authenticate, then resubscribe.** On streams with per-connection auth, a subscribe
   sent first is rejected (Alpaca: 401 `not authenticated`), leaving a connected socket
   that carries no data.

7. **Resubscribe fresh from current desired state — never from a replayed log.** Replaying
   an append-only log of subscribe calls re-applies everything an earlier reconnect
   already restored, so the subscription count grows with every cycle of a long session.

8. **Reconcile the acknowledgement against desired state.** Compare what the broker
   confirms to what you wanted: symbols missing from the confirmation are a silent
   coverage gap, symbols present but no longer desired are stale subscriptions burning a
   quota slot. Log both at error level — neither is visible in the tick stream itself.

9. **Backfill the gap after resubscribing, not before.** Backfilling first bounds the REST
   request at a moment before the stream is live and leaves a second, silent gap between
   the end of that window and the first live tick. Resubscribe first, then backfill
   `[disconnect, resubscription complete]`, and let deduplication absorb the overlap:
   overlap is recoverable, a gap is not. A backfill that failed means missing data, not a
   warning to be logged and stepped over.

10. **Do not assume subscriptions only die with connections.** On IBKR's Client Portal Web
    API, `smd` market-data requests terminate after 10 minutes and must be re-issued while
    the socket stays perfectly healthy. Where a venue expires subscriptions, drive
    resubscription from a lifetime timer as well as from connection events.

11. **Log the reconnect record and measure the gap on a monotonic clock.** Disconnect and
    reconnect timestamps, measured gap, symbols restored, whether the backfill ran, and
    the reconciliation result. Wall-clock subtraction across an outage is corrupted by the
    event most likely to occur during one — an NTP step — which yields a negative or
    inflated gap that then sizes the backfill request.

12. **Deduplicate downstream on a real key.** Use the feed's sequence number where one
    exists. A `(symbol, timestamp)` key on a feed whose timestamps are coarser than its
    tick rate discards genuine ticks as duplicates — Kite's `exchange_timestamp` has
    one-second resolution.

> Full step-by-step procedure with broker-specific detail: see `references/workflows.md`.
> Sourced limits, SDK behaviour and protocol references: see `references/standards.md`.
> Printable pre-flight checklist: see `assets/checklist.md`.

## Common Pitfalls

- **Resubscribing on top of an SDK that already resubscribes.** `KiteTicker` calls
  `resubscribe()` from its own connect handler on every reconnect after the first. An
  application-level resubscribe in the same handler duplicates every subscribe message —
  and because `subscribe()` resets each token to `MODE_QUOTE`, a resubscribe that omits
  `set_mode` can silently downgrade the streaming mode as well.
- **Treating "reconnected successfully" as "nothing was missed".** The gap window is real.
  Ticks between disconnect and resubscription are gone unless they are backfilled, because
  WebSocket feeds generally do not replay. Handle it or record it; never step over it.
- **Backfilling before resubscribing.** It looks like the safer order and is not: it closes
  the REST window before the live stream opens and leaves an unfilled sliver that appears
  in no log.
- **Measuring the gap with `time.time()`.** An NTP correction during the outage produces a
  gap that is negative or an hour long, and the backfill request inherits it.
- **Silently normalising symbol case.** Upper-casing every symbol breaks venues where case
  is significant — Binance stream names are lower-case — and the resubscription then
  covers nothing, without error.
- **Reusing SDK objects across reconnects.** Internal bookkeeping keyed to the dead socket
  makes some clients ignore new subscribe calls because they believe they are already
  subscribed.
- **Reconnecting with no backoff.** During a broker-side outage affecting many clients this
  contributes to a thundering herd and earns rate limiting or an IP block; RFC 6455 §7.2.3
  asks for randomised backoff for this reason.

## Verification

- Force a disconnect mid-session (kill the interface, or the broker sandbox connection) and
  confirm the bot resubscribes to **exactly** the pre-disconnect instrument set — no more,
  no fewer — checked against the broker's own subscription acknowledgement rather than
  against your own send.
- Repeat the disconnect five or more times in one session and confirm the subscribe
  payload is identical every cycle. A payload that grows across cycles is a replayed
  subscription log.
- Blackhole the socket without closing it (drop packets, do not send RST) and confirm the
  staleness timer fires a reconnect. If nothing happens, the liveness check is missing and
  every other guarantee here is unreachable.
- Step the host clock backwards during a simulated outage and confirm the recorded gap
  stays positive and correct.
- Confirm no duplicate ticks reach downstream processing after a reconnect, verified by
  sequence number rather than by timestamp alone.
- Confirm the gap backfill (where implemented) covers through the moment resubscription
  completed, and that a backfill failure is surfaced as missing data rather than logged and
  ignored.
- Run `python -m unittest discover -s skills/websocket-reconnect-without-duplicate-subscriptions/scripts`
  and confirm a 100% pass rate.

## Related Skills

- `websocket-reconnection-with-state-recovery`
- `sequence-number-gap-detection-for-feeds`
- `graceful-degradation-to-polling-fallback`
- `producer-consumer-tick-pipeline`
- `market-data-snapshot-plus-delta-reconciliation`
- `token-lifecycle-live-probing`
