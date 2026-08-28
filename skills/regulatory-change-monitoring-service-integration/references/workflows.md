# Workflows — regulatory-change-monitoring-service-integration

The engine in `scripts/` covers steps 3–6. Steps 1–2 and 7–8 are the adapter and
governance work around it, described here so the boundary is explicit.

## 1. Feed ingestion (outside this engine)

- Poll each publisher's feed on the firm's cadence. Verify each publisher's terms
  of use and rate limits first — none are assumed by this skill.
- Suggested primary sources: Federal Register API (SEC rulemakings), FCA Handbook
  Notices, SEBI circulars, EUR-Lex / ESMA publications, MAS notices.
- Persist the raw payload before normalising. The raw record is the evidence that
  the firm received the notice on a given date.
- Use the publisher's own identifier as `update_id` (Federal Register
  `document_number`, Handbook Notice number, SEBI circular reference, CELEX
  number). Stable identifiers are what make replacement-on-revision possible.

## 2. Normalisation and classification (outside this engine)

- Map to `RegulatoryUpdate`. **Set `compliance_date` whenever the instrument sets
  a date distinct from its effective date.** For SEC rulemakings this is not in
  the structured feed — it must be extracted from the release body or entered by
  an analyst. Leaving it empty is the most consequential input error in this
  workflow (see `references/standards.md` for the T+1 evidence).
- A phased rulemaking becomes one `RegulatoryUpdate` per compliance phase, with
  distinct `update_id`s.
- Assign `severity` from the firm's taxonomy, `action_required`, and
  `impacted_subdomains` (the firm's own subject-area vocabulary, e.g.
  `SETTLEMENT`, `SHORT_SELLING`, `TICK_SIZE`, `REPORTING`). If nothing is known
  yet, leave `impacted_subdomains` empty — the engine retains unclassified items
  rather than filtering them away.
- A **revised** update replaces the existing record for that `update_id`. Passing
  both the original and the revision in one batch raises.

## 3. Regulator and subject-area filtering

- Regulator match is case-insensitive after stripping.
- Filtered updates are counted (`filtered_regulator_count`,
  `filtered_subdomain_count`) and unmonitored authorities are named
  (`filtered_regulators`). Review these counts every cycle: a rising unmonitored
  count usually means the firm's exposure has changed, not that the feed is noisy.
- Fail-open rule: an update with no `impacted_subdomains` survives an active
  subject-area filter and is logged at WARNING.

## 4. Deadline resolution

- Binding deadline = `compliance_date` if present, else `effective_date`.
  `deadline_basis` records which was used; `deadline_iso` records the date.
- `effective_date` is parsed and validated even when overridden.
- A `compliance_date` earlier than the `effective_date` is retained but logged at
  WARNING — it is usually a data-entry inversion.
- `days_until_effective = deadline − assessment date`, in calendar days. Negative
  means past due.

## 5. Status and escalation

| Condition | Status | Immediate action |
|---|---|---|
| `action_required` false | `MONITORING` | No |
| `remediation_complete` true | `COMPLIANT` | No |
| Open, deadline passed | `OVERDUE` | **Yes — at any severity** |
| Open, CRITICAL/HIGH, within window | `ACTION_REQUIRED` | Yes |
| Open, otherwise | `ACTION_REQUIRED` | No |

`action_required_count` counts open items only. Closing an item moves it to
`COMPLIANT`; it never disappears from `assessments`.

## 6. Reporting

- Assessments are sorted nearest-deadline-first, `update_id` as tie-break, so the
  report is byte-identical across runs over the same batch.
- `audit_notes` records the assessment date and every count. Persist the full
  `RegulatoryChangeReport`, not just the alerts — the filtered counts are part of
  the evidence that monitoring ran and what it consciously excluded.

## 7. Routing (outside this engine)

- Route `requires_immediate_action` items to the named compliance and engineering
  owners on the firm's escalation matrix.
- Route `OVERDUE` items to a **different, more senior** owner: these are live
  breaches, not planning items.
- Feed open items into the change-management process that owns testing and
  approval, so the compliance date is a *completion* date, not a start date.

## 8. Governance review

- Reconcile open items at each supervisory review, and at the RTS 6 Art. 9 annual
  self-assessment for EU/EEA algorithmic trading firms.
- Re-examine `monitored_regulators` and `monitored_subdomains` whenever the firm
  adds a venue, instrument class, or legal entity — the filter is only as good as
  the exposure map behind it.
