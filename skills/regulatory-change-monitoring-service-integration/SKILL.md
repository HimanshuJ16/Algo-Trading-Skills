---
name: regulatory-change-monitoring-service-integration
description: >-
  Regulatory change monitoring service integration engine tracking regulatory feed updates (SEC, FCA, SEBI, ESMA, MAS), classifying impact severity, tracking implementation deadlines, and routing action alerts to compliance teams.
domain: Regulatory & Financial Compliance
subdomain: Regulatory Intelligence & Change Governance
tags: ["regulatory-monitoring", "compliance-tracking", "regulatory-change", "sec", "fca", "sebi", "esma", "mas"]
brokers_frameworks: ["SEC Regulatory Feeds", "FCA Handbook Updates", "SEBI Circulars", "ESMA Technical Standards", "Python Dataclasses"]
version: "1.0.0"
author: algo-trading-skills-contributors
license: Apache-2.0
---

## When to Use

Use this skill when managing compliance governance for algorithmic trading systems operating across global financial markets. Financial regulators (SEC, FCA, SEBI, ESMA, MAS) frequently issue new rules, circulars, and technical standards (e.g. T+1 settlement transitions, short-selling bans, tick size regime changes, margin requirement updates). Failing to track and implement these regulatory changes leads to illegal trading activity and heavy fines. This engine ingests regulatory update feeds, filters for relevant authorities, assesses impact severity and deadline urgency, and routes alerts to compliance engineering teams.

## Prerequisites

- Monitored regulators list (`monitored_regulators`: e.g. `['SEC', 'FCA', 'SEBI', 'ESMA', 'MAS']`).
- Regulatory update payload (`update_id`, `regulator`, `title`, `effective_date`, `impacted_subdomains`, `severity`, `action_required`).

## Workflow

1. **Regulatory Feed Filtering**:
   - Filter incoming updates to match monitored regulators list.
2. **Implementation Deadline Calculation**:
   - Calculate days remaining until effective date ($\text{Effective Date} - \text{Current Date}$).
3. **Severity & Urgency Classification**:
   - Classify update as `CRITICAL`, `HIGH`, `MEDIUM`, or `LOW`.
   - Flag as requiring immediate action if severity is `CRITICAL`/`HIGH`, effective within 30 days, and action is required.
4. **Compliance Alerting**:
   - Output structured `RegulatoryChangeReport`.

> Full procedure: see `references/workflows.md`.
> Standards reference: see `references/standards.md`.
> Printable pre-flight checklist: see `assets/checklist.md`.

## Common Pitfalls

- **Ignoring Implementation Lead Time**: Treating effective date as the start of technical work rather than the completion deadline.
- **Unfiltered Noise Ingestion**: Ingesting non-trading regulatory updates (e.g. retail banking rules), creating alert fatigue.
- **Single-Jurisdiction Focus**: Monitoring local regulators while missing international regulatory changes impacting cross-border trading.

## Verification

- Instantiate `RegulatoryChangeMonitoringServiceIntegrationEngine`. Ingest SEC T+1 settlement update effective in 27 days with `CRITICAL` severity $\implies$ verify `ACTION_REQUIRED` status and `requires_immediate_action=True`. Ingest unmonitored regulator update $\implies$ verify filtered out (`NO_UPDATES`).
- Run `python scripts/test_regulatory_change_monitoring_service_integration.py`.

## Related Skills

- `regulatory-custody-requirements-by-jurisdiction`
- `reference-data-change-notification-pipeline`
---
