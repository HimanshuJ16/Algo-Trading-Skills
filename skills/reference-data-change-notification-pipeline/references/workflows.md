# Workflows for Reference Data Change Notification Pipeline

Full procedure behind `SKILL.md`. Every behaviour described here is exercised by
`scripts/test_reference_data_change_notification_pipeline.py`.

## 0. Establish the primary key before anything else

The instrument master must be keyed on an identifier that survives the changes this
pipeline detects. A FIGI never changes and is never reused; a US CUSIP survives a pure
ticker rename (Meta's was explicitly unchanged across `FB → META`). A ticker survives
nothing — it is the single most likely field to move.

If the master is keyed on ticker, a rename does not present as a change at all: it
presents as one instrument disappearing and a different one appearing, orphaning every
position, working order and historical series joined on the old key. No amount of
change detection downstream repairs that.

`instrument_id` passed to `detect_changes` is that persistent key. It is recorded on
every notification and forms the first component of the `change_key`.

## 1. Assemble the snapshot pair

Both snapshots must come from **the same source at the same schema version**. Diffing
vendor A's Monday record against vendor B's Tuesday record measures vendor disagreement,
not change over time — that is `reference-data-golden-source-designation`.

Canonicalize in the loader, not here:

- **Types.** Comparison is plain `==`. `"100"` vs `100` is reported as a change (it *is*
  a schema change, and coercing it away would hide it); `100` vs `100.0` is not.
- **Whitespace and case.** Fixed-width feeds pad. `"AAPL "` vs `"AAPL"` is otherwise a
  `CRITICAL` alert on every instrument, every cycle.
- **Enumerations.** A vendor switching `status` from `A`/`I` to `ACTIVE`/`INACTIVE` is a
  real schema change and *should* alert — once. Map it in the loader and re-baseline;
  do not suppress it in the engine.
- **`NaN`.** `float("nan") != float("nan")`, so a NaN published for a missing numeric
  re-alerts on every cycle, forever. Map it to `None` in the loader.

## 2. Choose the snapshot mode

| Mode | Config | Behaviour | Use when |
|---|---|---|---|
| Full snapshot (default) | `treat_missing_as_removal=True` | Union of both key sets is examined; a field in `before` and absent from `after` is `REMOVED`. | `after` is a complete record. |
| Delta | `treat_missing_as_removal=False` | Only fields present in `after` are examined. Removals are **undetectable** on this path. | `after` is a partial/incremental payload. |

Feeding a delta payload while in full-snapshot mode produces a mass-removal alert storm
— every unchanged field reads as removed. If you must run in delta mode, schedule a
periodic full-snapshot diff so removals are still caught eventually.

## 3. Validate before diffing

`detect_changes` raises `SnapshotError` on:

- a blank, whitespace-only, or non-string `instrument_id`;
- a snapshot that is not a `Mapping`;
- a snapshot with a non-string or empty field name.

Validation runs **before** the `enabled` check, so a misconfigured caller is not masked
by a disabled engine. `ChangeDetectionConfigError` covers configuration faults:
overlapping `critical_fields`/`warning_fields`, a bare string passed where a set of
field names was expected (it would iterate as characters), an empty or non-string field
name, or an invalid `removal_min_severity`.

## 4. Diff field by field, tracking presence

For each field in the candidate set, presence is tested with `in`, never with
`dict.get()`:

| `old_present` | `new_present` | Values | `change_type` |
|---|---|---|---|
| True | True | equal | *(no notification)* |
| True | True | differ | `MODIFIED` |
| False | True | — | `ADDED` |
| True | False | — | `REMOVED` |

This is what keeps `{"isin": None}` → `{}` (the vendor dropped the column) distinct from
`{"isin": None}` → `{"isin": None}` (no change) and from `{"isin": "US..."}` →
`{"isin": None}` (published as unknown). Consumers read `old_present`/`new_present` to
recover the distinction; when a side is absent, its value is `None`.

A value whose `__eq__` raises, or returns something that cannot be interpreted as a
bool, is treated as **changed**. Notifications are emitted in sorted field-name order,
so two runs over the same pair produce byte-identical reports.

## 5. Classify severity

```
base = CRITICAL if field ∈ critical_fields
       WARNING  if field ∈ warning_fields
       INFO     otherwise
severity = max(base, removal_min_severity) if change_type == REMOVED else base
```

Field names are casefolded on both sides, so `Symbol`, `SYMBOL` and `symbol` all match.
Additions are not escalated: new data arriving is not the same risk as existing data
vanishing.

The `INFO` default is **fail-quiet**. Every field your OMS, risk engine or strategy
actually reads must be named in one of the two sets. The shipped defaults are a starting
point, not a survey of your schema. Audit them against the fields your systems consume,
and re-audit when a vendor adds a column.

## 6. Route notifications

```python
consumers = [
    NotificationConsumer("risk-engine", risk.on_change, min_severity="CRITICAL"),
    NotificationConsumer("oms",         oms.on_change,  min_severity="WARNING"),
    NotificationConsumer("dq-dashboard", dash.on_change, min_severity="INFO"),
]
result = engine.route_notifications(report, consumers)
if not result.all_delivered:
    escalate(result.failures)   # not optional
```

Consumers are validated up front — unique non-blank names, callable callbacks, valid
`min_severity` — so a misconfigured set raises before any delivery is attempted rather
than half-way through.

Each `(consumer, notification)` pair is attempted **once**, in registration order then
field order. A callback that raises is caught, logged with `exc_info`, recorded as a
`DeliveryFailure(consumer_name, change_key, error)`, and dispatch continues to the next
notification and the next consumer.

**No retry happens here.** Retry, backoff, ordering and dead-lettering belong to the
transport, which is the only layer that knows whether a given sink is idempotent.
Because the same snapshot pair always yields the same `change_key`, a consumer can
de-duplicate a replayed or retried delivery without the engine holding state.

A non-empty `failures` list means a downstream system did not learn about a change it
subscribes to. Treat it as an incident, not a log line.

## 7. Gate application on the effective date

Detection is not activation. Reference-data changes are routinely published ahead of
their effective date — ISO 10383 MIC modifications are published on the second Monday of
the month and become effective on the fourth; Meta's ticker change was announced on 31
May 2022 and effective on 9 June 2022.

This engine has no notion of an effective date; it reports what the snapshot contains.
If the loader writes an announced-but-not-yet-effective value into the snapshot, the
change will be reported early and must not be applied early. Sequence effective dates
upstream — see `corporate-action-event-calendar-integration`.

## 8. Audit

`ReferenceDataChangeReport` carries `total_changes`, the three per-severity counts (which
sum to the total), `max_severity`, `status`, `audit_notes`, and the caller-supplied
`as_of`. The engine never reads a clock, so replaying a historical snapshot pair
reproduces the report exactly.

`status` distinguishes three outcomes that must never be collapsed:

| Status | Meaning |
|---|---|
| `CHANGES_DETECTED` | The pair was compared and differed. |
| `NO_CHANGES` | The pair was compared and matched. |
| `ENGINE_DISABLED` | **The pair was not compared at all.** |

`change_key` is `instrument_id | field_name | change_type | rendered old | rendered new`,
with an absent side rendered as `<absent>` so it can never collide with a literal
`None`. `as_of` is deliberately excluded: the same change observed at two times is one
change.
