---
name: regulatory-change-monitoring-service-integration
description: >-
  Use when a firm subscribes to regulatory feeds such as the Federal Register, FCA
  Handbook notices, SEBI circulars or EUR-Lex and needs to know which published changes
  still require work and how long is left before the operative deadline.
license: Apache-2.0
metadata:
  domain: algorithmic-trading
  subdomain: regulatory-compliance-global
  tags: regulatory-compliance-global, regulatory-change-monitoring, compliance-deadline-tracking, horizon-scanning, finra-rule-3110, rts-6-article-9
  brokers_frameworks: "Federal Register API (SEC rulemaking documents); FCA Handbook Notices; SEBI Circulars; EUR-Lex / ESMA publications; MAS Notices and Guidelines; Python Standard Library"
  version: "2.0.0"
  author: algo-trading-skills-contributors
---

## When to Use

Use this when a firm subscribes to one or more regulatory feeds and needs the
answer to a recurring question: **which published rule changes still require
work from us, and how long is left?** The engine is the deterministic assessment
stage behind whatever ingestion adapter you run — it filters a batch to the
authorities and subject areas the firm is exposed to, resolves each update's
binding deadline, flags open items that are urgent or already past due, and
emits an auditable record of what it kept, what it filtered, and why.

The failure this exists to prevent is not "we never heard about the rule." It is
**"we heard about it, alerted on the wrong date, and went quiet during the window
when the work had to happen."**

### The date that matters is usually not the date in the feed

The SEC's T+1 rulemaking is the canonical case, and every field below is
verifiable in the Federal Register API today:

| Field | Value | Where it lives |
|---|---|---|
| `publication_date` | 2023-03-06 | structured feed |
| `effective_on` | **2023-05-05** | structured feed |
| `dates` (free text) | "Effective date: May 5, 2023." | structured feed |
| **compliance date** | **2024-05-28** | only in the release body / press release 2023-29 |

An engine keyed on `effective_on` would have raised a 30-day alarm in **April
2023** and then been silent through the entire 15-month window in which
broker-dealers actually had to re-plumb settlement. That is why
`RegulatoryUpdate` carries `compliance_date` separately, why it is the deadline
when present, and why every assessment records which date it used
(`deadline_basis`). The EU has the same split under different names: MiFID II
entered into force 2014-07-02 and *applied* from 2018-01-03 — a date that was
itself moved by a later amending directive.

## When NOT to Use

- **As the ingestion layer.** This does not fetch, poll, authenticate, or parse
  RSS/HTML/PDF. It assesses records that an adapter has already normalised.
- **As the classifier.** `severity`, `action_required` and `impacted_subdomains`
  come from your analyst triage, vendor taxonomy, or model. The engine validates
  their shape and trusts their content — garbage severity in, confident
  mis-triage out.
- **As legal interpretation, or as evidence of compliance.** A `COMPLIANT`
  status means somebody set `remediation_complete`. Nothing here verifies that
  the work was done, and nothing here decides whether a rule applies to your
  entity. That is a lawyer's job.
- **For business-day or intraday deadlines.** Comparisons are naive calendar
  dates. No holiday calendar, no jurisdiction-local timezone. Deadlines that
  turn on trading days (an exchange notice effective "T+3 sessions") need
  `global-exchange-holiday-calendar-handling`, not this.
- **For phased rulemakings, as one record.** A rule with three staged compliance
  dates must be ingested as three updates. There is no phase model, and folding
  three deadlines into one field loses the two that have not passed yet.
- **As a substitute for the supervisory procedures themselves.** Monitoring
  supports FINRA Rule 3110 / RTS 6 Art. 9 obligations; it does not discharge them.

## Prerequisites

- An ingestion adapter producing `RegulatoryUpdate` records with an ISO
  `YYYY-MM-DD` `effective_date`, and `compliance_date` populated wherever the
  source sets a distinct one. **Leaving `compliance_date` empty because the
  structured feed has no field for it is the single most consequential input
  error in this skill.**
- `monitored_regulators`: the authorities the firm is actually exposed to. Pass
  `None` for the five-authority default; an empty list raises rather than
  silently monitoring everything.
- Optionally `monitored_subdomains` for noise suppression, and
  `urgent_action_window_days` if 30 days is not your escalation window.
- Stable `update_id`s from the source (Federal Register document number, FCA
  Handbook Notice number, SEBI circular reference). Duplicates within one batch
  raise.
- Named owners for the two routing outcomes: immediate action, and overdue.

## Workflow

1. **Filter to the monitored surface — and record what you dropped**:
   - Regulator match is case-insensitive after stripping. Non-matching updates
     are counted in `filtered_regulator_count` and named in
     `filtered_regulators`.
   - **Decision point — an update with no `impacted_subdomains` is retained**
     even when a subdomain filter is active. Fail-open: an unclassified rule
     change disappearing into a noise filter is the exact failure the filter is
     supposed to prevent. It is logged at WARNING.

2. **Resolve the binding deadline**:
   - `compliance_date` when present, `effective_date` otherwise;
     `deadline_basis` records which. `effective_date` is validated either way,
     so a broken record cannot hide behind the field that happens to be unused.
   - **Decision point — a malformed date raises, it is not defaulted.** The
     previous version substituted 30 days on any parse failure, which fabricated
     a deadline, wrote it into the audit record, and made a MEDIUM item look
     routine. A date the feed got wrong is a feed defect to fix, not a value to
     guess at.

3. **Classify severity and status**:
   - Severity is upper-cased and validated against `CRITICAL/HIGH/MEDIUM/LOW`.
     **Decision point — an unrecognised label raises rather than defaulting
     low**, and case is normalised: a feed emitting `'critical'` against a
     case-sensitive comparison silently leaves the urgent band *and*
     `critical_count`.
   - Status is `MONITORING` (informational), `COMPLIANT` (remediation signed
     off), `OVERDUE` (open past its deadline), or `ACTION_REQUIRED`.

4. **Escalate**:
   - An open CRITICAL/HIGH item at or inside `urgent_action_window_days`
     escalates. **Decision point — an open item past its deadline escalates
     regardless of severity**, because a missed deadline is a live breach, not a
     forecast. A `LOW` rule that took effect 107 days ago and was never
     implemented is the worst item in the report, not the least urgent.
   - `remediation_complete` removes an item from the open counts. It never makes
     it invisible — it stays in `assessments` as `COMPLIANT`.

5. **Report and route**:
   - Assessments are sorted nearest-deadline-first with `update_id` as
     tie-break, so two runs over the same batch produce byte-identical reports.
   - `audit_notes` carries the assessment date, every count, and both filter
     counts. Persist the whole report — it is the evidence that monitoring ran.

> Full procedure: see `references/workflows.md`.
> Standards reference: see `references/standards.md`.
> Printable pre-flight checklist: see `assets/checklist.md`.

## Common Pitfalls

- **Alerting on the effective date when a compliance date exists**: the 30-day
  warning fires 15 months early and nothing fires when the work is actually due.
  The structured Federal Register record for T+1 contains only `2023-05-05`; the
  date that mattered, `2024-05-28`, appears solely in the release body. Any
  adapter that maps `effective_on → effective_date` and stops there has this bug.
- **Assuming a deadline is fixed**: MiFID II's application date moved a year by
  amending Directive (EU) 2016/1034. Re-ingest revised records and *replace*
  them — the engine raises on a duplicate `update_id` in one batch precisely so
  that a re-delivered revision cannot be counted as a second obligation.
- **Silently swallowing a parse failure**: substituting a placeholder deadline
  puts a fabricated date into an audit trail a regulator may later read.
- **Case-sensitive severity matching**: `'critical'` from one feed and
  `'CRITICAL'` from another are the same obligation. One of them quietly
  bypassing escalation is a monitoring system that reports success.
- **Treating an empty filter as "no filter"**: `monitored_regulators=[]` used to
  fall through to the five defaults, so a caller narrowing scope got the
  opposite of what they asked for. It now raises.
- **Filtering without counting**: a drop you cannot see is indistinguishable
  from a quiet feed. Every filtered update is counted; unmonitored authorities
  are named.
- **Treating overdue as just another deadline**: `days_until_effective = -107`
  compared against a "within 30 days" window is arithmetically urgent by
  accident. Make it a distinct status so it can be routed to a distinct owner.
- **Running with the default assessment date in production**: the previous
  version defaulted to a hard-coded `2024-01-01`, so a caller who forgot the
  argument got confidently wrong day counts. It now defaults to today (UTC) and
  logs that it did — but pass the date explicitly for reproducible audits.
- **Treating this skill's 30-day window or 24-hour poll cadence as a rule**:
  both are house defaults. No regulator publishes either. See
  `references/standards.md` before repeating them in a policy document.
- **Ignoring implementation lead time**: the compliance date is when the work
  must be *finished*, including testing and change approval — not when it starts.
- **Single-jurisdiction focus**: monitoring the home regulator while a venue in
  another jurisdiction changes tick size or short-selling rules under the
  strategy.

## Verification

- Instantiate `RegulatoryChangeMonitoringServiceIntegrationEngine(monitored_regulators=["SEC","FCA","SEBI"])`.
  Ingest the SEC T+1 update with `effective_date="2023-05-05"` and
  `compliance_date="2024-05-28"` at `current_date_iso="2023-04-05"`: confirm
  `days_until_effective == 419`, `deadline_basis == "COMPLIANCE_DATE"`, and
  `requires_immediate_action is False`. Re-run at `2024-05-01`: confirm `27` days
  and `requires_immediate_action is True`. The same record assessed on
  `effective_date` alone is urgent in April 2023 and silent in May 2024 — that
  inversion is the regression this skill exists to prevent.
- Boundary: an effective date exactly `urgent_action_window_days` out escalates;
  one day beyond does not (30 vs 31).
- Overdue: a `LOW`, action-required update dated `2024-01-15` assessed at
  `2024-05-01` reports `days_until_effective == -107`, `status == "OVERDUE"`, and
  `requires_immediate_action is True`.
- Closure: the same update with `remediation_complete=True` reports
  `COMPLIANT`, drops out of `action_required_count` and `overdue_count`, and the
  batch falls back to `MONITORING_ONLY`.
- Filtering: an unmonitored authority yields `NO_UPDATES` with
  `filtered_regulator_count == 1` and the authority named in
  `filtered_regulators`. An update with empty `impacted_subdomains` survives an
  active subdomain filter.
- Negative checks — each must raise `ValueError`: `severity="URGENT"`,
  `effective_date="28-05-2024"`, `effective_date="2024-02-30"`,
  `compliance_date="soon"`, a blank `update_id`, a blank `regulator`, two
  updates sharing an `update_id`, `monitored_regulators=[]`,
  `urgent_action_window_days=-1`.
- Determinism: the same batch in reversed order produces identical
  `assessments` ordering and identical `audit_notes`.
- Run `python -m unittest discover -s skills/regulatory-change-monitoring-service-integration/scripts`
  from the `scripts/` directory and confirm a 100% pass rate (42 tests).

## Related Skills

- `regulatory-custody-requirements-by-jurisdiction`
- `reference-data-change-notification-pipeline`
- `cross-jurisdiction-regulatory-conflict-resolution`
- `annual-compliance-attestation-workflow`
- `broker-api-deprecation-notice-monitoring`
- `global-exchange-holiday-calendar-handling`
