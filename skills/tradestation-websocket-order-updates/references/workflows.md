# Deep Workflow Reference — tradestation-websocket-order-updates

This file holds the full technical procedure referenced by `SKILL.md`. Load this
when actually implementing the skill, not just when deciding whether it applies.
Endpoint, field and limit citations live in `references/standards.md`.

## Full Procedure

### 1. Select the environment before anything else

```
LIVE = https://api.tradestation.com
SIM  = https://sim-api.tradestation.com
```

The host selects the environment. Account id prefixes do not. A configuration
that picks the environment from an account id string will, on the day someone
adds a live account to a paper config, place real orders. Assert the pairing at
startup and fail closed.

### 2. Open the stream

```
GET {base}/v3/brokerage/stream/accounts/{accounts}/orders
Authorization: Bearer {access_token}

Transfer-Encoding: chunked
Content-Type: application/vnd.tradestation.streams.v3+json
```

`accounts` is 1–25 comma-separated account IDs. At most 40 order streams may be
open concurrently for the authenticated user, so batch accounts into one stream
rather than opening one per account.

### 3. Reassemble before parsing

TradeStation documents that HTTP chunk boundaries are not application message
boundaries and that intermediate proxies re-chunk streams. Buffer bytes, split on
complete JSON values, and only then classify. Treating a chunk as a frame appears
to work in development and silently truncates a JSON object in production.

`classify_frame()` reports an incomplete value as `FRAME_MALFORMED` rather than
guessing — if you see those in the logs, the framing layer above it is wrong.

### 4. Classify every frame

| Frame kind | Action |
|---|---|
| `FRAME_HEARTBEAT` | Reset the stall timer; advance the broker-time anchor from `Timestamp` |
| `FRAME_STREAM_STATUS` = `EndSnapshot` | Initial replay finished; subsequent frames are live changes |
| `FRAME_STREAM_STATUS` = `GoAway` | **Terminate the request**, back off, reconnect, run catch-up |
| `FRAME_ERROR` | **Terminate the request**, back off, reconnect, run catch-up |
| `FRAME_ORDER` | Deduplicate, apply, then commit |
| `FRAME_MALFORMED` / `FRAME_EMPTY` / `FRAME_UNKNOWN` | Count and log; these are liveness but not data |

`parse_stream_message()` raises `TradeStationStreamError` for `GoAway` and error
frames precisely so that a caller cannot accidentally treat them as "no order in
this frame". Callers that prefer branching to catching should use
`classify_frame()` and check `StreamFrame.requires_reconnect`.

### 5. Detect stalls, do not wait them out

TradeStation sends a heartbeat after 5 seconds of an idle stream. Silence beyond
the threshold (default 15 s ≈ three missed heartbeats) means the connection is
hung — a half-open TCP socket that will never raise on its own and can sit there
for the OS keepalive interval, which is measured in hours by default.

Measure with `time.monotonic()`, never `time.time()`: an NTP correction or a DST
change on a wall clock can fabricate a stall or, worse, mask one.

```python
if mgr.is_stream_stalled():
    mgr.mark_disconnected()
    transport.close()          # force the read to fail
    # then: backoff, reconnect, mgr.mark_connected(), catch up
```

### 6. Apply, then commit

```python
update = mgr.parse_stream_message(line)        # raises on GoAway / error frames
if update is not None and not mgr.is_duplicate(update):
    ledger.apply(update)                       # durable; may raise
    mgr.mark_processed(update)                 # only on success
```

`is_duplicate()` records nothing. The ordering is the whole point: if
`mark_processed()` ran first and `ledger.apply()` then failed, the next
reconnect's catch-up would classify the event as already seen and the fill would
be lost permanently. Committing after the apply degrades a crash into a
*duplicate*, and duplicates are harmless here because order frames are cumulative
snapshots applied by assignment.

### 7. Apply by assignment, never by accumulation

```python
# WRONG - double-counts on every reconnect snapshot replay
position[order_id] += update.filled_quantity

# RIGHT - cumulative snapshot, idempotent under replay
position[order_id] = update.filled_quantity
```

`Legs[].ExecQuantity` is the total executed so far on that leg. The size of the
newest execution is the difference between consecutive snapshots, if you need it.
For multi-leg orders, account per leg via `update.legs`; the scalar
`update.filled_quantity` is the sum across legs and is only meaningful on
single-leg orders.

### 8. Reconcile the gap on reconnect

Two queries, unioned, because neither covers an outage alone:

```
GET {base}/v3/brokerage/accounts/{accounts}/orders
    # today's orders and open orders; takes NO 'since' parameter

GET {base}/v3/brokerage/accounts/{accounts}/historicalorders?since={YYYY-MM-DD}
    # closed orders only; 'since' is a DATE, limited to 90 days
```

An order that both opened and closed inside the outage window is missing from the
first and present in the second. An order still working is the reverse.

```python
def fetch(since_date: str) -> list[dict]:
    rows = paginate(f"{base}/v3/brokerage/accounts/{accounts}/orders")
    rows += paginate(
        f"{base}/v3/brokerage/accounts/{accounts}/historicalorders",
        params={"since": since_date},
    )
    return rows

for update in mgr.reconcile_missed_orders(fetch):
    ledger.apply(update)
    mgr.mark_processed(update)
```

`reconcile_missed_orders()` derives `since` from the broker's own timestamps
(`ClosedDateTime` / `OpenedDateTime` / heartbeat `Timestamp`), clamps it into the
90-day window, dedupes the overlap between the two endpoints and against
already-committed state, and commits nothing itself.

### 9. Paginate, or silently lose the tail of the gap

`pageSize` caps at 600 (and is the default). Responses carry a `nextToken` valid
for **one hour**, used only in the immediately following request. `paginate()`
above must loop on it until a page returns fewer rows than requested. A fetch
that issues one request and stops recovers at most 600 orders and reports no
error for the rest.

Budget the requests: order details share a 320-request rolling 5-minute quota. A
reconnect loop that re-queries on every attempt will turn a brief outage into a
`429`.

### 10. Bound the deduplication state

`max_tracked_signatures` (default 10 000) caps the signature set with
oldest-first eviction. Eviction is safe *because* application is idempotent: a
re-delivered state that has aged out is re-applied by assignment, which is a
no-op. Size it against expected order-update volume per session, not per day.

## Known Failure Modes

- **Zero-quantity fills.** Reading `FilledQuantity` / `AveragePrice` — fields v3
  does not have — records every fill as zero. If that zero also feeds the dedupe
  key, consecutive `FPR` frames on one order collapse to one signature and every
  partial fill after the first is dropped without a log line.
- **Silent death on `GoAway`.** A parser that returns "no order" for control
  frames leaves the reader blocked on a socket the server has already abandoned.
  The bot believes it is connected and receives no further fills.
- **At-most-once loss.** Marking a signature processed inside the duplicate check
  means a consumer that crashes between receipt and persistence can never see the
  event again — catch-up suppresses it.
- **Double-counted positions after reconnect.** Accumulating `ExecQuantity`
  across the snapshot the stream replays on connect.
- **Invalid catch-up query.** Sending `?since=1756800000.123`; `historicalorders`
  wants a date, and `/orders` has no `since` at all.
- **Truncated recovery.** Ignoring `nextToken` and recovering only the first 600
  orders of a long outage.
- **Crash on a malformed field.** `float("n/a")` raising inside the read loop and
  taking the connection down.
- **Frozen socket deadlock.** No heartbeat-staleness monitor, so a half-open TCP
  connection is never detected.

## Production Implementation Reference

- Reference code: `scripts/tradestation_stream.py` — `TradeStationStreamManager`,
  `TradeStationOrderUpdate`, `OrderLegFill`, `StreamFrame`, `build_order_update`.
- Automated unit tests: `scripts/test_tradestation_stream.py`.
- The module is I/O-free by design: transport, backoff and pagination belong to
  the caller, which is what keeps every rule above deterministically testable.
