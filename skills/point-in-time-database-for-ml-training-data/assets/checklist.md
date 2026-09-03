# Pre-Flight / Sign-off Checklist — point-in-time-database-for-ml-training-data

Run before any training matrix produced by this engine is used to fit a model
that will inform capital allocation.

## Data contract

- [ ] Every feature record carries **both** `event_timestamp` and `available_at`.
- [ ] `available_at` is sourced from the publisher's release calendar, not synthesised from the event date. If synthesised, the assumption is recorded alongside the dataset and the output is **not** described as point-in-time correct.
- [ ] `revision` is populated wherever a value can be corrected without the publication instant changing.
- [ ] `label_timestamp` is the instant the prediction is made, not the instant the outcome is realised.

## Timestamp handling

- [ ] All timestamps are ISO 8601 with every component zero-padded, or `date`/`datetime` objects.
- [ ] No raw ISO string is compared with `<=` anywhere in the pipeline (RFC 3339 §5.1).
- [ ] Feeds emitting naive local time are converted to UTC upstream of ingest.
- [ ] `date_only_availability` policy chosen deliberately, and `start_of_day` used only where same-day publication order was independently verified.

## Join configuration

- [ ] `ingestion_lag_days` reflects the measured delay between publication and your pipeline holding the value.
- [ ] `max_staleness_days` is set for every decaying feature, and the value is defended in writing.
- [ ] `require_event_before_label` is `False` unless the feature must describe a completed period.

## Output audit

- [ ] `available_at <= label_timestamp` holds for every joined row.
- [ ] `future_leakage_prevented_count` reviewed; for any non-zero count, `naive_join_value` vs `feature_value` inspected to size the leak that was blocked.
- [ ] Missing and stale rows reviewed separately — a high stale count means the staleness bound is doing work and the feature may not be usable at this label frequency.
- [ ] `valid_pit_rows + stale_feature_rows + missing_feature_rows == total_joined_rows` confirmed.
- [ ] Downstream code filters on `is_valid_pit`, never on `feature_value is not None` alone.
- [ ] No imputation happens inside the join; any fill policy is applied downstream and documented.
- [ ] Rebuilding the matrix from the same inputs produces a byte-identical result (revision ties resolve deterministically).

## Automated testing

- [ ] Run `python -m unittest discover -s skills/point-in-time-database-for-ml-training-data/scripts` — 100% pass rate (37 tests).
- [ ] Restatement regression covered: a value published after the label instant is not returned.
- [ ] Same-day boundary covered for the configured `date_only_availability` policy.

## Sign-off

- Reviewed by: ___________________________
- Date: ___________________________
- Dataset / run ID: ___________________________
