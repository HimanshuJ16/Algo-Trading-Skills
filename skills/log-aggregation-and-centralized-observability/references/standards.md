# Standards for Centralized Observability

## Engineering standards enforced by `centralized_log_aggregator.py`

| Area | Standard | Enforced by |
|---|---|---|
| Log format | One JSON object per record, strictly parseable (no `NaN`/`Infinity` literals), carrying a trace `correlation_id`. | `json.dumps(..., allow_nan=False)` after non-JSON leaves are coerced to strings. |
| Credential redaction | Credential-bearing **metadata keys** masked to `[REDACTED]`, matched case- and separator-insensitively, by exact match and substring, through nested mappings and lists. | `SENSITIVE_KEYS`, `SENSITIVE_KEY_SUBSTRINGS`, `extra_sensitive_key_substrings`. |
| Redaction boundary | Message text and values are **not** scanned. A secret interpolated into `message` ships verbatim. | Documented limitation — no code can reliably undo it. |
| Timestamps | RFC 3339 UTC at microsecond precision, plus an `observed_timestamp_iso` ingest stamp. | `_format_timestamp`, `clock_fn`. |
| Severity | Level text normalised to an OpenTelemetry SeverityNumber before bucketing, so dialect differences cannot drop a record out of the error count. Unmapped levels get `0` and are counted, not silently ignored. | `SEVERITY_NUMBERS`, `unknown_level_count`. |
| Sampling | Deterministic 1-in-N over TRACE/DEBUG only, per batch, stateless across calls; INFO and above are never sampled; kept lines carry `sample_rate`. | `diagnostic_sample_rate`. |
| Error spike alert | Alert when records at SeverityNumber >= 17 in the batch reach `error_spike_threshold_count` (comparison is `>=`; threshold must be >= 1). | `has_error_spike_alert`, `status`. |
| Failure isolation | Malformed metadata, unusable timestamps and over-deep nesting are repaired and counted, never dropped and never allowed to abort the batch. | `malformed_record_count`, `coerced_values_count`, `MAX_REDACTION_DEPTH`. |

## Field mapping into an OpenTelemetry Collector

The emitted schema is this engine's own; it is **not** an OTel log record. The OTel logs data
model names its fields `Timestamp`, `ObservedTimestamp`, `TraceId`, `SpanId`, `SeverityText`,
`SeverityNumber`, `Body`, `Attributes` and `Resource`, with `Timestamp` typed as
"uint64 nanoseconds since UNIX epoch". Configure the mapping explicitly:

| Emitted field | OTel log record field |
|---|---|
| `timestamp_iso` | `Timestamp` |
| `observed_timestamp_iso` | `ObservedTimestamp` |
| `correlation_id` | `TraceId` (only if it is a real 16-byte trace id; otherwise an attribute) |
| `level` | `SeverityText` |
| `severity_number` | `SeverityNumber` |
| `message` | `Body` |
| `subsystem` | `Resource` attribute (e.g. `service.name`) |
| `metadata.*` | `Attributes` |

SeverityNumber ranges per the data model: TRACE 1-4, DEBUG 5-8, INFO 9-12, WARN 13-16,
ERROR 17-20, FATAL 21-24. The appendix maps log4j `FATAL`->21 / `ERROR`->17 / `WARN`->13 /
`INFO`->9 / `DEBUG`->5 / `TRACE`->1 and syslog `emerg`->21, `alert`->19, `crit`->18,
`err`->17, `warning`->13, `notice`->10, `info`->9, `debug`->5. Python's `CRITICAL` is not in
that appendix; this engine maps it to 21 (FATAL) as Python's highest level. Note the appendix
is explicitly illustrative — "not exhaustive or canonical".

## Grafana Loki ingestion constraints

- **Label cardinality.** Loki's label guidance warns that high cardinality "causes Loki to
  build a huge index and to flush thousands of tiny chunks to the object store" and that
  "Loki performs very poorly when your labels have high cardinality", advising "fewer labels,
  aim to have 10 - 15 labels at a maximum" against a default limit of 15 index labels, and
  directing frequently-searched high-cardinality data to **structured metadata** instead.
  `correlation_id` is per-order and unbounded: keep it in the log line, never as a label.
- **Late writes.** Unordered writes are the default from Loki 2.4 (`unordered_writes: true`),
  but acceptance is still bounded: entries are accepted back to
  `highest_timestamp_written − (ingester.max-chunk-age / 2)` for the stream, and older ones
  are rejected with reason `too_far_behind`. A backlog replayed after an outage can therefore
  be dropped by the backend. `max_ingest_lag_seconds` in the report is the number to compare
  against your cluster's configured `max_chunk_age`.

## Regulatory touchpoints (verify applicability for your jurisdiction and licence)

| Rule | Jurisdiction | What it constrains here |
|---|---|---|
| MiFID II RTS 6 — Commission Delegated Regulation (EU) 2017/589, Art. 16 *Real-time monitoring* | EU (and UK as assimilated law) | Art. 16(5): "Real-time alerts shall be generated within five seconds after the relevant event." Art. 16(2) places real-time monitoring with the trader in charge **and** the risk-management or independent risk-control function. A batch log pipeline's alert latency equals its flush cadence — it cannot carry this obligation. |
| MiFID II RTS 6, Art. 28 + Annex II — order records | EU / UK | Order records have prescribed content and retention. Redaction and sampling must never be applied to that record path; they belong to the operational log path only. Confirm the applicable retention period with compliance. |

These are jurisdiction-specific and depend on the firm's licence and activity. They are listed
because this pipeline sits next to those obligations, not because the module discharges them.

## Sources consulted

- OpenTelemetry — Logs Data Model: <https://opentelemetry.io/docs/specs/otel/logs/data-model/>
- OpenTelemetry — Logs Data Model Appendix (SeverityNumber example mappings): <https://opentelemetry.io/docs/specs/otel/logs/data-model-appendix/>
- Grafana Loki — Understand labels (cardinality guidance, structured metadata): <https://grafana.com/docs/loki/latest/get-started/labels/>
- Grafana Labs — "New feature in Loki 2.4: no more ordering constraint" and Loki request-validation docs (`unordered_writes`, `max_chunk_age`, `too_far_behind`): <https://grafana.com/blog/new-feature-in-loki-2-4-no-more-ordering-constraint/>, <https://grafana.com/docs/loki/latest/operations/request-validation-rate-limits/>
- Commission Delegated Regulation (EU) 2017/589 (RTS 6), Article 16 — via the FCA Handbook technical standards: <https://handbook.fca.org.uk/technical-standards/provision/s119c1039s371p1568>
