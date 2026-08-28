# Pre-Flight Checklist — structured-logging-for-post-incident-forensics

Sign this off before the logging this skill produces is relied on as the record of a
live incident. Each item is a thing that has silently produced a wrong or missing
timeline in practice.

## Sink and durability

- [ ] A handler is attached to the sink logger. (`emit` succeeds with no handler and the
      record goes nowhere.)
- [ ] The sink's formatter is `%(message)s` — nothing wraps the JSON object in prose.
- [ ] `sink.propagate` is `False`, so JSONL is not duplicated into the root handlers.
- [ ] The forensic sink is a different logger from the module's diagnostic logger
      (`structured_logger`), so no prose line lands inside the JSONL stream.
- [ ] The durable destination's retention is set from a **written** target derived from
      your jurisdiction and entity type — not from this module's defaults, which enforce
      nothing. See `references/standards.md` §3.
- [ ] Where an immutability obligation applies (e.g. SEC Rule 17a-4(f) WORM or its
      audit-trail alternative), it is satisfied by the storage layer. This module does
      not provide it.
- [ ] Disk/quota headroom is sized for the peak burst, not the average. An aggregator
      that drops records under ingest pressure drops them during the incident.

## Record schema

- [ ] Every record parses under a **strict** JSON parser — one that rejects `NaN` and
      `Infinity`, not Python's permissive default.
- [ ] Every record carries `schema_version`, `seq`, `instance_id`, `ts_ns`, `mono_ns`,
      `correlation_id`, `component`, `severity`, `severity_number`.
- [ ] `ts_iso` renders the same instant as `ts_ns`, to nine fractional digits.
- [ ] `severity` values come from the closed set only; no `WARN`/`warn`/`Notice`
      variants have reached the archive.

## Correlation and ordering

- [ ] Correlation IDs are minted at the **strategy signal**, not at order submission.
- [ ] Every order-lifecycle `emit` receives the correlation ID explicitly; a spot check
      for orphan events (a correlation ID appearing exactly once) comes back clean.
- [ ] Generated IDs are 32 lowercase hex characters. No truncation anywhere in the
      pipeline — including in dashboards, tickets, and copy-paste into a runbook.
- [ ] Every consumer, query, dashboard, and runbook sorts by `(instance_id, seq)` —
      **not** by file order and **not** by timestamp.
- [ ] Elapsed times are computed from `mono_ns`, and only within one `instance_id`.
- [ ] Where a regulatory algo/order identifier exists (e.g. a SEBI algo ID, an
      exchange-assigned tag, the broker order ID), it is carried in `metadata` on every
      lifecycle event so the internal and external trails join.

## Event taxonomy

- [ ] Every state transition emits, including the ones on the failure path.
- [ ] Request and confirmation are separate events: `ORDER_CANCEL_REQUESTED` at send,
      `ORDER_CANCELLED` only on venue confirmation. Same for place/acknowledge and
      modify/modified.
- [ ] Partial fills use `PARTIAL_FILL_RECEIVED`, not `FILL_RECEIVED`.
- [ ] A query for `_unknown_event_type` returns nothing — no call site is emitting a
      type outside the taxonomy by accident.
- [ ] A query for `_invalid_severity` returns nothing.
- [ ] A query for `_serialization_error` returns nothing.

## Content and secrets

- [ ] Facts live in `metadata`, not interpolated into `message`. A responder can filter
      on symbol, side, quantity, venue, and order ID without regex over prose.
- [ ] No credential, token, session ID, or private key is passed to `emit` at all.
      Redaction is the backstop, not the control.
- [ ] `redact_keys` has been extended with the credential-bearing key names this
      deployment actually uses.
- [ ] A grep of a sample of the live archive for known secret prefixes
      (`sk_live`, `Bearer `, `-----BEGIN`) returns nothing.
- [ ] Tick-rate events are level-gated or sampled; nothing emits per tick.

## Buffer and failure behaviour

- [ ] `buffer_capacity` is sized against the longest incident you expect to debug
      in-process, and the team knows the buffer is **not** the record of truth.
- [ ] Any runbook step that reads `reconstruct_timeline` checks
      `buffer_status()["complete"]` first and falls back to replaying the sink.
- [ ] A deliberate sink failure (revoke write permission on the log file in a staging
      run) confirms `emit` still returns, the event is still buffered, and
      `sink_failures` increments.

## Verification

- [ ] `python -m unittest discover -s skills/structured-logging-for-post-incident-forensics/scripts`
      — 56 tests, 100% pass.
- [ ] `python tools/validate_skills.py` passes.
- [ ] An end-to-end drill: pick a real order from staging, reconstruct its full timeline
      from the durable sink alone (not the in-memory buffer), and confirm every
      transition is present and correctly ordered.

## Sign-off

- Reviewed by: ___________________________
- Date: ___________________________
- Retention target and regime applied: ___________________________
