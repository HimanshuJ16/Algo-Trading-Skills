---
name: reference-data-change-notification-pipeline
description: >-
  Use when an instrument-master record is refreshed and something downstream will act on
  it, separating identity and routing changes such as symbol or ISIN from
  order-construction changes such as lot size or tick size.
license: Apache-2.0
metadata:
  domain: algorithmic-trading
  subdomain: data-management-global
  tags: reference-data, change-detection, notification-pipeline, instrument-master, corporate-actions, symbol-change
  brokers_frameworks: "ISO 10383 MIC Codes; ISO 6166 ISIN; CUSIP; FIGI (OpenFIGI); MiFIR RTS 23 / ESMA FIRDS; Python Dataclasses"
  version: "2.0.0"
  author: algo-trading-skills-contributors
---

## When to Use

Use this skill when an instrument-master record is refreshed from a vendor, an exchange directory file, or a golden-source reconciliation, and something downstream — a strategy keyed on a ticker, an OMS that rounds to a lot size, a risk engine keyed on a currency — will act on the new record without being told it changed.

The engine takes the *before* and *after* record for one instrument, reports every field that moved, grades each move by what it actually breaks, and hands the resulting notifications to registered consumers filtered by severity.

The grading is the point. Not every changed field is the same kind of accident:

- **`CRITICAL` — identity and routing.** `symbol`, `exchange`, `mic`, `status`, `currency`, `isin`, `cusip`, `sedol`, `figi`. A stale value here sends an order to the **wrong instrument or the wrong venue**, or to a symbol that now belongs to somebody else.
- **`WARNING` — order construction.** `lot_size`, `tick_size`, `contract_multiplier`, `min_order_qty`, `price_precision`, `expiry`, `strike`, `settlement_date`. A stale value here sends a **malformed or mis-sized** order to the right instrument: rejection, odd-lot, or an unintended notional.
- **`INFO` — everything else**, e.g. a changed long name or sector label.

A field *disappearing* is floored at `WARNING` whatever the field is, because a vendor silently dropping a column is a data-quality incident in its own right.

## When NOT to Use

- **As a corporate-action calendar or effective-date scheduler.** This diffs two states that already exist; it has no notion of an effective date. Reference-data changes routinely carry one — ISO 10383 MIC modifications are published on the second Monday of the month and become effective on the fourth. If your loader writes an announced-but-not-yet-effective value into the snapshot, this correctly reports a change that must not be acted on yet. Sequence effective dates upstream with `corporate-action-event-calendar-integration`.
- **As the reconciler between disagreeing vendors.** This compares one source against itself over time. Two vendors disagreeing *at the same instant* is a golden-source problem — see `reference-data-golden-source-designation` and `multi-source-price-reconciliation-tie-breaking`.
- **As symbol translation.** Detecting that `symbol` moved `FB → META` is not the same as maintaining a vendor↔canonical mapping — see `reference-data-symbol-mapping-across-vendors` and `isin-cusip-sedol-cross-reference-service`.
- **For universe-level membership changes** (instruments appearing in or leaving the tradable set). This is per-instrument, field-level; see `instrument-universe-change-detection-and-alerting`.
- **As the transport.** `route_notifications` attempts each delivery exactly once and returns the failures. Retry, backoff, ordering guarantees and dead-lettering belong to the message bus.
- **On a partial/delta payload without setting `treat_missing_as_removal=False`.** In the default full-snapshot mode, every field absent from `after` is reported as a removal — feed a delta payload and you get a mass-removal alert storm.

## Prerequisites

- A stable **`instrument_id` that is not the ticker**. The ticker is one of the fields whose change this engine exists to detect, so keying the master on it defeats the purpose. Prefer a persistent identifier: a FIGI never changes and is never reused; a US CUSIP survives a pure ticker rename (Meta's Class A CUSIP was explicitly unchanged when `FB` became `META` on 2022-06-09).
- **Before/after snapshots for the same instrument** as `Mapping[str, Any]`, both produced by the **same source and the same schema version**.
- **Canonicalized value types.** Comparison is plain `==`: `"100"` and `100` are reported as a change (a real schema change, not something to cast away silently), while `100` and `100.0` are not. Normalize types, case and padding in the loader — many vendors pad fixed-width fields, and `"AAPL "` vs `"AAPL"` is otherwise a `CRITICAL` alert every cycle.
- Python 3.9+. Standard library only.

## Workflow

1. **Validate the pair before diffing.**
   `detect_changes` raises `SnapshotError` on a blank `instrument_id`, a non-mapping snapshot, or a non-string field name — including when the engine is disabled, so a misconfigured caller cannot be masked by a disabled engine.
   - **Decision point — is `after` a full snapshot or a delta?** Full is the default. A delta payload requires `treat_missing_as_removal=False`, which also means removals can never be detected on that path; detect them by periodically diffing full snapshots instead.

2. **Diff field by field, tracking presence separately from value.**
   Absent and `None` are different facts: absent means the vendor stopped publishing the field, `None` means the vendor published "unknown". Every notification carries `old_present` / `new_present` alongside the values, and `change_type` is `ADDED`, `MODIFIED` or `REMOVED`.
   - **Decision point — a value that cannot be compared is treated as changed**, never as stable. If `__eq__` raises, the engine reports a change rather than assuming equality.

3. **Classify severity.**
   `CRITICAL` / `WARNING` / `INFO` per the field sets above, matched **case-insensitively** so a vendor publishing `Symbol` is not silently downgraded to `INFO`. Removals are then floored at `config.removal_min_severity` (default `WARNING`). Additions are not escalated — new data arriving is not the same risk as existing data vanishing.
   - **Decision point — an unrecognized field defaults to `INFO`.** That default is fail-quiet. Any field your OMS or risk engine actually reads must be named in `critical_fields` or `warning_fields`; the defaults are a starting point, not a survey of your schema.

4. **Route notifications by severity, isolating failures.**
   Register consumers with a `min_severity` (risk engine at `CRITICAL`, data-quality dashboard at `INFO`). Each `(consumer, notification)` pair is attempted **once**, in registration order.
   - **Decision point — a failing sink does not abort the dispatch.** A risk engine that is down must not stop the OMS from hearing that a symbol was renamed, so a raising callback is recorded in `NotificationDispatchResult.failures` and dispatch continues. **A non-empty `failures` list means a downstream system did not learn about a change it subscribes to** — that is an incident. Check `all_delivered`; do not fire-and-forget.

5. **Audit.** `ReferenceDataChangeReport` carries per-severity counts, `max_severity`, and the caller-supplied `as_of`. Each notification exposes a deterministic `change_key` (instrument, field, change type, rendered old/new — deliberately excluding `as_of`) so a re-run, replay or failover produces identical keys and consumers can de-duplicate without the engine holding state.

> Full procedure: see `references/workflows.md`.
> Standards reference: see `references/standards.md`.
> Printable pre-flight checklist: see `assets/checklist.md`.

## Common Pitfalls

- **Keying the instrument master on the ticker.** The one field guaranteed to change is the one used as the primary key, so a rename looks like "old instrument disappeared, new instrument appeared" and every open position, order and historical series keyed on `FB` is orphaned. Key on a persistent identifier and let `symbol` be an attribute that changes.
- **Assuming a ticker change implies an identifier change.** It does not. Meta's CUSIP was unchanged across `FB → META`, and since a US ISIN is the country code plus the CUSIP plus a check digit, the ISIN was unchanged too. Logic that reacts to a rename by re-resolving every identifier will churn through work that no corporate action justifies — and logic that *only* watches ISIN will miss the rename entirely.
- **Reading a snapshot with `dict.get()`.** It collapses "field absent" and "field present with value `None`" into the same `None`, so a vendor dropping a column reads as no change at all. Test presence with `in`.
- **Treating everything outside the critical list as harmless.** A `lot_size` that moved 100 → 200 does not misroute an order, it gets the order rejected — for every instrument, for the rest of the session. That belongs in a graded severity bucket, not in the same one as a changed sector label.
- **Acting on a change before its effective date.** Detection is not activation. A MIC modification published on the second Monday is not effective until the fourth; a ticker change announced on 31 May was not effective until 9 June. Applying it early routes orders to a venue code the exchange has not switched on yet.
- **Symbol reuse.** Exchanges recycle freed tickers. A backtest or reconciliation that joins on ticker without a date qualifier will silently splice two different companies into one series; join on a persistent identifier, or on (ticker, date).
- **Comparison noise from uncanonicalized values.** Trailing whitespace from a fixed-width feed, a case flip, a vendor switching a numeric field from string to int, or a `NaN` published for a missing numeric (`NaN` never equals itself, so it re-alerts every cycle forever) produces a `CRITICAL` alert on every instrument at once. When a full-universe alert storm fires, suspect the loader before the exchange.
- **Fire-and-forget dispatch.** Ignoring `NotificationDispatchResult.failures` reproduces exactly the failure this pipeline exists to prevent: the change was detected, and the system that needed it never heard.
- **Running with `enabled=False` and reading `NO_CHANGES`.** A disabled engine reports `ENGINE_DISABLED`, not `NO_CHANGES`. Never collapse the two — "we did not look" is not "there was nothing to find".

## Verification

- Instantiate `ReferenceDataChangeNotificationPipelineEngine()`. Diff `{"symbol": "FB", "lot_size": 100, "cusip": "30303M102"}` against `{"symbol": "META", "lot_size": 100, "cusip": "30303M102"}` $\implies$ exactly one notification, `field_name == "symbol"`, `severity == "CRITICAL"`, `max_severity == "CRITICAL"`, and **no** notification for `cusip`.
- Diff a `lot_size` 100 → 200 change $\implies$ `warning_changes == 1`, `info_changes == 0` (regression: this used to classify as `INFO`).
- Presence checks: `{"isin": None}` → `{}` must yield one `REMOVED` with `old_present=True, new_present=False`; `{"isin": "US0378331005"}` → `{"isin": None}` must yield `MODIFIED` with `new_present=True`; `{"isin": None}` → `{"isin": None}` must yield `NO_CHANGES`.
- Severity checks: `{"Symbol": "FB"}` → `{"Symbol": "META"}` must be `CRITICAL` (case-insensitive matching); removing an unrecognized field must be floored at `WARNING`; adding one must stay `INFO`.
- Routing checks: with a `CRITICAL`-only consumer and an `INFO` consumer over a 3-change report, verify `delivered == 4` and `skipped_below_threshold == 2`; with one consumer whose callback raises, verify the other consumer still received its notification, `all_delivered is False`, and `failed_consumers == ["<broken>"]`.
- Negative checks: a blank `instrument_id`, a non-mapping snapshot, a non-string field name, overlapping `critical_fields`/`warning_fields`, a bare-string field set, a duplicate consumer name, and an invalid `min_severity` must each raise.
- Run `python -m unittest discover -s skills/reference-data-change-notification-pipeline/scripts` and confirm a 100% pass rate.

## Related Skills

- `reference-data-symbol-mapping-across-vendors`
- `reference-data-golden-source-designation`
- `instrument-universe-change-detection-and-alerting`
- `corporate-action-event-calendar-integration`
- `isin-cusip-sedol-cross-reference-service`
- `data-quality-monitoring-dashboard`
