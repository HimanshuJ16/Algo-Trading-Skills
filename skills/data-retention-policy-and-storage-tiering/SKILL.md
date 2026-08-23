---
name: data-retention-policy-and-storage-tiering
description: Storage tiering and retention engine that moves market data (L2/L3
  ticks, Parquet backtests, trade logs) across HOT (NVMe), WARM (S3), COLD (Glacier
  Instant Retrieval) and DEEP ARCHIVE tiers without recommending deletion of
  records still inside their retention period.
domain: Data Management Global
subdomain: Storage Optimization & Retention
tags:
- data-retention
- storage-tiering
- hot-warm-cold
- s3-glacier
- parquet-compaction
- cost-optimization
- sec-17a-4
brokers_frameworks:
- AWS S3 Lifecycle
- Glacier Deep Archive
- Python Dataclasses
version: "1.1.0"
author: algo-trading-skills-contributors
license: Apache-2.0
---

## When to Use

Use this skill in market data infrastructure and quantitative data lakes to plan
multi-tier storage lifecycles. High-frequency L2/L3 data accumulates faster than
any retention budget, and keeping multi-year tick history on HOT NVMe is a large
recurring bill for data nobody queries. This engine places each dataset on an
age-driven tier ladder, applies the applicable retention period as a *safety
gate* over that ladder, and quantifies the gross monthly cost delta with the
cloud-storage caveats that make naive lifecycle rules cost more than they save.

Use it when you need the tiering decision to be **auditable** — a persisted
record of why each dataset moved, or why it deliberately did not.

## When NOT to Use

- **Not a compliance oracle.** It does not determine which retention period
  applies to a record. That is a legal determination, supplied as the
  `regulatory_retention_years` input.
- **Not an executor.** It emits recommendations. It does not call AWS, apply
  lifecycle rules, or delete anything.
- **Not a billing forecast.** Savings are gross steady-state storage figures; see
  Prerequisites for what is excluded.
- **Not for hot-path query routing.** Tier placement here is a cost/retention
  decision, not a latency-tiering cache policy.

## Prerequisites

- Dataset metadata: `dataset_id`, `data_type`, `size_gb`, `age_days`
  (of the **newest** record), `current_tier`, `regulatory_retention_years`.
  Optionally `object_count` and `days_in_current_tier` — without them the engine
  cannot check small-object or early-deletion exposure and says so in `notes`.
- A retention period determined by compliance for each record type. SEC Rule
  17a-4 is not a single 6-year rule: 17a-4(a) imposes 6 years for the records it
  enumerates, 17a-4(b)(1) imposes 3 years for others including order memoranda,
  and **both** require the first two years in an "easily accessible place".
- A storage pricing map. The bundled `TIER_PRICING_USD_PER_GB` holds
  **illustrative us-east-1 list prices** (`HOT_NVME` $0.20, `WARM_PARQUET_S3`
  $0.023, `COLD_GLACIER_INSTANT` $0.004, `DEEP_ARCHIVE` $0.00099 per GB-month).
  The model uses S3 Standard's first-50 TB band as a flat rate and excludes
  retrieval fees, data transfer and volume tiering — pass your own `pricing_map`
  before quoting a number to anyone.

## Workflow

1. **Place the dataset on the age ladder** — age-driven only; the retention
   period never moves a rung:
   - $\le 30$ days $\implies$ `HOT_NVME`
   - $31$–$365$ days $\implies$ `WARM_PARQUET_S3`
   - $366$–$2555$ days $\implies$ `COLD_GLACIER_INSTANT`
   - $> 2555$ days $\implies$ `DEEP_ARCHIVE`, subject to steps 2 and 3.
2. **Apply the retention gate.**
   `retention_days = \lceil \text{years} \times 365.25 \rceil` (rounded up, so
   leap-day truncation never expires a record early);
   `retention_expired = age\_days > retention\_days`. If retention has not
   expired, deletion is prohibited. If it has, the dataset is *purge-eligible*,
   which is not purge-recommended: `PURGE` is emitted only when the engine was
   constructed with `purge_expired_records=True` **and** the `data_type` is not
   a regulated record type. Otherwise the answer is `DEEP_ARCHIVE` plus a note
   saying why deletion was withheld.
3. **Apply the easily-accessible floor.** No tier requiring a restore job is
   recommended for a record younger than `min_instant_access_days` (default
   730). A `DEEP_ARCHIVE` target inside that window is clamped to
   `COLD_GLACIER_INSTANT` — Deep Archive restores take hours and cannot satisfy
   17a-4's "easily accessible place" or 17a-4(j)'s "furnish promptly".
4. **Cost the move, then read the notes.** Gross delta is
   $\text{Size}_{\text{GB}} \times (\text{Price}_{\text{current}} - \text{Price}_{\text{target}})$,
   plus per-object Glacier metadata (32 KB at the archive rate + 8 KB at S3
   Standard rates) when `object_count` is given. A positive savings figure next
   to a small-object or early-deletion warning is frequently a net loss.
5. **Emit and persist.** Generate S3 Lifecycle rules and Parquet compaction jobs
   from the recommendations; persist the whole `DataRetentionAuditReport`,
   `notes` and `retention_expired` included, as the audit trail. Deletion stays a
   separate, human-approved step.

> Full procedure: see `references/workflows.md`.
> Standards reference: see `references/standards.md`.
> Printable pre-flight checklist: see `assets/checklist.md`.

## Common Pitfalls

- **Deriving the purge boundary from the retention period.** If retention is
  treated as the last ladder rung, a dataset with `regulatory_retention_years=0`
  becomes "expired" on day zero and is recommended for deletion the moment it
  ages past WARM — while the documented policy promises a seven-year floor. The
  ladder must be age-driven and retention must be a gate laid over it.
- **Archiving records that are still inside the two-year accessible window.**
  A 13-month-old order-memorandum dataset with a 1-year retention period is
  *expired* but still inside 17a-4(b)(1)'s "first two years in an easily
  accessible place". Deep Archive restores take hours; that placement is a
  compliance failure even though the retention arithmetic looked satisfied.
- **Archiving millions of small Parquet parts.** Since September 2024, S3
  Lifecycle by default **refuses to transition objects under 128 KB at all**
  (an object-size filter is required to override it), Standard-IA and Glacier
  Instant Retrieval bill a 128 KB minimum per object, and Glacier/Deep Archive
  adds 40 KB of per-object metadata — 32 KB at the archive rate plus 8 KB at
  full S3 Standard rates. For 10 million objects that 8 KB slice alone is ~76 GB
  billed at Standard rates, which can exceed the archive storage charge itself.
  Compact first.
- **Ignoring minimum storage durations.** Glacier Instant Retrieval has a 90-day
  minimum and Deep Archive 180 days. A COLD→DEEP hop on day 30, or purging 60
  days after archiving, is billed for the remainder. A single lifecycle rule
  cannot even express a chained transition inside the first class's minimum.
- **Quoting gross savings as the bill impact.** The savings figure excludes
  retrieval fees, transfer, request charges and S3 Standard's volume tiering.
  Supply `transition_price_per_1000_requests_usd` to get a payback period; a
  payback longer than the dataset's remaining retention life destroys value.
- **Letting an unknown tier name through.** A `current_tier` the pricing map does
  not recognise must raise, not silently price at some default — that produces
  an authoritative-looking savings number from a typo.

## Verification

- Instantiate `DataRetentionPolicyEngine()`. Input a 100,000 GB dataset at
  `age_days=120` in `HOT_NVME`: expect `WARM_PARQUET_S3`, $20,000/mo → $2,300/mo
  ($17,700/mo gross saving). Input a 50,000 GB dataset at `age_days=500` in
  `WARM_PARQUET_S3`: expect `COLD_GLACIER_INSTANT`, $1,150/mo → $200/mo.
- Safety check: a dataset at `age_days=366` with
  `regulatory_retention_years=0.0` must recommend `COLD_GLACIER_INSTANT`, never
  `PURGE`, even with `purge_expired_records=True`.
- Safety check: a `TRADE_AUDIT_LOG` at `age_days=400` must never be routed to
  `DEEP_ARCHIVE`, whatever `deep_archive_after_days` is configured to.
- Run `python -m unittest discover -s skills/data-retention-policy-and-storage-tiering/scripts`.

## Related Skills

- `historical-tick-data-storage-and-compaction`
- `data-localization-requirements-for-trade-records`
- `record-retention-periods-by-jurisdiction`
- `record-keeping-requirements-for-tax-audit-defense`
