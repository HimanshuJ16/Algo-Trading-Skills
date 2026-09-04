---
name: model-training-data-freshness-sla
description: >-
  Use as the gate immediately before a scheduled retraining job, when an upstream
  pipeline you do not control assembles the dataset; measures event-time ingestion lag
  against a target and breach ladder, netting out exchange non-publishing days.
license: Apache-2.0
metadata:
  domain: algorithmic-trading
  subdomain: financial-ml
  tags: data-freshness, sla-monitoring, data-pipeline, feature-store, etl-lag, retraining-governance, data-contracts
  brokers_frameworks: "Feature Store SLAs; Data Pipeline Contracts; Python Dataclasses"
  version: "2.0.0"
  author: algo-trading-skills-contributors
---

## When to Use

Use this skill as the gate immediately before a scheduled model retraining job or a feature-store snapshot promotion, when the dataset is assembled by an upstream ETL pipeline you do not control. A stalled vendor feed or a silently failing DAG produces a dataset that still loads, still has the right schema, and still trains a model — one fitted to a market that has already moved on. The retrain then overwrites known-good weights with worse ones and nothing in the pipeline raises an error.

The engine answers one question: **is this dataset fresh enough to train on right now?** It measures the age of the newest record against a three-rung SLA ladder and returns the governance action to take.

| Rung | Condition | Action |
|---|---|---|
| `SLA_COMPLIANT` | effective lag $\le$ `target_sla_hours` | `PROCEED_NORMAL` |
| `SLA_WARNING_OFF_TARGET` | `target` $<$ lag $\le$ `warning` | `TRIGGER_BACKFILL_ALERT` |
| `SLA_WARNING_NEAR_LIMIT` | `warning` $<$ lag $\le$ `breach` | `ESCALATE_BACKFILL_URGENT` |
| `SLA_BREACH_CRITICAL` | lag $>$ `breach`, or gaps/row-count gates failed | `config.action_on_breach` |

Supervisory guidance on model risk management treats data relevance as an input to ongoing model monitoring, and testing as including "a critical assessment of data quality, relevance, and inputs" (SR 26-2 / OCC 2026-13) — but **no regulator prescribes a numeric freshness threshold**. The 24/36/48-hour defaults are illustrative operating points, not standards; derive yours from the dataset's own publication cadence. See `references/standards.md`.

## When NOT to Use

- **As a kill switch.** This gates *retraining*, not trading. Halting live strategies and flattening positions belongs to `kill-switch-and-drawdown-circuit-breakers`.
- **To decide whether a model has gone stale.** That is the opposite direction — an old *model* against fresh data — and belongs to `model-staleness-detection`. This skill only judges the *data*.
- **As a continuous feed-health monitor.** It evaluates one dataset at one instant from timestamps you supply. Per-vendor liveness, latency percentiles and completeness scoring across a live feed are `data-quality-monitoring-dashboard`.
- **As a source of exchange-calendar truth.** The engine owns no calendar. It accepts `calendar_excluded_hours` as an input and trusts it; compute it with `global-exchange-holiday-calendar-handling`.
- **To detect content-level corruption.** Fresh timestamps on wrong prices pass every check here. Value-level validation is out of scope.
- **On intraday feeds where the relevant unit is seconds.** Thresholds are hours; a sub-minute tick SLA is better served by the seconds-scale cutoffs in `strategy-specific-data-dependency-mapping`.

## Prerequisites

- A freshness contract per `(model_id, dataset_name)`: `target_sla_hours` $\le$ `warning_sla_hours` $\le$ `breach_sla_hours` (all $>0$), and `action_on_breach` $\in$ `{HALT_MODEL_RETRAINING, REDUCE_CONFIDENCE, ALERT_ONLY}`. Optional gates: `max_missing_days` (default 2), `min_record_count` (default 0 = disabled), `clock_skew_tolerance_seconds` (default 1.0).
- Dataset metadata: `latest_record_timestamp_epoch` and `current_system_timestamp_epoch`, both in **epoch seconds** and **the same clock domain**; `total_record_count`; `missing_days_count`.
- `timestamp_basis`: `EVENT_TIME` (default) or `INGESTION_TIME`. Freshness is only meaningful against event time; declaring `INGESTION_TIME` records the caveat in the audit trail rather than hiding it.
- `calendar_excluded_hours`: hours inside the audit window during which the dataset was **not expected to publish** (weekend, exchange holiday, overnight). Required for correctness on any session-bound dataset — see the first pitfall below.
- Python 3.10+. Standard library only.

## Workflow

1. **Validate the contract and the payload before computing anything.** Reject non-finite timestamps and thresholds, an inverted threshold ladder, negative counts, an unrecognised `action_on_breach`, and a `config.dataset_name` that disagrees with `metadata.dataset_name`. Every one of these is a caller bug that would otherwise produce a confident wrong verdict.
2. **Resolve clock skew before measuring lag.** If the newest record is dated *after* the evaluation timestamp, the lag is negative and the record's vintage is unknown. Within `clock_skew_tolerance_seconds` (routine host-to-host NTP drift), floor the lag at zero and record the skew in the audit note. Beyond it, raise — do not evaluate a dataset whose clock you cannot trust.
3. **Compute raw lag**: $\Delta t_{\text{raw}} = (T_{\text{current}} - T_{\text{latest\_record}}) / 3600$.
4. **Net out non-publishing calendar time**: $\Delta t_{\text{eff}} = \max(0, \Delta t_{\text{raw}} - h_{\text{excluded}})$. The verdict is computed from $\Delta t_{\text{eff}}$; the report carries both so the raw age stays auditable. If $h_{\text{excluded}} > \Delta t_{\text{raw}}$, raise — the caller's calendar claims more idle hours than have elapsed.
5. **Evaluate the hard-breach gates first**, collecting *every* condition that fired: lag past `breach_sla_hours`, `missing_days_count` past `max_missing_days`, `total_record_count` below `min_record_count`. Compare against the exact lag, never a rounded one — rounding to two decimals absorbs up to 18 seconds of real overshoot. A breach returns `config.action_on_breach` and names the actual triggers in the audit note; an audit record that misstates why it halted is worse than no note.
6. **Otherwise walk the warning rungs** from most to least severe so each configured threshold is reachable and maps to a distinct action.
7. **Emit `FreshnessSlaReport`.** Gate a hard stop on `is_sla_breached`; `is_sla_compliant` is the stricter "met the *target* SLA" and is `False` on both warning rungs.

> Full procedure: see `references/workflows.md`.
> Standards, sources and threshold provenance: see `references/standards.md`.
> Printable pre-flight checklist: see `assets/checklist.md`.

## Common Pitfalls

- **Measuring wall-clock lag on a session-bound dataset.** A daily-bar feed whose last record is Friday's 16:00 close is ~65 hours old when Monday's 09:00 retrain runs, on a pipeline that did exactly what it was supposed to do. Against a 48-hour hard limit that is a `HALT_MODEL_RETRAINING` every single Monday, plus a worse one after every long weekend — and once a team has silenced a recurring false halt, the true stall goes unnoticed with it. Always supply `calendar_excluded_hours`, or set thresholds that already span the longest non-publishing window the instrument has.
- **Letting NaN decide.** A NaN timestamp yields a NaN lag, and every `>` comparison against NaN is `False`. A ladder that falls through therefore returns `SLA_COMPLIANT` / `PROCEED_NORMAL` on unusable input — the gate fails *open*, which is the one direction it must never fail. Reject non-finite input as its own error.
- **Retraining on stale data.** The failure this skill exists to prevent: an automated retrain fits yesterday's prices replayed as today's and ships the result to production. Nothing throws, the metrics look normal, and the degradation surfaces days later as unexplained live/backtest divergence.
- **Measuring from ingestion time instead of event time.** Ingestion time is when the record reached *your* database, and it omits the vendor's own publication delay. A feed running four hours behind looks perfectly fresh the instant it lands. Declare the basis explicitly so the caveat is in the audit record.
- **Treating zero lag as freshness on a gapped series.** A backfill that wrote today's bar but skipped the previous four days has a lag of minutes and a hole where a fifth of the training window should be. Gate on gaps and row count independently of lag.
- **A typo in the action string.** `action_on_breach` flows into automation that matches it exactly. `"HALT_RETRAINING"` instead of `"HALT_MODEL_RETRAINING"` is a breach that is detected, logged, reported — and never acted on. Validate against the allowed set at configuration time.
- **An inverted threshold ladder.** Configuring `warning` above `breach` makes a rung unreachable and silently collapses the escalation path; the operator sees one undifferentiated warning band and loses the distinction between "off target" and "one hour from a halt".
- **Treating `calendar_excluded_hours` as verified.** It is a trusted caller input, and an over-stated exclusion silently disables the lag gate — the one place this design can be made to fail open. The engine rejects an exclusion larger than the elapsed window and flags any pass where the raw lag alone would have breached (`CALENDAR EXCLUSION IS LOAD-BEARING` in `audit_notes`, logged at WARNING), but it cannot verify the calendar itself. Alert on that flag.
- **Epoch milliseconds.** Passing milliseconds inflates lag ~1000x. This fails closed (a spurious breach), so it is survivable — but confirm the units before assuming a breach is real.
- **Rounding before comparing.** `round(lag, 2)` then `lag > breach` absorbs any overshoot under ~18 seconds. Classify on the exact value and round only for display.

## Verification

Run the unit suite:

```
python -m unittest discover -s skills/model-training-data-freshness-sla/scripts
```

54 tests cover:

- **Ladder separation** — 25h and 47h lag against a 24/36/48 ladder must return different statuses *and* different actions; the pre-2.0 engine returned identical verdicts for both because `warning_sla_hours` was never compared against anything.
- **Boundaries** — lag exactly at target (compliant), exactly at warning (off target), exactly at breach (near limit, not breached), and 10 seconds past breach (breached; the pre-2.0 rounding absorbed it).
- **Fail-closed on non-finite input** — NaN/Inf timestamps, NaN thresholds and NaN calendar hours raise instead of returning `SLA_COMPLIANT`.
- **Session calendar** — the Monday-after-Friday-close scenario breaches on raw lag (65h) and is compliant once 63 weekend hours are excluded, while a genuinely stalled pipeline (120h raw) still breaches with the same exclusion applied; a pass that depended on the exclusion is flagged as such, one that did not is not.
- **Audit-note accuracy** — a missing-days breach must not claim the lag exceeded the hard limit, a lag breach must not mention gaps, and a dual trigger must name both.
- **Validation** — inverted ladder, non-positive target, unrecognised breach action, dataset-name mismatch between config and payload, negative counts, boolean counts, unknown timestamp basis.
- **Clock skew** — sub-second skew absorbed and recorded; skew beyond tolerance raises; the effective lag is never negative.
- **Determinism** — identical inputs produce an identical report; the engine reads no clock of its own.

Repository checks:

```
python tools/validate_skills.py
```

## Related Skills

- `model-staleness-detection`
- `data-quality-monitoring-dashboard`
- `global-exchange-holiday-calendar-handling`
- `strategy-specific-data-dependency-mapping`
- `concept-drift-vs-staleness-differentiation`
- `point-in-time-database-for-ml-training-data`
- `reproducible-ml-training-pipelines`
- `kill-switch-and-drawdown-circuit-breakers`
