# Workflows for Post-Mortem Culture and Blameless Review Process

## 0. Before the screen runs

The engine screens a document that already exists. It does not gather facts.
Collect the account first — timeline, alerts that fired and did not fire, the
information each responder actually had at each decision point — then write it
up, then screen it. Screening a document assembled from one person's memory
produces a blameless account of an incomplete story.

For the forensic reconstruction itself use
`post-breach-root-cause-analysis-template` (5-Whys, timelines, financial
impact) and `structured-logging-for-post-incident-forensics`. This skill is the
language-and-completeness gate on top of that output.

## 1. Structural validation

`generate_blameless_postmortem` raises `ValueError` before any screening when
the input is malformed: blank `incident_id`, `summary` or `narrative`; an
`incident_date` that is not an ISO-8601 `YYYY-MM-DD` calendar date; a
`systemic_factors` or `proposed_actions` value that is a bare string rather
than a list; or a blank/non-string entry inside either list.

**Decision point — malformed structure raises, thin content does not.** A
caller passing `systemic_factors="pipeline gap"` has a bug and gets an
exception. A reviewer submitting one genuine factor has an incomplete review
and gets a `POSTMORTEM_INCOMPLETE` report they can act on. Do not collapse the
two: turning a review finding into an exception hides it from the audit trail.

## 2. Accusatory language scan — all four sections

Every free-text section that reaches the rendered document is scanned:
`summary`, `narrative`, each `systemic_factors[i]` and each
`proposed_actions[i]`. Each hit becomes a `BlameFinding` carrying the section
name, the canonical term, and a whitespace-collapsed context window so the
reviewer can locate the phrase without re-reading the document.

**Decision point — a hit inside an established technical phrase is not blame.**
`fault tolerance`, `fault injection`, `fault domain`, `segmentation fault`,
`lazy loading`, `lazy evaluation` and their hyphenated forms are exempted by
span. "The fault-tolerant failover path did not engage" is a systemic
statement, and rejecting it teaches reviewers to turn the screen off. Real
blame in the same sentence still fires: exemption is per-match, not per-field.

**Decision point — counterfactuals are advisory, never blocking.** "The alert
should have fired at 09:31" is a legitimate statement about a control. "The
on-call engineer should have noticed" is not, and no regex separates them.
Counterfactual hits are surfaced to the human reviewer and rendered into the
approved document under *Reviewer Advisories*; they never withhold approval.

**Decision point — `strict_blame_check=False` downgrades, it does not
discard.** In advisory mode `blame_detected` stays `True`, the terms stay in
`detected_blame_terms`, and the hits move into `advisory_findings` with status
`BLAMELESS_POSTMORTEM_APPROVED_WITH_ADVISORIES`. An advisory-mode run must
never look identical to a clean run.

## 3. Systemic reframing and completeness

Contributing factors and corrective actions are counted against
`Config.min_systemic_factors` (default 2) and `Config.min_corrective_actions`
(default 1). Shortfalls are returned as `completeness_gaps` with status
`POSTMORTEM_INCOMPLETE`, and no document is rendered.

Reframing guidance, applied by the human author, not the engine:

| Blameful | Systemic |
|---|---|
| "The developer forgot to update the venue config." | "Deployment applies the strategy binary and the venue config independently; nothing fails the release when the two versions diverge." |
| "The trader was careless with the size field." | "The order entry form accepts a notional two orders of magnitude above the desk limit without a confirmation step." |
| "Human error during the rollout." | "The rollout runbook has 14 manual steps with no automated verification between them." |

Each systemic factor should name a **tool, process, control or architectural
gap that exists independently of who was on shift**. If replacing the person in
the sentence with any other qualified colleague changes the outcome, the
factor is still about the person.

**Decision point — blameless is not consequence-free.** Removing individual
shaming does not remove the team's obligation to ship the fix. Each CAPA item
needs a named owner and a due date in your tracker; this engine records the
item, not the accountability chain.

## 4. Report generation

On approval the engine renders a Markdown document with a fixed section
structure: header (incident id, date, method citation), Executive Summary,
Systemic & Architectural Factors, Incident Narrative, CAPA checklist, and —
when advisories exist — Reviewer Advisories.

**Decision point — author text cannot forge structure.** Leading `#` heading
markers inside `summary` and `narrative` are backslash-escaped, so a narrative
containing `## 4. Corrective & Preventative Actions` renders as literal text
rather than a second, competing CAPA section in a retained record. Embedded
newlines inside a list entry are collapsed so one factor stays one bullet.

## 5. Audit report output

Consume `BlamelessPostmortemReport`:

| Status | Meaning | `is_approved` | Document |
|---|---|---|---|
| `BLAMELESS_POSTMORTEM_APPROVED` | Clean | `True` | rendered |
| `BLAMELESS_POSTMORTEM_APPROVED_WITH_ADVISORIES` | Counterfactuals, or blame hits in advisory mode | `True` | rendered, advisories appended |
| `BLAME_LANGUAGE_DETECTED` | Blocking blame terms in strict mode | `False` | empty |
| `POSTMORTEM_INCOMPLETE` | Below the factor/action floor | `False` | empty |

**Decision point — status precedence loses no information.** When a document
is both blameful and incomplete, `status` reports the blame (the headline
failure) while `completeness_gaps` remains populated and both appear in
`audit_notes`. Key your automation on `is_approved` plus the two lists, not on
`status` alone.

## 6. Feeding the findings back

An approved document is an output, not an outcome. Route the CAPA items into
the tracker, and route recurring systemic factors into the periodic control
review — under DORA Art. 13(3) that feedback into the ICT risk management
framework is itself the obligation, and under MiFID II RTS 6 Art. 9 the annual
self-assessment is where unremediated post-mortem findings surface.
