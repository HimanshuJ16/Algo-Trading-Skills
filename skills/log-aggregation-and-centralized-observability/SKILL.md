---
name: log-aggregation-and-centralized-observability
description: >-
  Centralized logging and observability pipeline for distributed trading microservices — redacting credential-bearing metadata keys before logs leave the host, emitting structured JSON lines with OpenTelemetry SeverityNumbers and microsecond timestamps for Grafana Loki / OpenTelemetry Collector ingestion, sampling high-volume diagnostic levels, and flagging error-velocity spikes per batch.
domain: System Architecture & Infrastructure
subdomain: Observability & Distributed Logging
tags: ["log-aggregation", "observability", "opentelemetry", "grafana-loki", "elk-stack", "structured-json", "credential-redaction", "error-spike-alert"]
brokers_frameworks: ["OpenTelemetry Collector", "Grafana Loki API", "Python Dataclasses"]
version: "1.1.0"
author: algo-trading-skills-contributors
license: Apache-2.0
---

## When to Use

Use this skill when distributed algorithmic trading microservices (order routers, market data gateways, risk managers, execution engines) ship logs off-host into a shared store — Grafana Loki, an OpenTelemetry Collector, or an ELK cluster. Two things go wrong at that boundary and both are expensive: credentials that were harmless in a local file become an exfiltration target once they are centrally indexed and broadly readable, and unstructured text becomes unqueryable exactly when an incident makes querying urgent.

`CentralizedLogAggregatorEngine` takes a batch of `RawLogRecord`s and returns an `ObservabilityReport` containing one structured JSON line per record: credential-bearing **metadata keys** replaced with `[REDACTED]`, an OpenTelemetry `severity_number` normalised across logging dialects, RFC 3339 UTC timestamps at microsecond precision, optional deterministic sampling of TRACE/DEBUG, and an error-velocity check over the batch.

## When NOT to Use

- **You need alerting with a latency bound.** This engine only evaluates the batch it is handed, so alert latency equals your flush cadence and a spike straddling two flushes may trip neither. Firms in scope of MiFID II RTS 6 should note Article 16(5): *"Real-time alerts shall be generated within five seconds after the relevant event."* Put that monitoring on its own path — not behind a log-shipping pipeline.
- **You are relying on redaction to make it safe to log secrets.** Redaction here is metadata-**key** scoped. It never inspects `message`, so `f"auth failed for {api_key}"` ships the key verbatim. The control is "never put a credential in a log", not "the aggregator will catch it".
- **You need a literal OpenTelemetry log record.** The emitted schema is this engine's own (`timestamp_iso`, `subsystem`, `level`, `message`, `metadata`) plus `severity_number`; it is not OTel's `Timestamp`/`Body`/`Attributes` model. Map it explicitly in your Collector — see `references/standards.md`.
- **You are designing the event taxonomy itself** (event types, sequence numbers, order-lifecycle correlation) — start at `structured-logging-for-post-incident-forensics`; this skill assumes the schema already exists and handles the shipping boundary.
- **The record is a regulatory order record.** Order records under RTS 6 Article 28 / Annex II are governed by retention and completeness rules; do not point a redactor at that path. See `record-retention-periods-by-jurisdiction`.

## Prerequisites

- Log records shaped as `RawLogRecord`: `subsystem` (`ORDER_ROUTER`/`RISK_GATEWAY`/`MARKET_DATA`), `level`, `message`, `correlation_id`, optional `metadata` mapping and `timestamp_epoch`.
- A Loki / OTel Collector ingestion endpoint, and a decision about which fields become **stream labels** (see pitfalls — `correlation_id` must not be one).
- Agreement on which metadata field names at your firm carry credentials; anything site-specific goes in `extra_sensitive_key_substrings`.

## Workflow

1. **Configure the engine before the first flush, not after an incident.**
   - `error_spike_threshold_count` fires at `>=` — a threshold of 10 alerts on the 10th error in the batch, not the 11th. It must be `>= 1`; `0` is rejected because it would alert on error-free batches.
   - `diagnostic_sample_rate` defaults to `1` (nothing is sampled). Raise it only for TRACE/DEBUG volume; INFO and above are never sampled, so an alert path can never be thinned by this setting.
   - Inject `clock_fn` in tests so `observed_timestamp_iso` and `max_ingest_lag_seconds` are deterministic.

2. **Redact on the emitting side, before the batch leaves the host.**
   - Keys are matched case- and separator-insensitively (`API-Key`, `api_key`, `apiKey` all collapse), by exact match *and* substring, so `broker_api_key` and `access_token` are caught — an exact-match blocklist misses both.
   - Traversal covers nested mappings **and** mappings inside lists (captured HTTP headers, retry attempts, multi-leg payloads).
   - `token` is not a bare substring pattern: `token_symbol`/`token_address` in DEX metadata are data, not credentials. Add site-specific names via `extra_sensitive_key_substrings` rather than widening to patterns that eat real fields.
   - Nesting deeper than `MAX_REDACTION_DEPTH` (12) is replaced with `[TRUNCATED: MAX_DEPTH]` — bounded so a cyclic structure cannot raise `RecursionError` and take the whole batch with it.

3. **Normalise severity before counting anything.**
   - Level text is mapped to an OpenTelemetry SeverityNumber (TRACE 1 / DEBUG 5 / INFO 9 / WARN 13 / ERROR 17 / FATAL 21), so `WARNING` (Python), `FATAL` (log4j) and `err` (syslog) bucket identically. Bucketing on raw strings silently drops whole dialects out of the error count.
   - A level string with no mapping is emitted with `severity_number: 0`, counted in `unknown_level_count`, and logged as a warning. It is **not** counted as an error — assert `unknown_level_count == 0` in CI, or a service using an unknown dialect will never trip the spike alarm.

4. **Sample only what is safe to lose.**
   - Sampling keeps 1 of every N TRACE/DEBUG records deterministically (first of each group), per batch, with no state carried between calls. Kept lines carry `sample_rate` so downstream counts can be rescaled; without it every DEBUG-derived metric is silently wrong by a factor of N.

5. **Audit the batch, then act on the report — not just on `status`.**
   - `has_error_spike_alert` / `status` is the headline. `redacted_keys_count`, `coerced_values_count`, `malformed_record_count`, `unknown_level_count`, `sampled_out_count` and `max_ingest_lag_seconds` are the operational signals: a rising `max_ingest_lag_seconds` predicts backend rejection, and a non-zero `malformed_record_count` means some service is emitting records this pipeline had to repair.

> Full procedure: see `references/workflows.md`.
> Standards, sources, and the OTel/Loki field mapping: see `references/standards.md`.
> Printable pre-flight checklist: see `assets/checklist.md`.

## Common Pitfalls

- **Interpolating a secret into the message string.** `logger.error(f"auth failed: {api_key}")` defeats key-based redaction completely, and the line is already in central storage by the time anyone notices. Pass credentials-adjacent context as metadata keys, never as message text.
- **Trusting an exact-match credential blocklist.** `{"api_key"}` does not match `broker_api_key`, `access_token`, `X-Api-Key`, or `Authorization`. The prefixed and suffixed forms are the common ones in real payloads — match on the normalised key, by substring.
- **Redacting only the top level of the mapping.** Secrets arrive nested — inside `wallet`, inside a list of captured request headers, inside per-retry attempt records. Dict-only recursion leaks every one held in a list.
- **Over-redacting with a bare `token` rule.** In crypto/DEX metadata `token_symbol` and `token_address` are the fields you need for forensics. Destroying them protects nothing.
- **Promoting `correlation_id` (or any per-order ID) to a Loki stream label.** Loki's documentation warns that high cardinality "causes Loki to build a huge index and to flush thousands of tiny chunks", performs "very poorly", and advises "10 - 15 labels at a maximum" (default limit 15 index labels), directing high-cardinality search at *structured metadata* instead. Keep `correlation_id` in the JSON line, and label on bounded dimensions (`subsystem`, `level`, environment).
- **Buffering a long backlog and flushing it late.** With unordered writes (Loki's default since 2.4) Loki still only accepts entries back to `highest_timestamp_written − max_chunk_age/2` per stream, rejecting older ones as `too_far_behind`. Replaying a post-outage backlog can therefore silently drop exactly the logs the incident review needs. Watch `max_ingest_lag_seconds` against your cluster's `max_chunk_age`.
- **Letting one bad field kill the flush.** `Decimal` prices, `datetime` objects and `NaN` ratios are normal trading metadata: raw `json.dumps` raises on the first two and emits invalid JSON (`NaN`, `Infinity`) for the third. This engine coerces them and counts it in `coerced_values_count` — a pipeline that raises loses every other log in the batch, including the ones explaining the failure.
- **Formatting timestamps at whole-second resolution.** A trading system emits many events per second; second-granularity stamps make a post-incident timeline unorderable. Timestamps here are microsecond RFC 3339 UTC.
- **Reading `info_logs_count` as "everything that wasn't an error".** It is INFO only; WARN has its own counter. Reading a lumped count hides a warning surge that precedes most incidents.

## Verification

- Run the unit suite: `python -m unittest discover -s skills/log-aggregation-and-centralized-observability/scripts` — all tests must pass.
- Ingest a record whose metadata holds `api_key`, `broker_api_key`, `access_token`, `Authorization`, a nested `wallet.private_key`, and a credential inside a list of headers; confirm all six become `[REDACTED]`, `redacted_keys_count == 6`, and that `token_symbol` is untouched.
- Ingest a batch of 15 records at `FATAL` and `WARNING` with the default threshold of 10; confirm `error_logs_count == 15`, `warn_logs_count` is separate, and `status == "OBSERVABILITY_ERROR_SPIKE_ALERT"`. Repeat with exactly 9 and 10 errors to confirm the boundary fires on the 10th.
- Ingest metadata containing `Decimal("101.25")`, a `datetime`, and `float("nan")`; confirm every payload survives `json.loads` and the batch does not raise.
- Ingest an unknown level string (e.g. `"LOUD"`); confirm `unknown_level_count` rises, `severity_number` is `0`, and the record is still emitted.
- Run with `diagnostic_sample_rate=10` over 100 DEBUG plus 2 ERROR records; confirm 10 DEBUG lines survive with `sample_rate: 10`, both ERROR lines survive, and `sampled_out_count == 90`.

## Related Skills

- `structured-logging-for-post-incident-forensics`
- `data-lineage-tracking-for-audit-and-debugging`
- `sandbox-credential-leakage-prevention`
- `cross-vendor-timestamp-precision-reconciliation`
- `record-retention-periods-by-jurisdiction`
---
