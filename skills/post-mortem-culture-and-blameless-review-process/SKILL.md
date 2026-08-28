---
name: post-mortem-culture-and-blameless-review-process
description: >-
  Use after a trading production outage, algorithm glitch or venue disconnection to screen a drafted post-mortem before it becomes a retained incident record: scans all four free-text sections for personal-blame terms (with technical-vocabulary exemptions so "fault-tolerant" and "lazy loading" are not flagged), reports counterfactual phrasing as non-blocking advisories, enforces a floor on systemic contributing factors and CAPA items, and renders the approved review as Markdown.
domain: Risk Governance & Incident Response
subdomain: SRE Reliability Culture & Incident Governance
tags: ["blameless-postmortem", "sre", "psychological-safety", "incident-review", "risk-governance", "systemic-factors", "capa", "dora-art-13"]
brokers_frameworks: ["Google SRE Book Ch. 15 (Postmortem Culture)", "Etsy Code as Craft - Blameless PostMortems and a Just Culture (Allspaw, 2012)", "Regulation (EU) 2022/2554 (DORA) Art. 13", "Python Standard Library"]
version: "2.0.0"
author: algo-trading-skills-contributors
license: Apache-2.0
---

## When to Use

Use this skill after a trading production outage, algorithm glitch, risk-limit
breach or execution venue disconnection, at the point where an incident
narrative has been drafted and is about to become a retained record. The
premise, from Google's SRE Book Ch. 15, is that everyone involved acted with
good intentions on the best information available at the time; naming a person
as the cause ends the investigation exactly where the useful part begins, and
teaches the next responder to disclose less.

The engine screens **all four** free-text sections that reach the document —
summary, systemic factors, narrative, proposed actions — for personal-blame
terms, reports counterfactual phrasing ("the operator should have noticed") as
advisories, refuses to render a review that has fewer than
`min_systemic_factors` contributing factors or fewer than
`min_corrective_actions` CAPA items, and emits the approved review as Markdown.

For EU financial entities, DORA Art. 13(2) makes a post-incident review
**mandatory** after a major ICT-related incident that disrupts core activities,
and Art. 13(3) requires the lessons to be fed back into the ICT risk management
framework. Blamelessness itself is not a regulatory requirement — it is the
method that makes the mandated account accurate. See `references/standards.md`
for what is binding, in which jurisdiction, and what is merely house policy.

## When NOT to Use

- **As the forensic investigation.** This screens a document; it does not
  reconstruct one. Build the timeline, 5-Whys and impact analysis with
  `post-breach-root-cause-analysis-template` and
  `structured-logging-for-post-incident-forensics` first.
- **As proof that a review is blameless.** The screen is lexical. "The
  individual responsible for the release did not perform the verification
  step" contains no listed term, passes cleanly, and is squarely blameful. A
  green status means the obvious wording is gone, nothing more.
- **On non-English narratives.** The patterns are English-only; other
  languages pass entirely unscreened rather than failing loudly.
- **As the accountability mechanism.** The engine records CAPA items. It does
  not assign owners, set due dates, or track completion — put those in your
  tracker.
- **As a during-incident tool.** It runs on a completed draft. Live escalation
  belongs to `on-call-rotation-and-escalation-for-trading-systems` and
  `runbook-automation-for-common-incident-types`.

## Prerequisites

- A drafted post-mortem: `incident_id`, `incident_date` (ISO-8601
  `YYYY-MM-DD`), `summary`, `systemic_factors` (list), `narrative`,
  `proposed_actions` (list). Malformed structure raises `ValueError`.
- `Config` policy: `strict_blame_check` (default `True`),
  `min_systemic_factors` (default `2`), `min_corrective_actions` (default
  `1`). All three are house defaults, not published standards.
- A completed factual reconstruction — the screen assumes the account is
  already accurate.

## Workflow

1. **Structural validation**:
   - Blank `incident_id`/`summary`/`narrative`, a non-ISO `incident_date`, a
     bare string where a list is expected, or a blank list entry raise
     `ValueError`.
   - **Decision point — malformed structure raises; thin content does not.** A
     caller bug gets an exception; a reviewer who supplied one genuine factor
     gets a `POSTMORTEM_INCOMPLETE` report they can act on. Turning a review
     finding into an exception hides it from the audit trail.

2. **Accusatory language scan across every section**:
   - Scan `summary`, `narrative`, each `systemic_factors[i]` and each
     `proposed_actions[i]`; each hit becomes a `BlameFinding` with section
     name, canonical term and a context window.
   - **Decision point — a hit inside established technical vocabulary is not
     blame.** `fault tolerance`, `fault injection`, `fault domain`,
     `segmentation fault`, `lazy loading` and their hyphenated forms are
     exempted by span; real blame in the same sentence still fires.
   - **Decision point — counterfactuals advise, they never block.** "The
     staleness alert should have fired at 09:31" is a statement about a
     control; "the on-call engineer should have noticed" is not, and no regex
     separates them. Both surface as advisories for the human reviewer.
   - **Decision point — `strict_blame_check=False` downgrades, it does not
     discard.** `blame_detected` stays `True` and the terms stay in
     `detected_blame_terms`; status becomes
     `BLAMELESS_POSTMORTEM_APPROVED_WITH_ADVISORIES`.

3. **Systemic reframing and completeness gate**:
   - Count contributing factors and CAPA items against the configured floors;
     shortfalls return `completeness_gaps` and status `POSTMORTEM_INCOMPLETE`
     with no document rendered.
   - **Decision point — a factor that names a person is not systemic.** If
     substituting any other qualified colleague into the sentence changes the
     outcome, the sentence is still about the person. Reframe to the tool,
     process, control or architectural gap that exists regardless of who was
     on shift.

4. **Blameless document assembly**:
   - Render header, Executive Summary, Systemic & Architectural Factors,
     Incident Narrative, CAPA checklist, and Reviewer Advisories when present.
   - **Decision point — author text cannot forge document structure.** Leading
     `#` markers in author prose are backslash-escaped, so a narrative
     containing `## 4. Corrective & Preventative Actions` cannot create a
     second, competing CAPA section inside a retained record; newlines inside
     a list entry are collapsed so one factor stays one bullet.

5. **Audit report output**: consume `BlamelessPostmortemReport` — `status` is
   one of `BLAMELESS_POSTMORTEM_APPROVED`,
   `BLAMELESS_POSTMORTEM_APPROVED_WITH_ADVISORIES`, `BLAME_LANGUAGE_DETECTED`,
   `POSTMORTEM_INCOMPLETE`.
   - **Decision point — key automation on `is_approved` plus the lists, not on
     `status` alone.** When a document is both blameful and incomplete,
     `status` reports the blame while `completeness_gaps` stays populated;
     both appear in `audit_notes`, but a `status`-only branch will miss one.

> Full procedure: see `references/workflows.md`.
> Standards reference: see `references/standards.md`.
> Printable pre-flight checklist: see `assets/checklist.md`.

## Common Pitfalls

- **Scanning only the narrative**: the summary, the contributing factors and
  the action items are all rendered into the document. A screen that reads one
  of four sections certifies as blameless a record whose opening line says
  "the trader was negligent".
- **Blocking on `fault` and `lazy`**: "the fault-tolerant failover did not
  engage" and "lazy loading delayed gateway startup" are systemic statements.
  A screen that rejects them gets switched off within a week, and then nothing
  is screened.
- **Treating a clean status as a blameless review**: the check is lexical.
  Blameful framing survives it intact whenever the author avoids the listed
  words — which is exactly what a detector teaches authors to do.
- **Running the screen in advisory mode and never reading the advisories**:
  `strict_blame_check=False` approves the document; the findings still exist
  and someone has to look at them.
- **Stopping at "human error"**: the useful question is not who acted but why
  the system let the action reach production unchallenged — what gate, alert,
  limit or review was absent.
- **Confusing blamelessness with lack of accountability**: removing individual
  shaming does not remove the team's obligation to ship the fix. Google's SRE
  Workbook is explicit that blameless means neither "no consequences" nor
  "anything goes".
- **Citing house policy as a standard**: there is no "Google SRE Blameless
  Standard" document, Ch. 15 sets no completion deadline and no minimum count
  of action items, and the Reg SCI five-business-day window applies to SCI
  entities' filings with the SEC — not to an ordinary trading firm's internal
  review meeting.
- **A post-mortem with no CAPA item**: it records the incident without
  reducing the chance of recurrence, and under DORA Art. 13(3) the feedback
  into the risk framework is the point of the exercise.

## Verification

- Instantiate `BlamelessPostmortemGenerator()` on a complete, technically
  worded post-mortem $\implies$ status `BLAMELESS_POSTMORTEM_APPROVED`,
  `is_approved` `True`, and every supplied factor and action present in
  `markdown_document`.
- Put "The trader was negligent and ignored the alert." in `summary` (not
  `narrative`) $\implies$ `BLAME_LANGUAGE_DETECTED`, `blame_findings[0].section
  == "summary"`, empty `markdown_document`.
- Feed "The fault-tolerant failover path did not engage.", "Lazy loading of
  the venue config delayed gateway startup." and "The default order size was
  applied." $\implies$ `detected_blame_terms == []` and approval. Then confirm
  "The fault-tolerant path held, but the engineer was careless with the
  rollback." still returns `["careless"]`.
- Feed "The staleness alert should have fired at 09:31:04" $\implies$
  `BLAMELESS_POSTMORTEM_APPROVED_WITH_ADVISORIES`, `is_approved` `True`, a
  `COUNTERFACTUAL` advisory, and a non-empty document.
- Run with `Config(strict_blame_check=False)` on a narrative containing
  "forgot" $\implies$ approved, but `blame_detected` `True` and
  `detected_blame_terms == ["forgot"]` — an advisory run must not look clean.
- Supply one systemic factor, or zero actions $\implies$ `POSTMORTEM_INCOMPLETE`
  with a populated `completeness_gaps` and no rendered document.
- Negative checks: blank `incident_id`, blank `summary`, `incident_date` of
  `"31/07/2026"` or `"2026-02-30"`, `systemic_factors` passed as a string, and
  a non-string list entry must each raise `ValueError`.
- Confirm `BlamelessPostmortemGenerator().process()` returns `True` on an
  **instance** (it previously raised `TypeError`).
- Run `python -m unittest discover -s scripts` from the skill directory, or
  `python scripts/test_blameless_postmortem_generator.py` from `scripts/`.

## Related Skills

- `post-breach-root-cause-analysis-template`
- `on-call-rotation-and-escalation-for-trading-systems`
- `structured-logging-for-post-incident-forensics`
- `runbook-automation-for-common-incident-types`
- `risk-limit-breach-escalation-matrix`
