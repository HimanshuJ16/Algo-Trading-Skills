---
name: point-in-time-database-for-ml-training-data
description: >-
  Use when assembling a feature and label matrix, to as-of join features on the
  knowledge axis so no row carries a restated value that was not knowable at the label
  timestamp. The storage schema itself is
  backtest-database-schema-for-point-in-time-queries.
license: Apache-2.0
metadata:
  domain: algorithmic-trading
  subdomain: financial-ml
  tags: financial-ml, point-in-time-db, training-data, feature-store, data-leakage-prevention, as-of-join, knowledge-time, staleness-bound
  brokers_frameworks: "Point-In-Time ML Database Engine; Python Standard Library"
  version: "2.0.0"
  author: algo-trading-skills-contributors
---

## When to Use

Invoke this skill when assembling the feature/label matrix that an ML alpha model will train on. A standard SQL join on `(symbol, date)` silently pairs each label with whatever value the feature table holds *today* — the restated EPS, the revised GDP print, the corrected vendor field. The model then learns from information that did not exist at the moment it is supposed to be predicting, and the backtest reports a Sharpe the strategy cannot reproduce live.

This engine joins on the **knowledge axis** only: for each label instant `T` it returns the latest feature value whose `available_at <= T`. It additionally reports what a naive event-date join *would* have returned, so the leakage you avoided is a number in an audit report rather than an article of faith.

Two axes, following the same vocabulary as `backtest-database-schema-for-point-in-time-queries`:

| Axis | Field | Meaning | Used as the join gate? |
|---|---|---|---|
| Knowledge time | `available_at` | When the value became externally knowable (publication / filing release). | **Yes** |
| Event (valid) time | `event_timestamp` | The period or event the value describes (fiscal quarter end, reference month). | No — audit only |

## When NOT to Use

- **As the schema/storage layer.** This is a join engine over an in-memory store. Designing the bitemporal tables, indexes and ingest contracts is `backtest-database-schema-for-point-in-time-queries`.
- **For fundamentals joins driven by SEC filing dates specifically.** `point-in-time-fundamentals-data-joins` models filer status, `period_end_date` and restatement chains directly.
- **For index membership over time.** Survivorship-bias-free universes are `point-in-time-index-constituent-tracking`; this engine joins features, not membership windows.
- **For live inference.** As-of machinery reconstructs the past. At inference time you already hold the current value — reading it through a historical join adds latency and a class of bug that only shows up in production. Parity between the two paths is `feature-store-for-live-and-backtest-parity`.
- **When you cannot source a real `available_at`.** If the vendor ships only a reference date and you synthesise `available_at = event_timestamp + guess`, this engine will faithfully enforce a fiction. Record the assumption and the guess; do not present the output as point-in-time correct.
- **As a substitute for the rest of leakage control.** A PIT-correct matrix still permits overlapping labels, target-derived features and fold contamination — see `feature-engineering-without-leakage`, `sample-weighting-for-overlapping-labels` and `hyperparameter-tuning-without-target-leakage`.

## Prerequisites

- Feature records carrying **both** timestamps: `event_timestamp` (period described) and `available_at` (publication instant). One timestamp is not enough — a store holding only one of them cannot answer a point-in-time question at all.
- Timestamps as ISO 8601 strings, `datetime.date`, or `datetime.datetime`. Every component zero-padded. Naive datetimes are interpreted as UTC; if your feed emits naive local time, convert upstream.
- A `revision` counter wherever a value can be corrected without the publication instant changing.
- Label records whose `label_timestamp` is the instant the **prediction is made**, not the instant the outcome is realised.
- A decided `date_only_availability` policy, and — if features go stale — a defended `max_staleness_days`.

## Workflow

1. **Normalise every timestamp at the boundary.** Parse to timezone-aware UTC on ingest; never compare raw ISO strings. RFC 3339 §5.1 makes lexicographic ordering chronologically correct *only* when every value shares one zone representation and one fractional-second precision — a guarantee no multi-vendor feed provides. The engine rejects unpadded and unparseable input rather than storing it and mis-sorting it later.

2. **Resolve date-granular availability explicitly.**
   - **Decision point — a bare date is not an instant.** When `available_at` is `2023-01-20` with no time, you do not know whether it landed before or after that day's close. The default `"end_of_day"` policy resolves it to `2023-01-21T00:00Z`, so a value published on day D is *not* usable for a decision made on day D. `"start_of_day"` permits same-day use and is defensible only when you have independently verified the publication preceded the decision.
   - A date-granular `label_timestamp` resolves to the **start** of its day: a model predicting "on 2023-01-21" may use only what was knowable before the day began.

3. **Add the ingestion lag you actually incur.** `available_at` is when the *publisher* released the value. `ingestion_lag_days` shifts it by the delay before your pipeline held it. A value the vendor posted at 16:05 and your loader picked up in the 02:00 batch was not available for that afternoon's decision.

4. **Execute the as-of join on the knowledge axis.** For each label, take the record with the greatest `(available_at, revision, insertion_sequence)` satisfying `available_at <= label_timestamp`.
   - **Decision point — "latest" must be a total order.** Two records sharing one `available_at` (an original and a same-instant correction) resolve by `revision`, then by insertion order. Selecting with `max()` on the timestamp alone returns whichever happened to be loaded first, so the same dataset rebuilds differently.

5. **Bound staleness.** Without `max_staleness_days` a value published in 2010 joins to a 2023 label and reports as valid point-in-time. It *is* point-in-time correct and it is also useless. Refused rows are flagged `is_stale`, counted separately from missing rows, and keep their `feature_available_at` so the drop is auditable. This is the role `tolerance` plays in `pandas.merge_asof`.

6. **Audit against the naive join.** The engine independently resolves what an event-date join would have returned. Where that differs from the point-in-time answer, the row carries `leakage_blocked=True` and `naive_join_value`.
   - **Decision point — count label rows, not filtered records.** A naive join returns one value per label, so at most one row can be wrong per label. Reporting "3 future revisions blocked" because three unreleased records were filtered overstates the finding and hides which row was actually at risk.

7. **Emit the matrix without imputing.** Unknowable and stale cells stay `None` and the row is marked `is_complete=False`. Forward-filling or back-filling inside the join reintroduces precisely the leakage the join exists to prevent. Impute downstream, where the policy is visible.

> Full procedure: see `references/workflows.md`.
> Standards reference: see `references/standards.md`.
> Printable pre-flight checklist: see `assets/checklist.md`.

## Common Pitfalls

- **Joining on the event/reference date**: Q4 EPS describes the period ending 2022-12-31 but is not knowable until the filing lands. Under SEC deadlines a 10-K is due 60/75/90 days after fiscal year end and a 10-Q 40/40/45 days after quarter end, by filer category — so an event-date join hands the model up to a quarter of future knowledge. Macro series are worse: a GDP print describes a quarter that ended weeks earlier and is then revised for years.
- **Comparing ISO strings instead of instants**: `2023-02-01T09:00:00-05:00` string-sorts before `2023-02-01T12:00:00Z` but is two hours *later*. The leak is silent and affects only the rows where the offsets differ.
- **Unpadded dates**: a single `2023-9-01` sorts after `2023-10-01`, so the record becomes invisible to every as-of query that should have returned it — a missing feature, not an error.
- **Treating a publication date as midnight**: an earnings release stamped only `2023-06-01` is usually after the close. Start-of-day resolution makes it tradable a full session early.
- **Ignoring vendor ingestion lag**: the publisher's release instant is not the instant your pipeline held the value. Assuming vendor data was available at the close it describes is the same bug one layer down.
- **Unbounded carry-forward**: a 13-year-old value joined to today's label passes every availability check and is still not a feature.
- **Non-deterministic revision ties**: resolving same-instant corrections by insertion order makes the training matrix irreproducible across rebuilds — see `backtest-determinism-and-reproducibility`.
- **Letting `NaN` into the matrix**: a non-finite feature value propagates through training and silently poisons whichever estimator tolerates it. The engine rejects non-finite values and targets at ingest.
- **Filling missing cells inside the join**: forward-filling from a later publication is leakage wearing the costume of data hygiene.
- **Reading `feature_value` without checking `is_valid_pit`**: `is_valid_pit` is the single gate. It is `False` for both missing and stale rows, and `feature_value` is `None` in both cases.

## Verification

- Insert Q4 EPS = 1.50 (`event_timestamp` 2022-12-31, `available_at` 2023-01-20) and Q1 EPS = 1.80 (2023-03-31, 2023-04-18). Query a label at 2023-04-01: verify `feature_value` is **1.50**, `naive_join_value` is **1.80**, and `leakage_blocked` is `True` — the quarter has ended but the filing has not landed.
- Boundary: with `available_at` = 2023-01-20 date-only, a label at 2023-01-20 must **not** join; 2023-01-21T00:00Z must join with `staleness_days` = 0.0; 2023-01-20T23:59:59.999999Z must not.
- Restatement: original 1.50 published 2023-02-15, restatement 1.20 published 2023-08-10. A label at 2023-03-01 must return 1.50; at 2023-09-01, 1.20.
- Offset handling: a record `available_at` = `2023-02-01T09:00:00-05:00` (i.e. 14:00Z) must be excluded at a 12:00Z label and included at 15:00Z, though raw string comparison would admit it at both.
- Determinism: insert two records sharing one `available_at` with `revision` 0 and 1 in **both** orders; the `revision=1` value must win in both.
- Leakage counting: three records all published 2023-04-18 with events 2023-01-31, 2023-02-28 and 2023-03-31, queried at 2023-04-01, must report `future_leakage_prevented_count == 1` (one label row at risk), not 3.
- Negative checks: non-finite value or target, empty `symbol`/`feature_name`, negative `revision`, unpadded date, duplicate feature names, and each out-of-range constructor argument must all raise `ValueError`. A batch containing one bad record must leave the store unchanged.
- Run `python -m unittest discover -s skills/point-in-time-database-for-ml-training-data/scripts` and confirm 100% pass rate (37 tests).

## Related Skills

- `backtest-database-schema-for-point-in-time-queries`
- `point-in-time-fundamentals-data-joins`
- `point-in-time-index-constituent-tracking`
- `feature-engineering-without-leakage`
- `feature-store-for-live-and-backtest-parity`
- `lookahead-bias-elimination`
- `backtest-determinism-and-reproducibility`
