---
name: post-breach-root-cause-analysis-template
description: >-
  Standardized post-incident Root Cause Analysis (RCA) generator incorporating 5-Whys analysis, chronological event timelines, financial impact audits, and CAPA action items.
domain: Risk Governance & Incident Response
subdomain: Incident Post-Mortem & Regulatory Compliance
tags: ["rca", "root-cause-analysis", "incident-response", "5-whys", "capa", "post-mortem", "risk-governance"]
brokers_frameworks: ["FINRA / SEC Post-Mortem Standards", "Python Dataclasses"]
version: "1.0.0"
author: algo-trading-skills-contributors
license: Apache-2.0
---

## When to Use

Use this skill following a trading system risk limit breach, runaway algorithm incident, or severe drawdown event. Regulatory frameworks (SEC Rule 15c3-5, FINRA Rule 4511, FCA SYSC) and institutional risk committees require a formal, blameless Root Cause Analysis (RCA) post-mortem. This engine automates the structure, validation, and generation of RCA documents containing 5-Whys analysis, chronological incident timelines, financial P&L impact audits, and Corrective and Preventive Actions (CAPA).

## Prerequisites

- Breach incident metadata (`incident_id`, `incident_date`, `strategy_id`, `breach_type`, `severity`).
- Financial impact data (`financial_loss_usd`, `unauthorized_turnover_usd`).
- Incident timeline events (`List[Tuple[timestamp_str, event_description]]`).
- 5-Whys analysis steps (`List[str]`) and CAPA action items (`List[str]`).

## Workflow

1. **Incident Data Ingestion & Validation**:
   - Validate 5-Whys depth ($\ge 3$ levels required) and CAPA action item completeness.
2. **Chronological Timeline & P&L Audit Assembly**:
   - Order events by timestamp and format financial loss summaries.
3. **Markdown & JSON RCA Generation**:
   - Render standardized markdown post-mortem document and machine-readable JSON report payload.
4. **Audit Report Output**: Output structured `RCAReport`.

> Full procedure: see `references/workflows.md`.
> Standards reference: see `references/standards.md`.
> Printable pre-flight checklist: see `assets/checklist.md`.

## Common Pitfalls

- **Superficial "Human Error" Diagnosis**: Stopping at "Engineer deployed bad parameter" without drilling down to why automated staging gates failed.
- **Missing Timestamp Synchronization**: Recording un-synchronized logs from multiple nodes, obscuring the true sequence of events.
- **Unassigned CAPA Items**: Defining action items without explicit engineering owners or due dates.

## Verification

- Instantiate `BreachRcaGenerator`. Ingest incident `INC-2026-001` with 5-Whys analysis ($5$ levels) and 2 action items $\implies$ verify `RCA_GENERATED_SUCCESS` status and markdown document output.
- Run `python scripts/test_breach_rca_generator.py`.

## Related Skills

- `position-limit-breach-simulation-fire-drills`
- `log-aggregation-and-centralized-observability`
---
