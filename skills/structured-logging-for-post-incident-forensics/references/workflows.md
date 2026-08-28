# Deep Workflow Reference — structured-logging-for-post-incident-forensics

## A. Instrumenting a trading system

### 1. Wire the sink before you wire the call sites

`ForensicLogger` formats records and hands them to a `logging.Logger`. With no handler
attached, `emit` succeeds and the record goes nowhere.

```python
import logging
from logging.handlers import RotatingFileHandler
from structured_logger import (
    DEFAULT_REDACT_KEYS, EventType, ForensicLogger, Severity, new_correlation_id,
)

sink = logging.getLogger("forensic")
sink.setLevel(logging.DEBUG)
sink.propagate = False                      # keep JSONL out of the root handlers
handler = RotatingFileHandler("forensic.jsonl", maxBytes=256 * 1024 * 1024, backupCount=40)
handler.setFormatter(logging.Formatter("%(message)s"))   # the record IS the message
sink.addHandler(handler)

oms_log = ForensicLogger(component="oms", sink=sink)
```

Two details matter:

- **`propagate = False`.** Otherwise every JSON record is also emitted by the root
  logger's handlers, doubling volume and interleaving JSONL into your console log.
- **`Formatter("%(message)s")`.** Any other format string wraps the JSON object in
  prose and the file stops being JSONL.

Rotation size and `backupCount` are the only retention knobs a local file gives you.
Where a retention *obligation* exists (see `references/standards.md`), a rotating file on
the trading host is not the answer — ship to a store that enforces it.

### 2. Mint the correlation ID at the causal origin

```python
cid = new_correlation_id()
oms_log.emit(EventType.STRATEGY_SIGNAL, "Momentum long triggered",
             correlation_id=cid,
             metadata={"symbol": "AAPL", "z_score": 2.31, "model": "mom-v4"})
```

Mint it where the *decision* happens, not where the order is submitted. An ID first
created at submission time cannot answer why the order existed, which is the question a
post-incident review asks first.

Thread `cid` through every downstream call. An `emit` without `correlation_id` gets a
fresh one and becomes an orphan event that joins to nothing.

### 3. Emit at every state transition, splitting request from confirmation

```python
oms_log.emit(EventType.ORDER_PLACED, "Submitting BUY 100 AAPL LMT 150.25",
             correlation_id=cid,
             metadata={"symbol": "AAPL", "side": "BUY", "qty": 100,
                       "limit_px": 150.25, "venue": "XNAS",
                       "client_order_id": "c-8891"})

# ... broker responds ...
oms_log.emit(EventType.ORDER_ACKNOWLEDGED, "Venue accepted",
             correlation_id=cid,
             metadata={"broker_order_id": "B-55231", "client_order_id": "c-8891"})

# ... cancel path ...
oms_log.emit(EventType.ORDER_CANCEL_REQUESTED, "Cancel sent", correlation_id=cid,
             metadata={"broker_order_id": "B-55231"})
# ONLY when the venue confirms:
oms_log.emit(EventType.ORDER_CANCELLED, "Venue confirmed cancel", correlation_id=cid,
             metadata={"broker_order_id": "B-55231", "leaves_qty": 0})
```

The interval between `ORDER_CANCEL_REQUESTED` and `ORDER_CANCELLED` is the window in
which the order was still live and still fillable. A taxonomy that collapses the two
makes that window unrecoverable — and it is the window in which the fill you are trying
to explain usually happened. The same applies to `ORDER_PLACED`/`ORDER_ACKNOWLEDGED` (an
ambiguous submission: see `order-placement-idempotency`) and to
`ORDER_MODIFY_REQUESTED`/`ORDER_MODIFIED`.

### 4. Log from the failure path, and expect the failure path to be hostile

```python
try:
    response = broker.place_order(payload)
except BrokerTimeout as exc:
    oms_log.emit(EventType.SYSTEM_ERROR, "Order submission timed out — state ambiguous",
                 correlation_id=cid, severity=Severity.ERROR,
                 metadata={"client_order_id": "c-8891", "exc": exc,
                           "elapsed_s": elapsed, "will_retry": False})
```

`exc` is an exception object, not a string; a raw `json.dumps` would need a `default`
hook and would still fail on other shapes. Sanitisation reduces it to a bounded `repr`.
That is the design point: **`emit` never raises**, so an already-degraded path is not
made worse by the thing recording it. Nothing here is allowed to fail closed.

### 5. Never let a credential reach `metadata`

Redaction covers exact key matches (`api_key`, `access_token`, `private_key`, …) at any
nesting depth, and the set is extendable:

```python
oms_log = ForensicLogger(component="oms", sink=sink,
                         redact_keys={*DEFAULT_REDACT_KEYS, "client_code", "pan"})
```

It does **not** cover a secret inside a free-text `message`, inside a URL query string,
or under a key you did not anticipate. Where records land in an immutable store the
mistake is permanent — the SEC Rule 17a-4(f) audit-trail alternative preserves every
version of a record by design.

---

## B. Reconstructing an incident

### 1. Establish what you are reading

```python
status = oms_log.buffer_status()
# {'instance_id': '4711-a3c9e02b', 'component': 'oms', 'capacity': 100000,
#  'emitted': 812, 'retained': 812, 'evicted': 0, 'first_retained_seq': 1,
#  'sink_failures': 0, 'complete': True}
```

`complete is False` means the in-memory history is not the history: events were evicted
or a sink write failed. A timeline reconstructed from a truncated buffer returns fewer
events than happened and **looks whole**. Replay the durable sink instead.

For any incident older than the process, the buffer is irrelevant by construction —
start from the JSONL.

### 2. Pull the timeline

```python
for entry in oms_log.reconstruct_timeline(cid):
    print(entry["seq"], entry["elapsed_ms"], entry["event_type"], entry["message"])
```

`elapsed_ms` is measured from the first event of the timeline on the **monotonic** clock,
which cannot step backwards under NTP correction. It is `None` when the timeline spans
more than one `instance_id`, because two processes' monotonic clocks share no epoch.

### 3. Reading back from the durable sink

```bash
# one order's lifecycle, in the only correct order
jq -c 'select(.correlation_id == "8f14e45fceea167a5a36dedd4bea2543")' forensic.jsonl \
  | jq -s 'sort_by(.instance_id, .seq) | .[]'

# every mis-instrumented call site
jq -c 'select(.metadata | has("_invalid_severity") or has("_unknown_event_type"))' forensic.jsonl

# the cancel race: requests with no confirmation
jq -c 'select(.event_type | test("ORDER_CANCEL"))' forensic.jsonl
```

**Sort by `(instance_id, seq)`, never by file order and never by `ts_ns`.** The lock is
released before the sink write so that logging does not serialise the order path, which
means concurrent emitters can land out of order in the file. And a wall clock can step:
`ts_ns` is for correlating against external evidence (venue drop copies, exchange
timestamps, another host's logs), not for ordering your own events.

### 4. Merging logs from several processes

Every record carries `instance_id`. Concatenate the files, sort by
`(instance_id, seq)` *within* each instance, and interleave the instances on `ts_ns` —
accepting that the cross-instance interleave is only as good as the clock synchronisation
behind it. That accuracy bound is a separate obligation and a separate skill
(`clock-synchronization-ptp-for-trading-hosts`); this schema records the timestamp, it
does not improve it.

---

## C. Migration from schema 1.0.0

`SCHEMA_VERSION` moved to `2.0.0` because the record shape changed. A reader that must
handle both should branch on `schema_version`, whose absence identifies a 1.x record.

| 1.0.0 | 2.0.0 | Reason |
|---|---|---|
| `ts` (float epoch seconds) | `ts_ns` (int ns) + `ts_iso` (RFC 3339) | Float seconds spend precision on the seconds; integer nanoseconds match the OpenTelemetry `Timestamp` type and carry a 1 µs granularity obligation without loss. |
| — | `mono_ns` | Elapsed times that survive an NTP step. |
| — | `instance_id` | `seq` restarts at 1 per process; alone it does not order merged or post-restart logs. |
| — | `severity_number` | OpenTelemetry `SeverityNumber`, so the level survives a pipeline move. |
| — | `schema_version` | These records outlive their parser. |
| `correlation_id` 12 chars | 32 lowercase hex | ~44 bits collides silently; W3C Trace Context `trace-id` shape. |
| `severity` free string | closed set of 5 | An open field degrades into variants no single query catches. |

Behavioural changes with no schema footprint: `emit` no longer raises on any input; the
event buffer is a bounded ring (`DEFAULT_BUFFER_CAPACITY = 100_000`) rather than an
unbounded list; records go to the `forensic` logger by default rather than to
`structured_logger`, which is now reserved for the module's own diagnostics; `metadata`
is snapshotted and redacted at emit time; `StructuredLogEvent` is frozen; and
`reconstruct_timeline` carries timestamps and warns when the buffer has evicted.

## Production Implementation Reference

- Code: `scripts/structured_logger.py` — `ForensicLogger`, `StructuredLogEvent`,
  `EventType`, `Severity`, `new_correlation_id`, `sanitize_metadata`.
- Tests: `scripts/test_structured_logger.py` — 56 tests, including the regression
  classes that pin each v1.0.0 defect.
- Schema and regulatory scope: `references/standards.md`.
