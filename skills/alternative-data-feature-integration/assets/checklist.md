# Checklist for Alternative Data Integration

## Compliance Gate (Step 0) — per source
- [ ] MNPI source classification recorded (MNPI-free or MNPI-controlled with handling decision).
- [ ] PII scrubbed; any panel/aggregated data meets the >= 50-contributor threshold per cell.
- [ ] Vendor due-diligence record signed and current (record the **due-diligence last refreshed** date).
- [ ] License/usage-restriction check passed: contract covers live trading and the relevant jurisdiction.
- [ ] Earnings blackout enforced for sources that touch issuer-specific information around earnings windows.

## PIT Math
- [ ] Verify the exact, non-negative `publication_lag` for the data source directly with the vendor.
- [ ] Confirm `knowledge_timestamp` is calculated as `event_timestamp + publication_lag` (or `revised_date + publication_lag` for a restatement).
- [ ] All datetimes (`event_timestamp`, `revised_date`, `trading_times`) are naive UTC; none are timezone-aware.
- [ ] `feature_value` is finite (no NaN/inf) at ingest.
- [ ] `schema_version` of each payload matches the configured expected version for the source.

## Revisions & Versioning
- [ ] Data revision/version control is active: restatements carry a `revised_date` and are appended as new PIT facts, never overwriting the original.
- [ ] The original value is still served for trading times before the restatement's `knowledge_timestamp`.

## Alignment & Freshness
- [ ] Missing-data alignment (forward filling) is executed *as-of* the `knowledge_timestamp`.
- [ ] A `max_age` TTL is configured per source (or globally); values older than `max_age` surface as `STALE`/`None`, not silently forward-filled.
- [ ] The model has an explicit `STALE`/`UNKNOWN` downweighting or fallback policy.

## Verification
- [ ] Zero leakage: no aligned value has `knowledge_timestamp > trading_time`.
- [ ] Per-source freshness within SLA at the consumed trading times.
- [ ] Ingest is deterministic: re-ingesting in any order yields the same `pit_features`.
- [ ] Run test suite: `python -m unittest discover -s skills/alternative-data-feature-integration/scripts`.

## Deployment / Rollback / Monitoring
- [ ] Deployment: integrator is single-threaded or caller-serialized; concurrent reads snapshot `pit_features`.
- [ ] Rollback: re-running the backfill over the same events is idempotent (dedup on `(source_id, knowledge_timestamp)`); `reset()` clears state for a clean-room rebuild.
- [ ] Monitoring: track per-source `STALE` rate and `schema_version` drift as data-quality signals (see `data-quality-monitoring-dashboard` and `model-training-data-freshness-sla`).

## Sign-off
- Lead Quantitative Researcher: ___________________________
- Chief Compliance Officer / Legal: ___________________________
- Date: ___________________________
