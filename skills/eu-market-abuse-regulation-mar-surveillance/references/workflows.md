# Workflows — eu-market-abuse-regulation-mar-surveillance

## 1. Ingest the order and trade log

Collect `NEW`, `MODIFY`, `CANCEL`, `FILL` and `REJECT` events into `OrderExecutionEvent`
records. Two decisions matter before anything is scored:

- **Batch boundaries.** Size each batch so a full order lifecycle sits inside it. A
  `CANCEL` whose `NEW` fell into the previous batch cannot be timed; it is excluded from
  the cancel ratio and surfaces in `unmatched_cancels`. A rising `unmatched_cancels` is
  the signal that the window is cut too tight.
- **Clocks.** `timestamp_ns` is nanoseconds since the Unix epoch, UTC. Under MiFID II the
  applicable accuracy standard for venues and their members is RTS 25 (Delegated
  Regulation (EU) 2017/574). Do not run 100ms lifespan logic across feeds you have not
  reconciled.

## 2. Configure the engine

```python
engine = EuMarSurveillanceEngine(
    spoof_cancel_ratio_threshold=0.90,     # calibrate — not a regulatory value
    spoof_max_lifespan_ms=100.0,           # calibrate
    quote_rate_threshold_per_sec=500,      # must clear market-making obligations
    min_orders_for_cancel_ratio=5,
    beneficial_owner_map={"SUB_A": "ENTITY_X", "SUB_B": "ENTITY_X"},
    require_opposite_side_fill=False,
)
```

Out-of-range parameters raise `ValueError` at construction, not at the first alert.

## 3. Screen the batch

```python
report = engine.audit_events_for_mar_patterns(events)
```

Validation runs first — an unknown event type or side, a non-positive quantity, a
non-finite price, a negative timestamp, an event identifying no instrument, or a
duplicate `event_id` raises before any pattern is scored. Events are then sorted
internally, so the outcome does not depend on the order they arrived in.

Each detector groups by **(beneficial owner, instrument)**:

| Detector | Trigger | Severity |
|---|---|---|
| Wash trading | A `FILL` whose buyer and seller resolve to the same beneficial owner. | `CRITICAL` |
| Layering/spoofing | Fast-cancel ratio ≥ threshold over ≥ `min_orders_for_cancel_ratio` orders, **with** an opposite-side `FILL` inside the window. | `HIGH` |
| Layering/spoofing | Same ratio, no opposite-side fill observed — orders placed with no apparent intention to execute. | `MEDIUM` |
| Quote stuffing | Peak `NEW`/`MODIFY`/`CANCEL` count inside any one-second sliding window > threshold. Fills excluded. | `MEDIUM` |

## 4. Triage the alerts (human analysis)

For each alert, before anything is escalated:

1. Read `indicator_reference` — which MAR / Delegated Regulation (EU) 2016/522 indicator
   the pattern maps to.
2. Check `account_id` against the beneficial-owner mapping. A wash-trade alert on a
   give-up or internal booking representation is a mapping problem, not market abuse.
3. Check `opposite_side_fill_observed` on spoofing alerts. `MEDIUM` without a fill is a
   weaker case and is frequently legitimate quote maintenance.
4. Check the participant's quoting obligations before treating a quote-stuffing burst as
   abusive — a designated market maker in a liquid name quotes hard by design.
5. Pull the underlying events by `event_ids` and the window
   `first_event_timestamp_ns` → `last_event_timestamp_ns`.

## 5. Escalate and file

Reasonable suspicion is a human determination. Where it is formed, transpose the alert
into the NCA's STOR template — the harmonised template is the Annex to Delegated
Regulation (EU) 2016/957 — and submit through that authority's own channel (for example
the BaFin MVP portal's STOR procedure, or the AMF ROSA extranet). Enrolment with the
authority precedes the first submission; there is no EU-wide endpoint and this skill
submits nothing.

MAR Article 16 requires notification **without delay** once reasonable suspicion is
formed. Do not hold reports back to file them in a batch.

## 6. Retain the analysis for five years

Delegated Regulation (EU) 2016/957 requires the analysis of examined orders and
transactions to be retained for five years and produced to the competent authority on
request — **including the cases where no STOR was filed, and the reasons**. Persist, per
batch:

- the alerts and their `event_ids`,
- `report.detection_parameters` (the exact thresholds that produced them),
- the triage outcome and the reasoning, filed or not filed,
- `unmatched_cancels` and `groups_examined`, which describe the coverage of the run.

Without the parameters, a decision taken under one calibration cannot be reconstructed
after the thresholds have moved on.
