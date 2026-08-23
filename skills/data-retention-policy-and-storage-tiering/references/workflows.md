# Workflows for Data Retention Policy and Storage Tiering

## 1. Describe the dataset

Capture `dataset_id`, `data_type`, `size_gb`, `age_days`, `current_tier` and the
`regulatory_retention_years` that compliance has determined applies to this
record type. Supply `object_count` and `days_in_current_tier` whenever known —
without them the engine cannot detect small-object and early-deletion exposure
and says so in its notes.

Age the dataset by its **newest** record. A partition dated by its oldest record
gets archived while it still contains recent data.

## 2. Place it on the age ladder

The ladder is age-driven only. The retention period never moves a rung.

| Age (days) | Tier | Retrieval |
|---|---|---|
| ≤ 30 | `HOT_NVME` | ms |
| 31 – 365 | `WARM_PARQUET_S3` | ms |
| 366 – 2555 | `COLD_GLACIER_INSTANT` | ms |
| > 2555 | `DEEP_ARCHIVE` (subject to the gates below) | hours |

## 3. Apply the retention gate

- `retention_days = ceil(regulatory_retention_years * 365.25)` — rounded up, so
  leap-day truncation never expires a record early.
- `retention_expired = age_days > retention_days`.
- If retention has **not** expired: deletion is prohibited, full stop.
- If retention **has** expired: the dataset is *purge-eligible*, which is not the
  same as purge-recommended. `PURGE` is only emitted when the operator built the
  engine with `purge_expired_records=True` **and** the `data_type` is not in
  `regulated_data_types`. Otherwise the recommendation is `DEEP_ARCHIVE` with a
  note explaining why deletion was withheld.

## 4. Apply the easily-accessible floor

No tier requiring a restore job is recommended for a record younger than
`min_instant_access_days` (default 730), because SEC Rule 17a-4(a) and (b)(1)
require the first two years in an "easily accessible place". A `DEEP_ARCHIVE`
target inside that window is clamped to `COLD_GLACIER_INSTANT` and the clamp is
recorded in `notes`. This holds regardless of how the age thresholds are
configured.

## 5. Cost the move, then read the notes

- Gross monthly delta: `size_gb * (price_current - price_target)`, plus per-object
  Glacier metadata (32 KB at the archive rate + 8 KB at S3 Standard rates) when
  `object_count` is supplied.
- Check `notes` before acting. A positive `monthly_cost_savings_usd` alongside a
  small-object warning or an early-deletion warning is frequently a **net loss**.
- Supply `transition_price_per_1000_requests_usd` to get the one-off request
  charge and a payback period. A payback beyond the dataset's remaining
  retention life means the transition destroys value.

## 6. Execute and record

Emit S3 Lifecycle rules from the recommendations, compacting first where the
small-object warning fired (objects under 128 KB will not transition by default
in any case). Persist the full `DataRetentionAuditReport`, including `notes` and
`retention_expired`, as the audit trail for why each dataset moved — or did not.
Any actual deletion should be a separate, human-approved step against the
purge-eligible list, not an automatic consequence of running this engine.
