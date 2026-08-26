# Pre-Flight Checklist — Centralized Log Aggregation

## Credential safety
- [ ] No credential is ever interpolated into `message` text (key-based redaction cannot catch it).
- [ ] Redaction verified against the affixed forms actually present in your payloads: `broker_api_key`, `access_token`, `X-Api-Key`, `Authorization` — not just `api_key`.
- [ ] Redaction verified for secrets nested inside lists (captured headers, retry attempts), not only nested dicts.
- [ ] Site-specific credential field names added via `extra_sensitive_key_substrings`.
- [ ] Confirmed the blocklist does not eat real data (`token_symbol`, `token_address`, `author`).

## Structure and severity
- [ ] Every emitted line survives a strict `json.loads` — no `NaN`/`Infinity` literals.
- [ ] `correlation_id` present on every record and used to reconstruct at least one full order lifecycle.
- [ ] `severity_number` mapped into your Collector/Loki pipeline (see `references/standards.md`).
- [ ] `unknown_level_count == 0` asserted in CI, so an unmapped dialect can never bypass the error count.
- [ ] Timestamps confirmed at microsecond precision and in UTC.

## Alerting
- [ ] `error_spike_threshold_count` derived from a normal-day error count **for the current batch size**, and re-derived whenever the flush cadence changes.
- [ ] Boundary behaviour confirmed: threshold N fires at N errors, not N+1.
- [ ] Alert latency (== flush cadence) documented and accepted; anything with a hard latency bound (e.g. MiFID II RTS 6 Art. 16(5), five seconds) runs on a separate path.
- [ ] Someone is paged by `OBSERVABILITY_ERROR_SPIKE_ALERT` — the report status is not self-actuating.

## Volume and delivery
- [ ] `diagnostic_sample_rate` set deliberately; confirmed INFO and above are never sampled.
- [ ] Downstream dashboards rescale by the `sample_rate` field.
- [ ] Stream labels chosen from bounded dimensions only; `correlation_id` is **not** a Loki label.
- [ ] `max_ingest_lag_seconds` monitored against the backend's acceptance window (Loki: `max_chunk_age/2`) before replaying any backlog.
- [ ] `malformed_record_count` and `coerced_values_count` are on a dashboard, not just in the return value.

## Scope
- [ ] Regulatory order records are on a separate, non-redacted, non-sampled path with their own retention.
