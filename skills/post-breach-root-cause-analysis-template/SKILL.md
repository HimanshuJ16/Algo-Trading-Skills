---
name: post-breach-root-cause-analysis-template
description: >-
  Use when writing the post-mortem after a trading-system risk-limit breach,
  runaway algorithm, or severe unexpected drawdown: builds a structured Root
  Cause Analysis record with a UTC-normalised chronology, a 5-Whys drill-down,
  a quantified financial-impact section, and CAPA items that must carry a named
  owner and a due date, then renders it as Markdown and as a deterministic JSON
  payload.
domain: Risk Governance & Incident Response
subdomain: Incident Post-Mortem & Regulatory Compliance
tags:
- rca
- root-cause-analysis
- incident-response
- 5-whys
- capa
- post-mortem
- risk-governance
brokers_frameworks:
- Python Standard Library
version: "2.0.0"
author: algo-trading-skills-contributors
license: Apache-2.0
---

## When to Use

Use this after a risk-limit breach, a runaway algorithm, an unexpected kill-switch
activation, or a severe drawdown, when someone has to produce the post-mortem record.
The engine does not work out what went wrong. It enforces that the record of what went
wrong is complete enough to be worth keeping, and renders it twice: as a Markdown
document a human reviews, and as a deterministic JSON payload an incident database
ingests.

Concretely, it will refuse to certify an RCA that stops at two "whys", that lists action
items nobody owns, that has no chronology, or in which nobody has explicitly answered the
question *"might this have been a rule violation?"* - the determination that, in several
jurisdictions, starts a reporting clock.

The completeness thresholds it applies are **house standards, not regulatory minimums**.
No regulator publishes a mandatory 5-Whys depth or an RCA page count. `references/standards.md`
sets out what regulators actually require, of whom, and by when.

## When NOT to Use

- **As a substitute for the incident-reporting decision.** This module produces a record.
  Whether that record triggers an obligation (FINRA Rule 4530(b), Regulation SCI Form SCI,
  a DORA major-incident report) is a legal determination made by Compliance, not an output
  of this tool. `possible_rule_violation` records the author's assessment; it does not make it.
- **As a truth check.** Every gate is structural. A five-level chain of confident fiction
  passes cleanly. The gates measure whether the RCA was *written*, never whether it is *right*.
- **As the financial impact calculation.** `financial_loss_usd` and
  `unauthorized_turnover_usd` are caller-supplied and must be reconciled against the books
  by whoever owns the P&L. The engine only validates that they are finite, non-negative
  magnitudes.
- **During the incident.** This is the post-containment artefact. Live containment belongs
  to `kill-switch-and-drawdown-circuit-breakers` and `risk-limit-breach-escalation-matrix`;
  the forensic evidence trail belongs to `structured-logging-for-post-incident-forensics`
  and, for key compromise specifically, `post-incident-forensics-for-suspected-key-compromise`.
- **As the review process itself.** Generating a document is not conducting a blameless
  review. See `post-mortem-culture-and-blameless-review-process` for the human process this
  artefact is the output of.

## Prerequisites

- **Incident metadata**: `incident_id`, `strategy_id`, `breach_type` (your own taxonomy -
  no standard one exists, so it is validated as non-blank, not against a fixed list),
  and a `Severity` enum member.
- **Two timezone-aware timestamps**: `detected_at` (when responsible personnel first had a
  reasonable basis to conclude the breach had occurred) and `contained_at` (kill switch
  engaged, positions flattened, algorithm disabled). `contained_at` may equal but not
  precede `detected_at`.
- **Financial impact as non-negative magnitudes**: a 25,000 USD loss is `25000.0`, never
  `-25000.0`. Negative and non-finite values raise.
- **A `List[str]` of 5-Whys**, root cause last. Blank entries raise rather than padding the
  depth count, and a bare string is rejected rather than iterated by character.
- **A `List[TimelineEvent]`**, each with a timezone-aware timestamp, a description, and the
  `source` clock it came from. Input order is irrelevant; the engine sorts.
- **A `List[CapaItem]`**, each ideally with `owner`, `due_date`, and a `CapaType`
  (`CORRECTIVE` fixes this instance; `PREVENTIVE` stops the class of failure recurring).
- **An explicit `possible_rule_violation`** - `True` or `False`. Leaving it `None` is itself
  a finding.
- **A caller-supplied `generated_at`**. The module reads no wall clock, so the same input
  always renders byte-identical output.

## Workflow

1. **Ingest and structurally validate the incident record.**
   - `BreachIncidentSpec.__post_init__` raises `ValueError` on anything malformed: blank
     identifiers or descriptions, negative or non-finite money, a severity outside the enum,
     containment before detection, or an untyped timeline/CAPA entry.
   - **Decision point - naive datetimes are rejected, never assumed to be UTC.** Assuming is
     precisely how a post-mortem ends up with a chronology ordered by clock offset instead of
     by causality. Every datetime must carry a tzinfo and is normalised to UTC on entry.
   - **Decision point - a bare string is not a sequence.** `five_whys="Human error"` iterates
     by character and would report an eleven-level analysis of single letters that clears the
     depth gate. Strings and bytes are rejected where a sequence is expected.
   - **Decision point - a malformed record is a caller bug, not an audit finding.** Structural
     defects raise. Completeness defects (step 2) do not.

2. **Apply the completeness gates - all of them, not the first one that fails.**
   - `INSUFFICIENT_5_WHYS_DEPTH`, `MISSING_ACTION_ITEMS`, `MISSING_TIMELINE`,
     `CAPA_MISSING_OWNER_OR_DUE_DATE`, `RULE_VIOLATION_ASSESSMENT_MISSING`, `RCA_PAST_DUE`.
   - **Decision point - `status` is the highest-precedence finding, `validation_findings`
     is all of them.** A caller that switches on `status` alone sees the most serious problem
     but must not infer that nothing else is wrong. Read the list.
   - **Decision point - advisory findings never invalidate the RCA.**
     `TERMINAL_BLAME_ATTRIBUTION` (see Common Pitfalls) is recorded and rendered, but
     `is_valid_rca` stays `True`. A heuristic string match must not be able to block a
     post-mortem.
   - **Decision point - `possible_rule_violation=None` blocks.** An RCA that has not asked
     whether a rule was broken is incomplete, and in some jurisdictions the answer starts a
     clock measured in days. Forcing an explicit `True`/`False` is the point; defaulting it
     to `False` would quietly answer the question on the author's behalf.

3. **Assemble the chronology in UTC.**
   - Sort by normalised UTC timestamp. The sort is **stable**, so events sharing a timestamp
     keep the caller's order - the only ordering information available for events a clock
     cannot separate.
   - Each entry renders its `source` clock, so a reader can see which hosts the sequence
     depends on before trusting the causal story built on it.

4. **Render both artefacts - including when the RCA is incomplete.**
   - The Markdown document and JSON payload are always produced. An incomplete post-mortem
     still needs to be readable, and its gaps appear in section 6 of the document marked
     `BLOCKING` or `ADVISORY`.
   - Free-text fields are whitespace-collapsed to single lines so an embedded newline cannot
     split one bullet into two and invent a timeline entry that never happened.

5. **Route the output.**
   - `is_valid_rca is False` sends it back to the author with `validation_findings`.
   - `possible_rule_violation is True` sends it to Compliance, before the RCA is finalised.
     See `references/standards.md` for the deadlines that may attach.
   - Either way, retain the record. An RCA is a business record and falls under the firm's
     retention schedule - see Common Pitfalls.

> Full procedure: see `references/workflows.md`.
> Standards reference: see `references/standards.md`.
> Printable pre-flight checklist: see `assets/checklist.md`.

## Common Pitfalls

- **Stopping the drill-down at a person.** "The engineer toggled the flag off" is where the
  analysis gets comfortable, not where it is finished; the next why is why a single toggle
  could reach production unchallenged. The engine flags a final "why" matching a short list
  of blame phrases as `TERMINAL_BLAME_ATTRIBUTION` - a crude, advisory, case-insensitive
  substring match that will both miss real cases and occasionally fire on a legitimate step.
  It marks a line for human review; it does not adjudicate one.
- **Timelines assembled from unsynchronised clocks.** Events from an OMS host, a broker log,
  and an exchange drop copy sorted together on wall-clock strings produce a sequence ordered
  by clock offset. The causal story built on that sequence can be exactly backwards. This is
  why timestamps must be timezone-aware and why every event records its `source`. Clock
  discipline itself belongs to `clock-synchronization-ptp-for-trading-hosts`.
- **CAPA items with no owner and no date.** "Improve deployment validation" assigned to
  nobody is a sentence, not an action. `CAPA_MISSING_OWNER_OR_DUE_DATE` blocks; the Markdown
  renders `**UNASSIGNED**` and `**NO DUE DATE**` in bold so it cannot be skimmed past.
- **All-corrective CAPA.** Restoring the flag fixes this incident and prevents nothing.
  `has_preventive_action` is `False` when no item is typed `PREVENTIVE` - check it.
- **Forgetting the RCA is a discoverable record.** For a FINRA member, books and records with
  no other specified period are preserved for at least six years under FINRA Rule 4511(b), in
  a format complying with SEA Rule 17a-4. Write the post-mortem in the knowledge that an
  examiner may read it years later; see `record-retention-periods-by-jurisdiction`.
- **Concluding a violation occurred and treating it as an internal matter.** FINRA Rule
  4530(b) requires a member to report to FINRA "promptly ... but in any event not later than
  30 calendar days, after the member has concluded or reasonably should have concluded" that
  it or an associated person violated applicable laws or rules. The RCA *is* often where that
  conclusion is first written down. Escalate to Compliance rather than deciding in the
  post-mortem meeting.
- **Assuming SEC Rule 15c3-5 requires this post-mortem.** It does not. Rule 15c3-5(e)
  requires a system for *regularly reviewing* the effectiveness of the risk-management
  controls and for "promptly addressing any issues" - a periodic-review obligation, with no
  incident-level RCA, post-mortem, or root-cause requirement anywhere in the rule. Do not
  cite it as the authority for this artefact; see `references/standards.md`.
- **Treating "$-25,000.00" as a loss.** The sign convention is magnitude, enforced: negative
  amounts raise rather than rendering a double negative into the impact section.
- **Reading `status` and stopping.** It reports one finding. `validation_findings` reports
  every one.

## Verification

- Build a complete `BreachIncidentSpec` (`INC-2026-001`, 5 whys, 3 timeline events, 2 CAPA
  items both owned and dated, `possible_rule_violation=False`) and generate with
  `generated_at = 2026-08-03T09:00:00Z`. Confirm `status == "RCA_GENERATED_SUCCESS"`,
  `is_valid_rca is True`, `validation_findings == []`, `containment_seconds == 1.0`, and
  all six Markdown section headings present.
- Supply timeline events out of order, including one stamped `09:00:00-05:00`. Confirm it
  normalises to `2026-07-31T14:00:00+00:00` and sorts *first*, ahead of the `14:05:00Z` event.
- Supply two events sharing a timestamp. Confirm the caller's order survives (stable sort).
- Supply a spec with one why, no CAPA items, no timeline, and `possible_rule_violation=None`.
  Confirm all four findings are returned together, `status` is `INSUFFICIENT_5_WHYS_DEPTH`,
  and a Markdown document is still rendered listing them as `BLOCKING`.
- Supply a CAPA item with an owner but no due date. Confirm `CAPA_MISSING_OWNER_OR_DUE_DATE`,
  `unassigned_action_items == 1`, and `**NO DUE DATE**` in the document.
- End the 5-Whys with "... because of human error". Confirm `TERMINAL_BLAME_ATTRIBUTION` is
  raised, rendered as `(ADVISORY)`, and that `is_valid_rca` remains `True`.
- Boundary checks: exactly `min_five_whys_depth` whys passes; `generated_at == rca_due_by`
  is not past due; `contained_at == detected_at` is allowed.
- Negative checks: a blank why, a negative or `NaN` amount, a naive datetime (in the spec, in
  a `TimelineEvent`, or as `generated_at`), containment before detection, a `str` severity, a
  raw tuple in `timeline_events`, a raw string in `action_items`, and a bare string passed as
  `five_whys` must each raise `ValueError`.
- Confirm determinism: generating twice from equal specs yields identical `markdown_document`
  and `json_payload`.
- Run `python -m unittest discover -s skills/post-breach-root-cause-analysis-template/scripts -v` from the `scripts/` directory -
  52 tests, 100% pass rate.

## Related Skills

- `post-mortem-culture-and-blameless-review-process`
- `risk-limit-breach-escalation-matrix`
- `position-limit-breach-simulation-fire-drills`
- `structured-logging-for-post-incident-forensics`
- `post-incident-forensics-for-suspected-key-compromise`
- `record-retention-periods-by-jurisdiction`
- `clock-synchronization-ptp-for-trading-hosts`
