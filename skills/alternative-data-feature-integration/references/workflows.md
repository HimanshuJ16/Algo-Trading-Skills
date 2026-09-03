# Workflows for Alternative Data Integration

## Feature Engineering Pipeline

1. **Vendor Ingestion**: Download alternative data files (CSV, JSON, Parquet) from the data vendor.
2. **Compliance Gate (Step 0, mandatory)**: For each source, pass MNPI classification, PII/anonymization assertion, vendor due-diligence sign-off, and license/usage-restriction check *before* any PIT math. See the SKILL.md Workflow for the mandatory sibling-skill gates. **Reject the source on any gate failure** — do not build a leak-correct but illegal feature.
3. **Lag Auditing**: Explicitly identify the vendor's publication SLA (e.g., $T+2$ days) as a defensible, non-negative `publication_lag` per source.
4. **PIT Transformation**: Map all `event_dates` to `knowledge_dates` using `AltDataIntegrator.ingest_events()`. Ingest is atomic (validate-and-stage, then commit) and idempotent (dedup on `(source_id, knowledge_timestamp)`). Each stored fact retains its `event_timestamp`, so the applied lag stays re-derivable for audit.
5. **Revision Handling**: When a vendor supplies a `revised_date`, ingest the restatement as an appended PIT fact (`knowledge_timestamp = revised_date + publication_lag`). The as-of merge serves the original until the restatement's knowledge timestamp passes; never overwrite the original.
6. **Simulation Alignment**: During backtesting, generate a list of exact naive-UTC trading timestamps. Pass these to `align_to_trading_schedule()` to produce, per trading time and per source, an `AlignedValue` (value, knowledge_timestamp, age, staleness_state).
7. **Bounded-Staleness Check**: Configure `max_age` (per-source `SourceConfig.max_age` or the `max_age` argument). Flag any value whose `age > max_age` as `STALE` (returned as `None`); the model must downweight or fall back per the degradation policy. Do not silently train/serve on stale values.
8. **Machine Learning Inference**: Pass the aligned, lag-safe features into the predictive model.

## Decision / Rejection Gates

- **MNPI fail** → reject the source. Route to `insider-trading-controls-for-alternative-data-usage`.
- **PII unscrubbed / anonymization unverified** → reject until PII is scrubbed and the vendor's aggregation methodology is documented and shown to meet the minimum cell size your compliance function set for this dataset. There is no universal numeric threshold; see `references/standards.md`.
- **License is research-only** → reject for live-trading use. Route to `data-vendor-contractual-usage-restriction-tracking`.
- **`schema_version` drift** → reject the batch (`AltDataValidationError`). Treat as a vendor schema change; route to `data-pipeline-schema-contract-testing` to reconcile the contract before re-ingesting.
- **`STALE` aligned value** → do not silently serve. Either downweight, fall back to a secondary source (`vendor-outage-fallback-data-source-hierarchy`), or skip the signal for that bar.
- **Ambiguous same-key collision** → `AltDataValidationError` at ingest: two different facts for one source resolved to the same `knowledge_timestamp`. Do not re-order the batch to make it pass; either the lag or the event timestamp is wrong upstream. A genuine correction goes in a separate ingest call.
- **Source declared but never delivered** → reported as `UNKNOWN` at every trading time. Treat a persistent `UNKNOWN` as a vendor-outage signal, not as a zero feature; route to `vendor-outage-fallback-data-source-hierarchy`.
- **Negative `publication_lag` / non-finite `feature_value` / tz-aware datetime** → `AltDataValidationError` at ingest; reject the offending event and fix the upstream feed.

## Recovery: Idempotent Re-Ingest / Backfill

Because `ingest_events()` is idempotent (dedup keyed on `(source_id, knowledge_timestamp)`), re-running a backfill or restarting a pipeline over the same events does not duplicate facts. A corrected value for an existing key must arrive in its own ingest call — the later call overwrites — because a batch containing two different facts for one key is rejected as ambiguous. Recovery procedure:

1. Identify the corrupted/lapsed partition and its source.
2. Re-fetch the raw vendor events for the affected window.
3. Optionally `reset()` the integrator for a clean-room rebuild, or re-ingest directly to upsert corrections in place.
4. Re-run `align_to_trading_schedule()`; results are deterministic given the same inputs regardless of ingest order.
5. Verify the self-check criteria in the SKILL.md Verification section (zero leakage, freshness within SLA, restatements appended not overwritten).
