# Workflows for Training Data Freshness SLA

The engine is a gate, invoked synchronously immediately before a retraining job or a
feature-store snapshot promotion. It is stateless, reads no clock of its own, and is
fully deterministic given `(config, metadata)` — so any verdict can be replayed from the
audit log.

## 0. Set the thresholds from the dataset's cadence

Do this before writing any code. Ask, for the specific dataset:

- How often is it *supposed* to publish? Once per session, hourly, continuously?
- What is the longest legitimate gap between publications? For a session-bound feed
  this is the longest weekend-plus-holiday run in the instrument's calendar, not 24
  hours.
- How late can the vendor be before the model materially suffers?

`breach_sla_hours` must exceed the longest legitimate gap unless you are supplying
`calendar_excluded_hours`. A ladder that cannot be satisfied by a healthy pipeline
produces recurring false halts, and a recurring false halt gets silenced.

The 24/36/48 defaults encode one illustrative case: a daily-bar dataset audited on a
weekday, with a weekend allowance handled through calendar exclusion rather than
through the thresholds.

## 1. Validate the contract and the payload

Reject, before any arithmetic:

- Non-finite (`NaN`, `±inf`) timestamps, thresholds or calendar hours — NaN silently
  defeats every `>` comparison and would return `SLA_COMPLIANT`.
- A ladder that is not ordered `0 < target <= warning <= breach`.
- `action_on_breach` outside `{HALT_MODEL_RETRAINING, REDUCE_CONFIDENCE, ALERT_ONLY}`.
- Negative `total_record_count`, `missing_days_count`, `max_missing_days`,
  `min_record_count` or `clock_skew_tolerance_seconds`.
- A `timestamp_basis` other than `EVENT_TIME` / `INGESTION_TIME`.
- `config.dataset_name != metadata.dataset_name` — evaluating one dataset's contract
  against another's metadata mislabels the verdict in the audit record, and the halt (or
  the pass) lands on the wrong model.

## 2. Resolve clock skew

If `latest_record_timestamp_epoch > current_system_timestamp_epoch`, the lag is negative.
A negative age is not fresher-than-fresh data; it means the two timestamps came from
clocks that disagree.

- Skew within `clock_skew_tolerance_seconds` (default 1.0s): routine host-to-host NTP
  drift. Floor the lag at zero, record the measured skew in the report, continue. A
  nightly governance job must not crash on 400ms of drift.
- Skew beyond tolerance: raise. The record's vintage is unknown, so no verdict is
  defensible. Fix clock synchronisation — see `clock-synchronization-ptp-for-trading-hosts`
  and `clock-drift-monitoring-alerting-thresholds`.

## 3. Compute raw and effective lag

```
raw_lag_hours       = (current_system_timestamp_epoch - latest_record_timestamp_epoch) / 3600
effective_lag_hours = max(0, raw_lag_hours - calendar_excluded_hours)
```

`calendar_excluded_hours` is the span inside the audit window during which the dataset
was **not expected to publish**. Compute it from the instrument's exchange calendar
(`global-exchange-holiday-calendar-handling`); this engine deliberately owns no calendar,
because a calendar embedded in a freshness monitor drifts out of sync with the one the
rest of the stack uses.

If `calendar_excluded_hours` exceeds `raw_lag_hours`, raise: the caller's calendar claims
more non-publishing hours than have actually elapsed, which is a bug in the calendar
integration, not a very fresh dataset.

The verdict is computed from `effective_lag_hours`. Both figures are reported, so the raw
age of the data stays visible to anyone reading the audit trail.

`calendar_excluded_hours` is the one input that can make this gate fail open: an
over-stated exclusion cancels an arbitrarily large lag. The engine therefore flags any
non-breaching verdict whose raw lag alone would have breached the hard limit
(`CALENDAR EXCLUSION IS LOAD-BEARING` in `audit_notes`, logged at WARNING). Alert on that
string — it marks every verdict where the exchange calendar, not the data, decided the
outcome.

## 4. Evaluate the hard-breach gates

Collect **every** condition that fired, not the first:

| Gate | Condition | Why it is independent of lag |
|---|---|---|
| Lag | `effective_lag_hours > breach_sla_hours` | The primary staleness signal. |
| Gaps | `missing_days_count > max_missing_days` | A backfill can write today's bar and skip the previous four days — lag of minutes, a hole in the training window. |
| Volume | `total_record_count < min_record_count` | A truncated extract can be perfectly fresh and still too small to fit. Default 0 disables this gate. |

Compare against the **exact** lag. Rounding to two decimals before comparing absorbs an
overshoot of up to 18 seconds — small, but it means a dataset past the hard limit trains
anyway. Round only for presentation.

The audit note must name the conditions that actually fired. A fixed string that always
recites every possible trigger makes the audit record useless for post-incident work: it
cannot be distinguished from the case where it is telling the truth.

## 5. Otherwise walk the warning rungs

Most severe first, so each configured threshold is reachable:

| Condition | Status | Action |
|---|---|---|
| `effective_lag > warning_sla_hours` | `SLA_WARNING_NEAR_LIMIT` | `ESCALATE_BACKFILL_URGENT` |
| `effective_lag > target_sla_hours` | `SLA_WARNING_OFF_TARGET` | `TRIGGER_BACKFILL_ALERT` |
| otherwise | `SLA_COMPLIANT` | `PROCEED_NORMAL` |

Thresholds are inclusive ceilings: lag exactly equal to a threshold stays on that rung.

## 6. Consume the report

```python
report = engine.evaluate_training_freshness_sla(config, metadata)

if report.is_sla_breached:
    abort_retraining(report.recommended_governance_action, report.audit_notes)
elif not report.is_sla_compliant:
    page_data_engineering(report)   # off target or near limit; retrain may proceed
```

- `is_sla_breached` — the hard stop. Gate the retraining job on this.
- `is_sla_compliant` — the stricter "met the *target* SLA"; `False` on both warning rungs.
- `audit_notes` — retain with the model artefact. Together with the config and payload it
  reconstructs why this retrain was allowed or refused.

Persist the full report alongside the trained model version
(`model-versioning-and-rollback`, `reproducible-ml-training-pipelines`). When a model is
later found to have degraded, the freshness verdict at training time is the first thing
worth checking.

## 7. What this workflow does not cover

- Whether the *values* in the dataset are correct. Fresh timestamps on wrong prices pass
  every gate here.
- Whether the *model* has gone stale against fresh data — `model-staleness-detection`.
- Continuous per-vendor feed health — `data-quality-monitoring-dashboard`.
- Point-in-time correctness of the features themselves — `point-in-time-database-for-ml-training-data`,
  `lookahead-bias-elimination`.
