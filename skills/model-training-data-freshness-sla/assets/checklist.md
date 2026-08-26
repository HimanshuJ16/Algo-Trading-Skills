# Pre-Flight Checklist — Training Data Freshness SLA

Sign off before wiring this gate into a retraining pipeline.

## Thresholds

- [ ] Are `target_sla_hours`, `warning_sla_hours` and `breach_sla_hours` derived from the dataset's actual publication cadence, rather than copied from the 24/36/48 defaults? (The defaults are illustrative and carry no external authority.)
- [ ] Is the ladder ordered `0 < target <= warning <= breach`, so every rung is reachable?
- [ ] Does `breach_sla_hours` exceed the longest legitimate non-publishing gap for this instrument — weekend plus adjacent holiday — **or** is `calendar_excluded_hours` being supplied instead?
- [ ] Does each rung map to an action someone will actually take? A warning nobody acts on is noise that trains operators to ignore the breach.

## Time and calendar

- [ ] Are both timestamps in **epoch seconds** (not milliseconds) and from the **same clock domain**?
- [ ] Is `timestamp_basis` declared honestly — `EVENT_TIME` where the timestamp is the market event, `INGESTION_TIME` where it is when the record landed locally?
- [ ] Is `calendar_excluded_hours` computed from the instrument's exchange calendar, including half-days and holidays?
- [ ] Has the Monday-morning case been tested end to end: Friday close, Monday retrain, healthy pipeline, no false halt?
- [ ] Is there an alert on `CALENDAR EXCLUSION IS LOAD-BEARING` in `audit_notes`? That flag marks every pass where the exclusion, not the data, decided the verdict — the one way this gate can fail open.
- [ ] Is `clock_skew_tolerance_seconds` set to absorb routine NTP drift between the pipeline host and the auditing host, and no more?

## Completeness gates

- [ ] Is `max_missing_days` set deliberately for this dataset rather than left at the default 2?
- [ ] Is `min_record_count` set where a truncated-but-fresh extract would be dangerous (0 disables the gate)?
- [ ] Is `missing_days_count` computed against the **expected** trading days for the window, not calendar days?

## Governance wiring

- [ ] Does the retraining job gate on `report.is_sla_breached` — not on `is_sla_compliant`, which is the stricter target-SLA signal and is `False` on both warning rungs?
- [ ] Is `action_on_breach` one of `HALT_MODEL_RETRAINING`, `REDUCE_CONFIDENCE`, `ALERT_ONLY`, and does downstream automation handle the exact string it will receive?
- [ ] Does a breach actually stop the job, rather than logging and continuing?
- [ ] Is the check invoked **immediately before every** retrain, not on an independent schedule?
- [ ] Is `config.dataset_name` the same dataset the job is about to read?

## Audit

- [ ] Is the full `FreshnessSlaReport` persisted with the trained model version, so a later investigation can recover the freshness verdict at training time?
- [ ] Are `data_lag_hours` (raw) and `effective_lag_hours` (calendar-adjusted) both retained?
- [ ] Are `ValueError` / `TypeError` from the engine escalated rather than caught and swallowed? Every one of them means the gate could not form a defensible verdict.
