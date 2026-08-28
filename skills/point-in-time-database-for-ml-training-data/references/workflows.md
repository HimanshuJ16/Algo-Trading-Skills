# Deep Workflow Reference — point-in-time-database-for-ml-training-data

This file holds the full technical procedure referenced by `SKILL.md`.

## Full Procedure

### 1. Establish both time axes on every feature record

A record needs `event_timestamp` (the period it describes) **and** `available_at` (when it became knowable). A store holding only one cannot answer a point-in-time question; it can only guess. Add a `revision` counter wherever a value can be corrected without the publication instant changing.

If the vendor ships only a reference date, you have a choice to make and to record: either source the release calendar separately, or synthesise `available_at` and document it as an assumption. A synthesised `available_at` produces a dataset that is *internally* consistent and *externally* unverified — do not describe its output as point-in-time correct without the caveat.

### 2. Normalise timestamps at the boundary

Parse to timezone-aware UTC on ingest. Do not defer this to comparison time, and do not compare raw ISO strings (see `references/standards.md` on RFC 3339 §5.1). Reject unpadded or unparseable values at ingest — a `2023-9-01` that sorts after `2023-10-01` becomes an invisible record, which surfaces as an unexplained missing feature rather than an error.

`insert_features` validates the entire batch before indexing any of it, so a malformed load leaves the store unchanged rather than half-populated.

### 3. Resolve date-granular availability

A bare date is a day, not an instant. Choose the policy deliberately:

- `end_of_day` (default): `available_at = D` resolves to `D+1 00:00Z`. A value published on day D cannot serve a decision made on day D. Correct when you do not know the intraday release time — which is the normal case for daily vendor extracts.
- `start_of_day`: resolves to `D 00:00Z`, permitting same-day use. Defensible only when you have independently verified the publication preceded the decision.

Label timestamps with date granularity always resolve to the start of their day.

### 4. Apply ingestion lag

`ingestion_lag_days` shifts every resolved `available_at` forward by the delay between publication and your pipeline actually holding the value. This is a distinct failure from publication lag and compounds with it: a value the vendor posted at 16:05 and your nightly loader ingested at 02:00 was not available to that afternoon's decision, however correct its `available_at` is.

### 5. Execute the as-of join

For each label, select the record with the greatest `(available_at, revision, insertion_sequence)` satisfying `available_at <= label_timestamp`.

The three-part key is not decoration. Ordering on `available_at` alone leaves same-instant corrections resolving by whichever record happened to load first, so the same inputs rebuild into a different training matrix — an irreproducibility bug that surfaces as unexplained metric drift between runs.

By default the event axis does not gate the join. This is deliberate: published forward guidance or a forecast is legitimately knowable at `T` even though the period it describes has not occurred. Set `require_event_before_label=True` only when the feature must describe a completed period, and understand you are then excluding all forward-looking data.

### 6. Bound staleness

Set `max_staleness_days` when a feature decays. Without it, the last value ever published joins to every subsequent label indefinitely and reports as valid point-in-time — which it is, and which does not make it a feature.

Refused rows are distinguished from missing rows: `is_stale=True`, `is_valid_pit=False`, `feature_value=None`, and `feature_available_at` retained so the audit trail shows *what* was dropped and how old it was. `PITDatasetReport` counts the two categories separately, and `valid + stale + missing == total` holds by construction.

### 7. Audit against the naive join

The engine resolves, independently, what an event-date join would have returned: the greatest `(event_timestamp, revision, sequence)` at or before the label. Where that record is not the point-in-time answer, the row carries `leakage_blocked=True` and `naive_join_value`.

The counter counts **label rows**, not filtered records. A naive join returns one value per label, so one label row can be wrong at most once. Counting filtered candidates instead inflates the figure — three unreleased records behind one label is one row at risk, not three — and obscures which rows actually needed attention.

Use `naive_join_value` as the diagnostic: comparing it against `feature_value` shows the magnitude of the leak, not merely its presence.

### 8. Emit the matrix

`build_training_matrix(labels, feature_names)` returns wide rows with one entry per requested feature. Unknowable and stale cells stay `None` and the row is `is_complete=False`.

The engine never imputes. Forward-filling from a later publication is exactly the leakage the join exists to prevent, and back-filling is worse. Decide imputation downstream where the policy is explicit and reviewable — and treat the missingness pattern itself as informative, since it encodes publication timing.

## Complexity

Records are stored append-only and the sorted views for each `(symbol, feature_name)` bucket are built lazily on first query, then cached until the next insert. A bulk load therefore costs one `O(n log n)` sort per bucket regardless of the order records arrive in, and each label lookup is `O(log n)` by binary search. Measured on a 200,000-record store loaded in reverse-chronological order (the worst case for incremental insertion): 0.80 s to load, 0.15 s to join 10,000 labels.

Interleaving inserts and queries invalidates the affected bucket's cache and forces a re-sort on the next query. Load first, then join.

## Production Implementation Reference

- Reference code: `scripts/pit_ml_database.py`
  - `PointInTimeMLDatabase` — engine (`insert_features`, `as_of_join`, `build_training_matrix`)
  - `FeatureRecord`, `LabelRecord` — inputs
  - `PITJoinRow`, `TrainingRow`, `PITDatasetReport` — outputs
- Automated unit tests: `scripts/test_pit_ml_database.py` (37 tests, standard library only).
