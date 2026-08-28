# Workflows — post-breach-root-cause-analysis-template

The engine is the last step of an incident, not the first. Steps 0 and 5 are human; steps 1–4
are what `BreachRcaGenerator` does.

## 0. Before you open the template (human)

1. **Confirm containment.** An RCA written while the breach is still growing documents a
   moving target. Record `contained_at` as the moment the position stopped changing — kill
   switch engaged, algorithm disabled, positions flattened — not the moment the ticket closed.
2. **Preserve the evidence before it rolls off.** Order logs, drop copies, risk-gateway
   config history, deployment records. Retention windows are shorter than post-mortem
   schedules; see `structured-logging-for-post-incident-forensics`.
3. **Establish `detected_at` deliberately.** The instant responsible personnel first had a
   reasonable basis to conclude the breach had occurred — not the instant it was escalated,
   and not the instant the ticket was filed. Time-to-containment is measured from it.
4. **Check clock provenance for every log you will quote.** Note, per source, which host or
   venue clock the timestamps came from. That value goes into `TimelineEvent.source`.

## 1. Ingest and structurally validate

Construct `TimelineEvent`, `CapaItem`, then `BreachIncidentSpec`. Validation happens in
`__post_init__`, so a malformed record fails at construction, before any rendering.

Raises `ValueError` on:

- blank `incident_id`, `strategy_id`, `breach_type`, description, owner, or any "why";
- `financial_loss_usd` / `unauthorized_turnover_usd` that is negative, `NaN`, `inf`, or
  non-numeric (they are **magnitudes**: a 25,000 USD loss is `25000.0`);
- any naive datetime — in the spec, in a `TimelineEvent`, in a `CapaItem.due_date`, or as
  `generated_at`;
- `contained_at` earlier than `detected_at`;
- a `severity` that is not a `Severity` member, or a `capa_type` that is not a `CapaType`;
- a raw tuple in `timeline_events` or a raw string in `action_items`.

All timezone-aware datetimes are normalised to UTC on entry.

## 2. Apply the completeness gates

Every gate is evaluated; the engine does not return on the first failure.

| Finding | Trigger | Blocking? |
|---|---|---|
| `INSUFFICIENT_5_WHYS_DEPTH` | fewer than `min_five_whys_depth` entries | yes |
| `MISSING_ACTION_ITEMS` | no CAPA item at all | yes |
| `MISSING_TIMELINE` | no timeline event | yes |
| `CAPA_MISSING_OWNER_OR_DUE_DATE` | any CAPA item lacking an owner or a due date | yes |
| `RULE_VIOLATION_ASSESSMENT_MISSING` | `possible_rule_violation is None` | yes |
| `RCA_PAST_DUE` | `generated_at > rca_due_by` | yes |
| `TERMINAL_BLAME_ATTRIBUTION` | final "why" matches a blame phrase | **no — advisory** |

`status` is the first code present in the precedence order
`INSUFFICIENT_5_WHYS_DEPTH → MISSING_ACTION_ITEMS → MISSING_TIMELINE →
CAPA_MISSING_OWNER_OR_DUE_DATE → RULE_VIOLATION_ASSESSMENT_MISSING → RCA_PAST_DUE`, or
`RCA_GENERATED_SUCCESS`. `is_valid_rca` is `False` when any **blocking** finding is present;
advisory findings never affect it. Read `validation_findings`, not `status` alone.

`MISSING_ACTION_ITEMS` and `CAPA_MISSING_OWNER_OR_DUE_DATE` are mutually exclusive: with no
items at all there is nothing to be unassigned.

## 3. Assemble the chronology

Events are sorted by their normalised UTC timestamp. The sort is **stable**, so events sharing
a timestamp keep the caller's order — the only ordering information available when a clock
cannot separate them. `timeline_clock_sources` on the report lists the distinct clocks the
chronology depends on, deduplicated and sorted.

## 4. Render

Both artefacts are produced on every call, valid or not:

- **`markdown_document`** — six sections: header, financial impact, chronology, 5-Whys, CAPA,
  rule-violation assessment, audit findings. Unassigned CAPA items render `**UNASSIGNED**`
  and `**NO DUE DATE**` in bold. Findings are labelled `BLOCKING` or `ADVISORY`.
- **`json_payload`** — `json.dumps(..., indent=2, sort_keys=True)`. Deterministic: the module
  reads no wall clock, so equal inputs yield byte-identical output.

Free-text fields are whitespace-collapsed to a single line before rendering, so an embedded
newline cannot split one bullet into two.

## 5. Route the output (human)

1. **`is_valid_rca is False`** → return to the author with `validation_findings`. All gaps
   are listed at once, so this is one round trip, not one per defect.
2. **`possible_rule_violation is True`** → escalate to Compliance *before* finalising. For a
   FINRA member, an internal conclusion that a violation occurred starts the Rule 4530(b)
   30-calendar-day reporting clock; other regimes have their own. See `references/standards.md`.
3. **`has_preventive_action is False`** → challenge it. An all-corrective CAPA set has fixed
   one incident and prevented nothing.
4. **Retain the record.** It is a business record under the firm's retention schedule (for a
   FINRA member, at least six years under Rule 4511(b) absent a more specific period), in a
   format complying with SEA Rule 17a-4. See `record-retention-periods-by-jurisdiction`.
5. **Track CAPA items to closure.** The engine checks that owners and dates exist. It cannot
   check that the work happened; that belongs to
   `risk-control-configuration-change-approval-workflow` and the firm's issue tracker.
