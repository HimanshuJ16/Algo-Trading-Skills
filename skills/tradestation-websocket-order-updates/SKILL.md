---
name: tradestation-websocket-order-updates
description: Use when consuming TradeStation's v3 order update stream (its
  "WebSocket" order feed, actually HTTP chunked streaming) to classify stream and
  control frames, detect stalled connections via heartbeats, reconcile gaps through
  REST catch-up on reconnect, and deduplicate cumulative fill snapshots across
  network reconnects
domain: algorithmic-trading
subdomain: broker-integration
tags:
- broker-integration
- tradestation-api
- websocket-stream
- order-updates
- gap-reconciliation
brokers_frameworks:
- TradeStation WebAPI v3
version: "2.0.0"
author: algo-trading-skills-contributors
license: Apache-2.0
---

## When to Use

Invoke this whenever a bot consumes real-time order and fill updates from
TradeStation so that a disconnect cannot leave its position ledger wrong. It
covers frame classification (order / heartbeat / `EndSnapshot` / `GoAway` /
error), stalled-connection detection, REST catch-up across the offline window,
and deduplication of the cumulative order snapshots the stream emits.

Two facts drive everything below, and both contradict the shape most people
assume:

- **The transport is not a WebSocket.** TradeStation serves order updates over
  RFC2616 HTTP/1.1 chunked streaming — an ordinary `GET` whose body never ends.
  This skill keeps the historical `websocket` slug for discoverability; the wire
  protocol is HTTP streaming. There is no `/v2/stream/orders` endpoint: order
  streaming exists only in **v3**.
- **Every order frame is a full cumulative snapshot, not a delta.**
  `Legs[].ExecQuantity` is the total executed so far. A ledger that does `+=` on
  each frame double-counts on the first reconnect, because the stream replays a
  snapshot of current orders on connect.

## When NOT to Use

Do **not** use this skill for order *placement* idempotency (see
`order-placement-idempotency`), for backoff/jitter policy (see
`websocket-reconnection-with-state-recovery`), or for any other broker — the
frame shapes and status codes here are TradeStation-specific.

## Prerequisites

- TradeStation API v3 OAuth access token with the `ReadAccount` scope.
- Correct **base URL for the environment**: `https://api.tradestation.com` for
  live, `https://sim-api.tradestation.com` for the simulator. The environment is
  selected by the host, *not* by the account id — a paper account id sent to the
  live host trades real money.
- Account ID(s), comma-separated, 1–25 per stream.
- An order ledger that applies order state by **assignment**, not accumulation,
  and can be committed durably before the update is marked processed.
- Awareness of the documented limits: 40 concurrent order streams, and 320
  requests per rolling 5 minutes on the order-details resource used for catch-up.

## Workflow

1. **Open the stream.** `GET {base}/v3/brokerage/stream/accounts/{accounts}/orders`
   with `Authorization: Bearer {token}`. Expect `Transfer-Encoding: chunked` and
   `Content-Type: application/vnd.tradestation.streams.v3+json`.

2. **Reassemble frames before parsing.** HTTP chunk boundaries are *not* message
   boundaries — proxies re-chunk freely, so one chunk may hold several JSON
   objects and one object may span chunks. Buffer until a complete JSON value is
   available; only then call `classify_frame()`. A fragment is a framing bug, not
   an order.

3. **Classify every frame, and branch on the control frames.**
   - `{"Heartbeat": N, "Timestamp": ...}` — liveness only; reset the stall timer.
   - `{"StreamStatus": "EndSnapshot"}` — the initial replay of current orders is
     done; frames after this are live changes.
   - `{"StreamStatus": "GoAway"}` — the server is shutting down. Terminate the
     request, back off, reconnect, run catch-up. Treating this as a no-op is how
     a bot goes silently dark while believing it is connected.
   - `{"Error": ..., "Message": ...}` — terminate the request and reconnect; do
     not keep reading.
   - Anything with an `OrderID` — an order snapshot.

4. **Detect stalls from heartbeat silence.** TradeStation sends a heartbeat after
   5 seconds of an idle stream, so silence beyond ~15 seconds (three missed
   heartbeats) means the socket is hung, not quiet — a state TCP will not report
   on its own. Measure on a *monotonic* clock so an NTP step cannot fabricate or
   mask a stall. On stall: tear the connection down rather than waiting longer.

5. **Apply, then commit — in that order.** `is_duplicate()` is a pure query;
   nothing is remembered until `mark_processed()`. Persist the update first, then
   mark it. Marking first turns the stream into at-most-once delivery: a crash in
   between loses the fill permanently, because catch-up will suppress it as a
   duplicate.

6. **Reconcile the gap on reconnect, with the right query shape.** There is no
   "everything since epoch-seconds N" call. Query **both**:
   - `GET /v3/brokerage/accounts/{accounts}/orders` — today's and open orders.
     It takes **no** `since` parameter.
   - `GET /v3/brokerage/accounts/{accounts}/historicalorders?since={date}` —
     closed orders, which the first endpoint omits. `since` is a **date**
     (`2026-09-02`), limited to 90 days, so catch-up necessarily re-reads whole
     days and leans on deduplication to absorb the overlap.

   Derive `since` from the broker's own event timestamps (`ClosedDateTime` /
   `OpenedDateTime` / heartbeat `Timestamp`), never the local clock: the query is
   evaluated against the broker's clock, so local skew silently narrows the
   recovery window. `catch_up_since_date()` does this and clamps to the 90-day
   limit.

7. **Paginate the catch-up fully.** Both endpoints cap a page at 600 orders and
   return a `nextToken` valid for one hour. A fetch that ignores it truncates
   recovery at 600 orders and drops the rest of the gap without any error.

8. **Deduplicate on the full order state.** The key must cover per-leg executed
   quantity, not a summed total: a two-leg order that fills 1/9 then 9/1 has an
   unchanged total and is a genuinely different state. Normalise decimal strings
   so the stream's `"100"` and REST's `"100.00"` produce one key.

> Full step-by-step procedure with broker-specific detail: see `references/workflows.md`.
> Sourced endpoint, field and limit tables: see `references/standards.md`.
> Printable pre-flight checklist: see `assets/checklist.md`.

## Common Pitfalls

- **Reading `FilledQuantity` / `AveragePrice`.** Neither field exists on a
  TradeStation v3 order. Executed quantity lives on `Legs[].ExecQuantity`; the
  average fill price is the top-level `FilledPrice`. Code that reads the absent
  names silently records zero for every fill — and if that zero feeds the
  deduplication key, every partial fill after the first on an order collapses to
  one signature and is dropped.
- **Treating `GoAway` or an error frame as "nothing to do".** Both oblige the
  client to end the HTTP request. A parser that returns "no order here" for them,
  the same as for a heartbeat, leaves the bot reading a dead socket forever.
- **Marking an event processed before the ledger has committed it.** The window
  between "received" and "persisted" is exactly where a crash loses a fill that
  catch-up would otherwise have recovered.
- **Accumulating `ExecQuantity` instead of assigning it.** Cumulative snapshots
  plus `+=` equals a double-counted position on the first reconnect, because the
  stream replays current orders on connect.
- **Sending a Unix timestamp as `since`.** `historicalorders` takes a date and
  nothing finer, and `/orders` takes no `since` at all. Sub-day precision is not
  available; overlap and dedupe instead.
- **Reconciling only against `/orders`.** It returns today's and open orders, so
  an order that opened *and* closed inside the outage window is absent from it.
  `historicalorders` is where it will be.
- **Using HTTP chunks as message delimiters.** TradeStation documents that
  proxies re-chunk streams, so this "works" until it silently truncates a JSON
  object in production.
- **Letting one bad field kill the loop.** `float("n/a")` on an unexpected
  payload raises mid-stream and takes the connection down with it. Coerce
  defensively and log.
- **Confusing `FPR` with `FLP`.** `FPR` is "Partial Fill (Alive)" — more can
  still execute. `FLP` is "Partial Fill (UROut)" — the remainder was cancelled
  and the order is done. Treating `FPR` as terminal abandons a working order.
- **Unbounded dedupe state.** A signature set that only grows is a slow memory
  leak in a process meant to run for months.

## Verification

- Feed a v3 order frame with `Legs[].ExecQuantity` and confirm the extracted
  quantity and `FilledPrice` are non-zero — the regression that zero fills come
  from reading v2-era field names.
- Feed three successive `FPR` frames on one order (2 → 5 → 9 of 10) and confirm
  three distinct events survive deduplication.
- Feed `{"StreamStatus": "GoAway"}` and an `{"Error": ...}` frame and confirm each
  forces a reconnect rather than being silently swallowed.
- Advance a fake monotonic clock past the stall threshold and confirm
  `is_stream_stalled()` trips; confirm a heartbeat resets it.
- Confirm `catch_up_since_date()` returns a `YYYY-MM-DD` string derived from
  broker event time and clamped to 90 days.
- Confirm `reconcile_missed_orders()` returns uncommitted states only, dedupes
  the overlap between the two endpoints, and commits nothing itself.
- Run the unit suite: `python -m unittest discover -s skills/tradestation-websocket-order-updates/scripts`.

## Related Skills

- `websocket-reconnection-with-state-recovery`
- `websocket-reconnect-without-duplicate-subscriptions`
- `order-placement-idempotency`
- `sandbox-vs-production-endpoint-drift`
- `webhook-based-order-fill-notifications`
