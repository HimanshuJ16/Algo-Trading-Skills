---
name: alternative-data-feature-integration
description: >-
  Use when turning an alternative data source into model features and the event date
  differs from the date your fund actually received the data; enforces point-in-time lag
  mapping from knowledge date so the feature cannot see the future.
license: Apache-2.0
metadata:
  domain: algorithmic-trading
  subdomain: financial-ml
  tags: machine-learning, alternative-data, look-ahead-bias, point-in-time, feature-engineering
  brokers_frameworks: generic
  version: "1.3.0"
  author: algo-trading-skills-contributors
---

## When to Use

Use this skill when integrating any alternative data source into a trading model. Alternative data is notoriously prone to **look-ahead bias** because the date an event happened (Event Date) is rarely the date the quantitative fund actually received the data (Knowledge Date or As-Of Date). This engine strictly enforces publication lags and aligns irregular alternative data frequencies (e.g., weekly satellite updates) to the trading strategy's frequency (e.g., daily market close) using safe, PIT-compliant forward-filling, per source, with bounded staleness.

This skill assumes the upstream compliance gates (Step 0 below) have already been satisfied. It models the PIT **math**; it does not perform MNPI classification, vendor due diligence, or license/usage-restriction tracking — those are mandatory upstream skills listed in Related Skills.

## When NOT to Use

Do **not** use this skill — and do not proceed to PIT feature construction — when any of the following hold. Route to the compliance/due-diligence sibling skills first instead of building leak-correct but illegal or incorrect features:

- The source **may carry MNPI** without an MNPI classification and handling decision on file. See `insider-trading-controls-for-alternative-data-usage`.
- The **vendor contract is research-only** or otherwise restricts live-trading usage. See `data-vendor-contractual-usage-restriction-tracking`.
- The source contains **unscrubbed PII**, or the vendor's aggregation/anonymization methodology is undocumented or unverified. There is no universal numeric cell-size threshold: your privacy/compliance function sets and records the minimum cell size (k-anonymity parameter) for the dataset, and it must be checked against what the vendor actually does rather than accepted on the vendor's representation. See `references/standards.md` for the authorities.
- The **publication lag is variable or historically unverified** (you cannot state a defensible `publication_lag` per source). A constant assumed lag is a silent look-ahead vector.
- **Restatements are expected** and you have no version-control/revised-date feed; this skill models revisions as appended PIT facts only when a `revised_date` is supplied.
- You need **multi-source fan-out to many consumers** with separate serving SLAs; that live-serving path belongs in `feature-store-for-live-and-backtest-parity`, not this in-process helper.

## Prerequisites

- Python 3.10+ (stdlib only; no third-party dependencies).
- Raw alternative data events containing an exact naive-UTC `event_timestamp`.
- A known, defensible `publication_lag` per source (how long after the event the vendor actually publishes the dataset), confirmed directly with the vendor.
- Completed **Step 0** of the Workflow (MNPI/PII/licensing compliance gate) for every source.

## Workflow

**Step 0 — Compliance Gate (mandatory, upstream of all PIT math).** Before ingesting a single event, for each source:
1. **MNPI classification**: classify the source as MNPI-free or MNPI-controlled, and record the decision. Mandatory gate: `insider-trading-controls-for-alternative-data-usage`.
2. **PII / anonymization assertion**: assert PII is scrubbed, and record both the minimum cell size (k-anonymity parameter) your compliance function requires for this dataset and the evidence that the vendor actually meets it. A vendor's "aggregated and anonymized" representation is not evidence — misrepresenting exactly that was the basis of the SEC's first alternative-data enforcement action (`references/standards.md`).
3. **Vendor due-diligence sign-off**: a current, signed due-diligence record exists. Mandatory gate: `alternative-data-vendor-due-diligence-checklist`.
4. **License / usage-restriction check**: the contract covers the intended use (live trading, not research-only) and jurisdiction. Mandatory gate: `data-vendor-contractual-usage-restriction-tracking` and `eu-market-abuse-regulation-mar-surveillance` for EU sources.

**Reject the source if any gate fails** — do not build the feature.

1. **Ingest Raw Events**: Load raw alternative data points into `AltDataIntegrator.ingest_events()`. Ingest validates every event first (atomic) and raises `AltDataValidationError` on a negative `publication_lag`, non-finite `feature_value`, timezone-aware datetime, or `schema_version` drift — without leaving the integrator half-populated.
2. **Apply Publication Lag**: The integrator computes the strict `knowledge_timestamp = event_timestamp + publication_lag` (or `revised_date + publication_lag` for a restatement). Ingest is idempotent: events keyed on `(source_id, knowledge_timestamp)` are deduped, so a backfill/restart resending the same rows does not duplicate facts. Two *different* facts colliding on that key inside one batch are rejected (`AltDataValidationError`) rather than silently resolved by list order; ingest a genuine correction in a separate call, where the later call wins.
3. **Model Revisions as Appended Facts**: When a vendor supplies a `revised_date`, the restatement is appended as a **new** PIT fact (never overwriting the original). The original is served by the as-of merge until the restatement's `knowledge_timestamp` passes.
4. **Align to Trading Timeline**: Pass a list of target naive-UTC trading times to `align_to_trading_schedule()`. It returns, per trading time and per source, an `AlignedValue` with the last-known value, its `knowledge_timestamp`, `age`, and a `staleness_state`. Every source declared in `source_configs` appears in every slot, so a vendor that has delivered nothing at all reads as `UNKNOWN` rather than as an absent key.
5. **Safe Forward-Filling with Bounded Staleness**: If no new data has published by the trading time, the integrator forward-fills the last known value. When `age > max_age` (per-source `SourceConfig.max_age` or the `max_age` argument), the value is reported as `None` with `staleness_state = STALE` rather than silently forward-filling an arbitrarily stale value on a vendor outage/lapse.
6. **Model Inference**: Pass the aligned, lag-safe features to the predictive model. Downstream should downweight or fall back on `STALE`/`UNKNOWN` values per the degradation policy.

> Full procedure: see `references/workflows.md`.
> Standards reference: see `references/standards.md`.
> Printable pre-flight checklist: see `assets/checklist.md`.

## Common Pitfalls

- **Using Event Date for Backtesting**: The most critical error in quantitative finance. If satellite imagery of a retailer's parking lot is taken on Sunday (Event Date) but not published by the vendor until Tuesday morning (Knowledge Date), backtesting as if you knew the data on Monday morning introduces massive look-ahead bias.
- **Naive Forward Filling**: Forward filling a pandas DataFrame without first shifting the index by the publication lag.
- **Negative `publication_lag`**: A negative lag makes the knowledge timestamp precede the event and silently re-introduces look-ahead. The integrator rejects it (`AltDataValidationError`); never work around the guard.
- **Non-finite `feature_value`**: A `NaN`/`inf` flows through the forward-fill and defeats `==` comparisons (NaN != NaN), producing silent, non-deterministic test and model behavior. The integrator rejects non-finite values at ingest.
- **Multi-source scalar clobbering**: Forward-filling into a single `Dict[datetime, float]` makes multiple sources overwrite each other at the same trading time. Always consume the per-source `Dict[datetime, Dict[str, AlignedValue]]` so each source's value and provenance are preserved.
- **Mixing naive and timezone-aware datetimes**: Comparing a tz-aware event timestamp with a tz-naive trading time raises `TypeError` mid-loop, leaving the integrator half-aligned, and a UTC/ET offset silently shifts knowledge times. All datetimes must be naive UTC; the integrator enforces this at ingest and alignment.
- **Unbounded stale forward-fill**: Forward-filling indefinitely on a vendor outage silently trains/serves on arbitrarily stale data. Configure a `max_age` TTL so stale values surface as `STALE`/`None`.
- **Treating an absent source key as "no signal"**: a source that has never delivered a row is exactly what a vendor outage looks like on day one, and an absent dict key reaches the model as a `KeyError` or, after a defensive `.get(sid, 0.0)`, as a fabricated zero feature. Declare every expected source in `source_configs`; the integrator then emits an explicit `UNKNOWN` for the ones that delivered nothing.
- **Zipping aligned output positionally against another series**: `align_to_trading_schedule` returns a mapping keyed by trading time, so duplicate trading times collapse and `len(result)` can be less than `len(trading_times)`. Look values up by timestamp; a positional zip silently shifts every feature one bar relative to its label.
- **Resolving a same-key collision by batch order**: two events for one source that land on the same `knowledge_timestamp` cannot both be the as-of value, and picking the list-order-last one makes your stored history depend on the order the vendor file happened to be read in. The integrator raises instead; fix the lag or the event upstream.
- **Using restated data before its `revised_date`**: A restatement is only knowable at `revised_date + lag`. Serving the revised value earlier leaks the future revision; the appended-PIT-fact model prevents this automatically as long as you supply `revised_date`.

## Verification

Run `python -m unittest discover -s skills/alternative-data-feature-integration/scripts` and confirm every test passes. Then self-verify the integration against these explicit, checkable criteria:

- **Zero leakage**: no aligned value has `knowledge_timestamp > trading_time` for its slot. (Equivalently, the PIT invariant `knowledge_timestamp <= trading_time` holds for every non-`UNKNOWN` `AlignedValue`.)
- **Per-source freshness within SLA**: every source consumed by the model is `FRESH` at the relevant trading times, or the model has an explicit `STALE`/`UNKNOWN` handling policy; no value older than `max_age` is silently served.
- **Compliance gate passed**: every ingested source has a recorded MNPI classification, PII/anonymization assertion, vendor due-diligence sign-off, and license/usage-restriction check (Step 0).
- **Restatements appended, not overwritten**: a restatement with `revised_date` produces an additional PIT fact; the original value is still served for trading times before the restatement's `knowledge_timestamp`.
- **Deterministic**: re-ingesting the same batch of events in any input order yields the same `pit_features` sequence (tie-break is `(knowledge_timestamp, source_id)`); an ambiguous same-key collision raises rather than being resolved by list order.
- **Lag re-derivable from the stored fact**: for every `PointInTimeFeature`, `knowledge_timestamp - (revised_date or event_timestamp)` equals the publication lag signed off for that source — the stored fact alone is enough for an auditor to re-check the PIT mapping.
- **No silently missing source**: every source declared in `source_configs` appears in every aligned slot; a source that delivered nothing reads as `UNKNOWN`, never as an absent key.

## Related Skills

Mandatory upstream gates (must pass before any PIT math in this skill):
- `insider-trading-controls-for-alternative-data-usage`
- `alternative-data-vendor-due-diligence-checklist`
- `data-vendor-contractual-usage-restriction-tracking`
- `eu-market-abuse-regulation-mar-surveillance`

Operational siblings:
- `feature-store-for-live-and-backtest-parity`
- `data-pipeline-schema-contract-testing`
- `model-training-data-freshness-sla`
- `vendor-outage-fallback-data-source-hierarchy`

Foundational:
- `feature-engineering-without-leakage`
- `point-in-time-database-for-ml-training-data`

## End-to-End Example

```python
from datetime import datetime, timedelta
from alt_data_integrator import (
    AltDataIntegrator, RawAltDataEvent, SourceConfig, StalenessState,
)

integrator = AltDataIntegrator(
    source_configs={"SAT_IMG_01": SourceConfig("SAT_IMG_01", max_age=timedelta(days=30))}
)

# Satellite image taken Mon Jan 5 12:00, published 48h later -> known Wed Jan 7 12:00.
integrator.ingest_events([
    RawAltDataEvent("SAT_IMG_01", datetime(2026, 1, 5, 12, 0),
                    timedelta(hours=48), feature_value=150.5),
])

# Restatement: revised_date Jan 9 09:00, +24h -> known Jan 10 09:00.
integrator.ingest_events([
    RawAltDataEvent("SAT_IMG_01", datetime(2026, 1, 5, 12, 0),
                    timedelta(hours=24), feature_value=160.0,
                    revised_date=datetime(2026, 1, 9, 9, 0)),
])

trading_times = [
    datetime(2026, 1, 6, 16, 0),   # Monday close: before first publication -> UNKNOWN
    datetime(2026, 1, 8, 16, 0),   # Wednesday close: original 150.5 (FRESH)
    datetime(2026, 1, 11, 16, 0),  # Sunday close: restated 160.0 (FRESH)
]

aligned = integrator.align_to_trading_schedule(trading_times)
for t in trading_times:
    av = aligned[t]["SAT_IMG_01"]
    print(t, av.staleness_state, av.value, av.knowledge_timestamp)
```

This integrator is **not thread-safe**; serialize concurrent ingest/align at the caller, and snapshot `pit_features` for read-side concurrency. Production serving must persist PIT features with idempotent partition finalization externally (see `feature-store-for-live-and-backtest-parity`); this helper is in-memory only.
