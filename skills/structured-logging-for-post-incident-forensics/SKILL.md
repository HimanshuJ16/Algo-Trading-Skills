---
name: structured-logging-for-post-incident-forensics
description: >-
  Designing and emitting a trading system's log records so an incident timeline can be
  reconstructed by query rather than by reading prose: one JSON object per event, a
  32-hex correlation ID linking an order's whole lifecycle, a lock-assigned sequence
  number that orders events when the wall clock cannot, integer-nanosecond timestamps,
  an OpenTelemetry-aligned severity, metadata redaction before anything reaches an
  immutable archive, and an emit path that never raises no matter what it is handed.
domain: algorithmic-trading
subdomain: deployment-ops
tags:
- deployment
- logging
- forensics
- incident-response
- structured-logs
- observability
- correlation-id
- audit-trail
brokers_frameworks:
- Python logging (stdlib)
- JSON Lines
- OpenTelemetry Logs Data Model
- W3C Trace Context
- ELK Stack
- Datadog
version: "2.0.0"
author: algo-trading-skills-contributors
license: Apache-2.0
---

## When to Use

Use this skill when you are designing, retrofitting, or auditing the log records a trading system writes, and the question you need them to answer later is *what exactly happened to this order, in what order, and when?*

An incident review is a reconstruction problem. Unstructured text (`print`, ad-hoc f-strings) cannot be filtered, joined, or ordered, so the reconstruction becomes a human reading a file and inferring. This skill produces the opposite: one JSON object per event, each carrying the identifiers that make the reconstruction mechanical.

`scripts/structured_logger.py` implements the record and the emitter:

- **One JSON object per event**, single line, valid strict JSON — no `NaN`, no forged newlines, no unserialisable object able to break the line.
- **32-lowercase-hex correlation IDs** with the shape and entropy the W3C Trace Context `trace-id` requires, linking signal → order → acknowledgement → fills → position update.
- **Sequence numbers assigned under the same lock that inserts the event**, so `(instance_id, seq)` totally orders everything one process emitted, independent of the clock.
- **Integer-nanosecond timestamps** (`ts_ns`, matching the OpenTelemetry Logs Data Model) plus a monotonic reading (`mono_ns`) for elapsed times that survive an NTP step, plus a rendered RFC 3339 `ts_iso`.
- **A closed event taxonomy** that separates a request from its confirmation (`ORDER_CANCEL_REQUESTED` vs `ORDER_CANCELLED`).
- **Redaction of credential-bearing metadata keys** before serialisation.
- **A total `emit`** — it records and flags bad input rather than raising, because it is called from `except` blocks.

## When NOT to Use

- **As your retention, immutability, or WORM layer.** This module formats records and hands them to a `logging.Logger`. Everything a recordkeeping regime actually requires — durability, retention period, tamper evidence, the SEC Rule 17a-4(f) WORM or audit-trail properties, legal hold — belongs to the handler and the storage behind it. The in-memory ring buffer is a live-debugging aid and is explicitly *not* the record of truth. See `record-retention-periods-by-jurisdiction` and `log-aggregation-and-centralized-observability`.
- **As the source of regulatory order records.** RTS 6 Art. 28 order records, CAT reports, and exchange audit trails have prescribed schemas and prescribed fields; this schema is not one of them and does not claim to be. Application forensics and regulatory reporting are two consumers with different contracts — see `best-execution-record-keeping-global` and `backtest-audit-trail-for-regulatory-review`.
- **As a clock.** Nothing here synchronises or disciplines a clock. `ts_ns` is only as good as the host clock behind `time.time_ns()`. If you have a clock-accuracy obligation (MiFID II RTS 25, FINRA Rule 4590), it is met by `clock-synchronization-ptp-for-trading-hosts` and monitored by `clock-drift-monitoring-alerting-thresholds`, not here.
- **On the tick path without a level gate.** Emitting a record per tick will dominate your I/O, your storage bill, and your aggregator's ingest quota. Log the decision, not the input that produced it; see `adaptive-sampling-under-extreme-tick-rates`.
- **As a distributed tracing system.** There are no spans, no parent/child relationships, and no context propagation across processes. The correlation ID is trace-id-*shaped* so it can be carried into one, not a substitute for one.
- **As a risk control.** Recording a `RISK_BREACH` event is not enforcing a limit. The enforcement lives in `kill-switch-and-drawdown-circuit-breakers`; this skill records that it fired.

## Prerequisites

- Python 3.7+ (`time.time_ns`, `time.monotonic_ns`).
- A configured `logging` handler pointing at a durable, appropriately retained sink — a file with rotation, a syslog socket, or a shipper into ELK/Datadog. Without one, `emit` formats records that go nowhere.
- A decision, before you instrument, about **where the correlation ID is minted**: at the strategy signal, so that every downstream event inherits it. Retrofitting an ID at order-submission time loses the causal step you will most want.
- A written retention target for the sink, taken from your jurisdiction and entity type rather than from this module's defaults.

## Workflow

1. **Mint the correlation ID at the causal origin, not at the order.** Call `new_correlation_id()` when the strategy decides, then thread that ID through signal, pre-trade risk check, submission, acknowledgement, every partial fill, and the position update. An ID minted at submission cannot answer *why* the order existed. Pass it explicitly to every `emit`; an omitted `correlation_id` gets a fresh one, which produces an orphan event that joins to nothing.
2. **Emit at every state transition, and separate a request from its confirmation.** `ORDER_CANCEL_REQUESTED` when you send the cancel, `ORDER_CANCELLED` only when the venue confirms. The gap between the two is the window in which the order was still live and still fillable, and it is the single most common thing a post-incident reconstruction needs and cannot recover from a taxonomy that collapses them. The same split applies to `ORDER_PLACED`/`ORDER_ACKNOWLEDGED` and `ORDER_MODIFY_REQUESTED`/`ORDER_MODIFIED`.
3. **Put the facts in `metadata`, not in the message.** `message` is for a human skimming; `metadata` is what a query filters on. `metadata={"symbol": "AAPL", "qty": 100, "limit_px": 150.25, "venue": "XNAS", "broker_order_id": "..."}` is greppable. The same content inside an f-string is not.
4. **Do not put credentials in `metadata`, and rely on redaction only as the second line.** Keys whose lowercased form is in the redaction set (`api_key`, `access_token`, `private_key`, …) are replaced before serialisation, and the set is extendable per deployment. This is a backstop, not a licence: matching is *exact on the key*, so a secret embedded in a free-text `message`, in a URL query string, or under a key you did not anticipate will pass straight through. Where records land in an immutable store the mistake is permanent — see `sandbox-credential-leakage-prevention`.
5. **Order by `(instance_id, seq)` when reading back — never by file order and never by timestamp.** Sink line order is deliberately not serialised against the lock, because holding a lock across log I/O would put the aggregator's latency on the order path. Wall-clock timestamps step backwards under NTP correction and differ between hosts. The sequence number is the only field that orders events, and it is scoped to one logger instance, which is what `instance_id` disambiguates after a restart or across a merge.
6. **Read elapsed times from `mono_ns`, not from `ts_ns`.** `reconstruct_timeline` reports `elapsed_ms` from the monotonic clock, which cannot step. It withholds the figure (`None`) when the timeline spans instances, because two processes' monotonic clocks share no epoch and differencing them yields a confident, meaningless number.
7. **Check `buffer_status()["complete"]` before trusting an in-memory reconstruction.** The ring buffer evicts oldest-first once full and a sink write can fail; either way `reconstruct_timeline` returns a *partial* timeline that looks whole. `complete` is False the moment anything was evicted or a sink write failed. For any incident older than the buffer, replay the durable sink instead.
8. **Treat a flagged record as a bug report against your instrumentation.** `emit` never raises, so mistakes surface in the data: `_invalid_severity` (a severity string that resolved to nothing), `_unknown_event_type` (a type outside the enum), `_serialization_error` (the record could not be encoded and a degraded placeholder was written). Query for these underscore-prefixed keys periodically; they mark call sites that will produce a weaker record next incident.
9. **Set the retention on the sink from your own obligation.** Nothing in this module enforces or knows a retention period. The regimes that commonly bind, with what they actually say and to whom, are tabulated in `references/standards.md`.

> Full procedure: see `references/workflows.md`.
> Schema, standards, and jurisdictional scope: see `references/standards.md`.
> Printable pre-flight checklist: see `assets/checklist.md`.

## Common Pitfalls

- **A logging call that raises inside an `except` block.** The v1.0.0 emitter resolved the level with `getattr(logging, severity, logging.INFO)`, so `severity="warning"` produced the *function* `logging.warning` and `Logger.log` raised `TypeError: level must be an integer` — from inside the handler that was trying to record the incident. Worse and quieter: `severity="raiseExceptions"` resolved to `True`, and `True == 1`, so the record was emitted below `DEBUG` and no handler ever saw it. Resolve severity from an explicit table, never by attribute lookup into a module.
- **Assuming `json.dumps(..., default=str)` cannot fail.** `default` covers *values* only. A dict keyed by a tuple still raises `TypeError`, a self-referencing structure still raises `ValueError`, and a `NaN` is emitted as the bare token `NaN` — which is not valid JSON, so a strict consumer rejects the entire line. One unpriceable Greek in a `POSITION_UPDATE` silently destroys the record it appears in.
- **Storing the caller's `metadata` dict by reference.** The v1.0.0 emitter did, so a caller that reused and mutated its dict retroactively rewrote history: an order logged as `qty: 100` read back as `qty: 0`. Snapshot at emit time.
- **Truncating the correlation ID.** `str(uuid.uuid4())[:12]` is 11 hex digits — about 44 bits, a 50% birthday collision at roughly 4.2 million IDs, well inside one year of a busy order flow. A collision merges two unrelated lifecycles into one timeline that looks complete and is wrong, which is worse than no timeline at all.
- **Trusting the order lines appear in the file.** Under concurrent emitters, nothing makes sink write order match event order. In v1.0.0 the in-memory buffer had the same problem for a different reason — the counter increment and the append were not atomic together, so roughly 30% of adjacent entries were out of sequence order under eight threads. Sort by `(instance_id, seq)`.
- **Reading a sequence number across a restart or a merge.** Sequence numbers restart at 1 in every new process. Two days of merged logs contain many events numbered 1. Without `instance_id` in the record the "monotonic ordering guarantee" is an ordering of nothing.
- **Computing an incident duration from wall-clock timestamps.** An NTP step mid-incident produces negative durations, or hides a stall. Use the monotonic reading, and only within one instance.
- **Reading a truncated in-memory buffer as the full history.** An unbounded buffer is an OOM; a bounded one silently drops the oldest events — the early part of a long incident, which is the part you need. Either way the timeline you get back does not announce what is missing. Check `complete`.
- **Logging every tick.** Volume is a correctness problem, not just a cost one: an aggregator that drops records under ingest pressure drops them during exactly the burst you are trying to explain.
- **Letting a secret into a record that will be retained immutably.** Under the SEC Rule 17a-4(f) audit-trail alternative the system must preserve every version of a record, so a leaked API key cannot be edited out for the whole retention period. Redaction is a backstop; not putting it there is the control.
- **Interleaving the logger's own diagnostics with the forensic stream.** If the module's warnings and the JSON records share a logger, a JSONL consumer hits a prose line and fails or skips. Keep the sink separate — the default here is `forensic`, distinct from the module's `structured_logger` diagnostic logger.
- **Treating "the event was recorded" as "the risk control fired".** A `RISK_BREACH` record documents a breach; it does not block anything.

## Verification

- Schema, against hand-derived UTC instants: `ts_ns = 1_700_000_000_123_456_789` $\implies$ `ts_iso == "2023-11-14T22:13:20.123456789Z"`; `ts_ns = 1_000_000_000_000_000_000` $\implies$ `"2001-09-09T01:46:40.000000000Z"` $\implies$ confirm no sub-second digit is lost.
- Severity numbers against the OpenTelemetry specification's range bases: DEBUG 5, INFO 9, WARN 13, ERROR 17, FATAL 21.
- Severity regression: `severity="warning"` $\implies$ recorded at `WARNING` (v1.0.0 raised `TypeError`); `severity="raiseExceptions"` $\implies$ recorded at `ERROR` with `_invalid_severity` set, and the handler sees `logging.ERROR` (v1.0.0 emitted at level 1, invisible).
- Serialisation regression: a tuple dict key (v1.0.0 `TypeError`), a self-referencing dict (v1.0.0 `ValueError`), and `float("nan")`/`float("inf")` (v1.0.0 emitted the bare `NaN` token) each produce a record that parses under a strict JSON parser configured to reject `NaN`/`Infinity`.
- Snapshot regression: mutate the metadata dict after `emit` $\implies$ the record still reads `qty: 100` (v1.0.0 read back `qty: 0`).
- Redaction: nested `api_key`, `Authorization`, and `private_key` $\implies$ `[REDACTED]` and the secret string absent from `to_json()`; `token_bucket_size` $\implies$ untouched, confirming whole-key rather than substring matching.
- Correlation ID: 32 lowercase hex characters, never all-zero, 50,000 draws with no repeat.
- Concurrency regression: 8 threads × 500 emits $\implies$ sequence numbers are exactly `1..4000` with no duplicates **and the buffer is held in that order** (v1.0.0: ~30% of adjacent entries inverted); monotonic readings non-decreasing in sequence order.
- Bounded buffer: capacity 10 with 25 emits $\implies$ `retained == 10`, `evicted == 15`, `first_retained_seq == 16`, `complete is False`; `reconstruct_timeline` returns 5 of 12 events on a capacity-5 buffer **and logs a warning** $\implies$ confirm a partial timeline is flagged rather than silently returned.
- Instance scoping: two loggers each report `seq == 1` for their first event and differ in `instance_id`; a timeline spanning two instances reports `elapsed_ms is None`.
- Log forging: a message containing `\n{"seq":999,"forged":true}\n` $\implies$ exactly one sink line, parsing to `seq == 1`.
- Sink failure: a handler that raises `OSError`, and a sink object whose `log` raises $\implies$ `emit` returns normally, the event is retained, and `buffer_status()["sink_failures"] == 1` with `complete is False`.
- Total-emit contract: `event_type=None`, an object whose `__str__` and `__repr__` both raise, a metadata key whose `__str__` raises, a `dict` subclass whose `items()` raises, a `list` subclass whose `__iter__` raises, a `correlation_id` whose `__bool__` raises, a list passed as `metadata`, and `severity=object()` each produce a parseable record rather than an exception.
- Construction: `buffer_capacity` of `0`, `-1` $\implies$ `ValueError`; of `1.5`, `"10"`, `True`, `None` $\implies$ `TypeError` naming the argument, rather than a `TypeError` from inside `deque`.
- Run `python -m unittest discover -s skills/structured-logging-for-post-incident-forensics/scripts` — 56 tests.

## Related Skills

- `log-aggregation-and-centralized-observability`
- `audit-logging-for-configuration-changes`
- `risk-control-bypass-audit-logging`
- `data-lineage-tracking-for-audit-and-debugging`
- `record-retention-periods-by-jurisdiction`
- `data-retention-policy-and-storage-tiering`
- `clock-synchronization-ptp-for-trading-hosts`
- `clock-drift-monitoring-alerting-thresholds`
- `sandbox-credential-leakage-prevention`
- `post-mortem-culture-and-blameless-review-process`
- `runbook-automation-for-common-incident-types`
- `order-placement-idempotency`
- `blue-green-deployment-for-live-strategy-updates`
- `systemd-supervision-for-trading-bots`
