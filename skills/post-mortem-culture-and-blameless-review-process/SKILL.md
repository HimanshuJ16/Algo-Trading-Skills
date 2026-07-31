---
name: post-mortem-culture-and-blameless-review-process
description: >-
  Site Reliability Engineering (SRE) blameless post-mortem review engine enforcing psychological safety, systemic root cause reframing, and language scanning to eliminate personal blame.
domain: Risk Governance & Incident Response
subdomain: SRE Reliability Culture & Incident Governance
tags: ["blameless-postmortem", "sre", "psychological-safety", "incident-review", "risk-governance", "systemic-factors", "capa"]
brokers_frameworks: ["Google SRE Blameless Framework", "Etsy Post-Mortem Standards", "Python Dataclasses"]
version: "1.0.0"
author: algo-trading-skills-contributors
license: Apache-2.0
---

## When to Use

Use this skill following trading production outages, algorithm glitches, or execution venue disconnections. Blameless post-mortems operate on the foundational Site Reliability Engineering (SRE) principle that engineers act with good intentions based on information available at the time. Focusing on personal fault causes secrecy and hides systemic vulnerabilities. This engine scans post-mortem narratives for accusatory language (e.g. "Trader X forgot...", "Developer Y was careless..."), reframes failures around systemic factors (e.g. staging validation gaps, missing alerts), and enforces actionable CAPA items.

## Prerequisites

- Post-mortem input data (`incident_id`, `incident_date`, `summary`, `systemic_factors`, `narrative`, `proposed_actions`).
- Config options (`strict_blame_check`: default True).

## Workflow

1. **Accusatory Language Scan**:
   - Audit narrative text for blame keywords (`forgot`, `careless`, `human error`, `blame`, `negligent`, `stupid`, `fault`).
2. **Systemic Reframing & Psychological Safety Audit**:
   - Verify that failure mechanisms are attributed to tool, process, or architectural gaps rather than individual humans.
3. **Blameless Post-Mortem Document Assembly**:
   - Generate formatted Markdown post-mortem document.
4. **Audit Report Output**: Output structured `BlamelessPostmortemReport`.

> Full procedure: see `references/workflows.md`.
> Standards reference: see `references/standards.md`.
> Printable pre-flight checklist: see `assets/checklist.md`.

## Common Pitfalls

- **Stopping at "Human Error"**: Treating human mistake as the root cause rather than asking why system safeguards allowed the action to reach production.
- **Punitive Language**: Using finger-pointing terminology in official post-mortem documents, damaging psychological safety.
- **Confusing Blamelessness with Lack of Accountability**: Eliminating individual shaming while failing to hold engineering teams accountable for implementing systemic fixes.

## Verification

- Instantiate `BlamelessPostmortemGenerator`. Ingest narrative with accusatory text "Developer forgot to update config" $\implies$ verify `BLAME_LANGUAGE_DETECTED` status, offending keywords flagged, and reframing guidance provided. Ingest systemic narrative $\implies$ verify `BLAMELESS_POSTMORTEM_APPROVED` status.
- Run `python scripts/test_blameless_postmortem_generator.py`.

## Related Skills

- `post-breach-root-cause-analysis-template`
- `on-call-rotation-and-escalation-for-trading-systems`
---
