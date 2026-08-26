# Workflows for Centralized Log Aggregation

## 0. Decide what the pipeline is allowed to carry

Before configuring anything, split the log paths:

- **Operational logs** — the path this engine handles. Redactable, samplable, alertable.
- **Regulatory order records** — RTS 6 Art. 28 / Annex II style records. Never redact,
  never sample, never route through a lossy buffer. See `record-retention-periods-by-jurisdiction`.
- **Real-time trading monitoring** — obligations with a latency bound (RTS 6 Art. 16(5):
  alerts within five seconds). Give it its own path; a log-flush cycle cannot carry it.

## 1. Construct the engine

```python
engine = CentralizedLogAggregatorEngine(
    error_spike_threshold_count=10,      # alerts on the 10th error in a batch (>=), must be >= 1
    diagnostic_sample_rate=1,            # 1 = keep every TRACE/DEBUG record
    extra_sensitive_key_substrings=["vendorpin"],   # site-specific credential field names
)
```

Configuration errors raise `LogAggregationError` (a `ValueError` subclass) at construction —
a threshold of `0` is rejected because it would alert on batches containing no errors, and a
sample rate below `1` is rejected because it would discard every diagnostic record.

Sizing the threshold: it is evaluated **per batch**, so it is coupled to your flush size, not
to wall-clock time. If the flush interval changes, the threshold's meaning changes with it.
Derive it from a normal-day error count for the same batch size, and re-derive it whenever
the flush cadence moves.

## 2. Ingest and redact — on the emitting host

```python
report = engine.process_and_aggregate_logs(records)
```

Redaction happens per record, before the JSON line exists, so a credential never reaches the
payload list. Key matching normalises the key (lowercase, separators stripped) and applies
both an exact-match set and a substring set, recursing through nested mappings and through
mappings nested inside lists.

What it does **not** do: inspect `message`, inspect values, or detect a credential-shaped
string. If a secret is interpolated into the message it ships. Treat "no credential in message
text" as a code-review rule, not something the pipeline can enforce.

Extending the blocklist: prefer `extra_sensitive_key_substrings` over widening
`SENSITIVE_KEY_SUBSTRINGS`. Patterns that are too broad (a bare `token`) destroy legitimate
fields — `token_symbol` and `token_address` in DEX metadata are data you need during an
incident.

## 3. Normalise severity, then bucket

Every level string is mapped to an OpenTelemetry SeverityNumber before counting, so
`WARNING`, `WARN`, `FATAL`, `CRITICAL`, `err` and `panic` all land in the right bucket
regardless of which service dialect emitted them. Bucketing on raw strings is how a
`FATAL`-emitting service ends up contributing zero to the error count.

Unmapped level strings are emitted with `severity_number: 0`, counted in
`unknown_level_count`, and logged as a warning. They do not count toward the spike threshold —
assert `unknown_level_count == 0` in CI so a new service's dialect is caught at integration
time rather than during the incident it fails to alert on.

## 4. Sample diagnostics, never alert levels

`diagnostic_sample_rate=N` keeps the first of every N TRACE/DEBUG records in the batch.
Sampling is deterministic and carries no state between calls, so replaying the same batch
yields the same output. INFO/WARN/ERROR/FATAL and unmapped levels are never sampled.

Kept diagnostic lines carry `sample_rate`, so downstream aggregations can rescale. Consumers
that ignore it will under-count DEBUG-derived metrics by a factor of N.

## 5. Ship, and respect the backend's acceptance window

Loki accepts out-of-order writes only back to
`highest_timestamp_written − max_chunk_age/2` per stream; older entries are rejected as
`too_far_behind`. Compare `max_ingest_lag_seconds` from the report against your cluster's
`max_chunk_age` before replaying a backlog, and expect that a long post-outage replay may be
partially refused by the backend even though this engine formatted every line successfully.

A negative `max_ingest_lag_seconds` means the batch is timestamped ahead of the aggregator —
clock skew between hosts. Investigate it; see `cross-vendor-timestamp-precision-reconciliation`.

Choose stream labels from bounded dimensions only (`subsystem`, `level`, environment).
`correlation_id` is per-order and must stay inside the JSON line.

## 6. Audit the report

`ObservabilityReport` fields and what each one tells an operator:

| Field | Read it as |
|---|---|
| `status` / `has_error_spike_alert` | Did this batch trip the error-velocity alarm. |
| `trace/debug/info/warn/error_logs_count` | Observed counts by bucket (`info` is INFO only; WARN is separate). Counts reflect records **observed**, not records emitted after sampling. |
| `unknown_level_count` | A service is emitting a level dialect this engine cannot map — it cannot trip the alarm. Should be 0. |
| `redacted_keys_count` | Credential-bearing keys masked. A sudden rise means a service started logging a new credential field. |
| `coerced_values_count` | Values stringified to keep the line valid JSON (Decimal, datetime, NaN). Expected to be non-zero in trading metadata. |
| `malformed_record_count` | Records repaired (non-mapping metadata, unusable timestamp). Non-zero means a producer is emitting broken records. |
| `sampled_out_count` | Diagnostic records dropped by sampling in this batch. |
| `max_ingest_lag_seconds` | Oldest record's age at flush; negative means clock skew ahead. Compare with the backend's acceptance window. |

## 7. Failure handling

The engine raises only on batch-level and configuration faults (`LogAggregationError`):
an empty or non-list batch, an invalid threshold or sample rate, or a non-mapping passed
directly to `redact_sensitive_metadata`. Record-level faults are repaired and counted instead
of raised — a logging pipeline that aborts on one bad field loses every other log in the
flush, including the ones describing the incident that produced it.
