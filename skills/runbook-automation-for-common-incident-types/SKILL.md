---
name: runbook-automation-for-common-incident-types
description: >-
  Production-grade runbook automation engine executing automated remediation playbooks for common algorithmic trading incidents (market data feed disconnects, latency spikes, broker API outages, drawdown limit breaches, order throttles) with dry-run mode and audit logging.
domain: Site Reliability Engineering & Trading Operations
subdomain: Automated Incident Response & Runbooks
tags: ["runbook-automation", "incident-response", "feed-disconnect", "kill-switch", "venue-failover", "trading-sre"]
brokers_frameworks: ["Runbook Automation Engine", "SRE Playbook Framework", "Python Dataclasses"]
version: "1.0.0"
author: algo-trading-skills-contributors
license: Apache-2.0
---

## When to Use

Use this skill when automating incident response playbooks for trading operations and infrastructure incidents. Manual incident response during high-volatility crashes or venue outages is error-prone and too slow (minutes instead of milliseconds). When a market data feed disconnects, a broker API suffers an outage, or a drawdown limit is breached, automated runbooks execute deterministic remediation sequences (e.g. cancel open orders $\to$ trigger kill switch $\to$ failover to secondary broker venue).

## Prerequisites

- Incident alert payload (`incident_id`, `incident_type`, `severity`, `source_service`, `metric_value`, `threshold_value`, `timestamp_iso`).
- Execution mode (`is_dry_run`: True for testing/verification, False for live execution).

## Workflow

1. **Incident Alert Ingestion**:
   - Ingest alert payload and classify `IncidentType` (`FEED_DISCONNECT`, `LATENCY_SPIKE`, `BROKER_API_OUTAGE`, `DRAWDOWN_BREACH`, `ORDER_THROTTLE`).
2. **Remediation Playbook Lookup**:
   - Retrieve step-by-step remediation action sequence (e.g. `DRAWDOWN_BREACH` $\implies$ `CANCEL_OPEN_ORDERS` $\to$ `TRIGGER_KILL_SWITCH`).
3. **Automated Step Execution**:
   - Execute remediation actions sequentially (or simulate in dry-run mode) and measure execution latency.
4. **Audit History & Report Output**: Output structured `IncidentRunbookReport`.

> Full procedure: see `references/workflows.md`.
> Standards reference: see `references/standards.md`.
> Printable pre-flight checklist: see `assets/checklist.md`.

## Common Pitfalls

- **Unvalidated Dry-Run Mode**: Testing runbook automation directly in production without a dry-run flag, triggering accidental order cancellations.
- **Unbounded Retries During Outages**: Retrying API calls endlessly when a broker endpoint is completely dead, delaying venue failover.
- **Missing Audit Logging**: Executing automated remediation without logging step-by-step actions for post-mortem analysis.

## Verification

- Instantiate `RunbookIncidentAutomationEngine`. Execute drawdown breach runbook $\implies$ verify `CANCEL_OPEN_ORDERS` and `TRIGGER_KILL_SWITCH` executed in sequence. Test dry-run mode $\implies$ verify `SKIPPED_DRY_RUN` status. Retrieve audit history $\implies$ verify complete incident record.
- Run `python scripts/test_runbook_incident_automator.py`.

## Related Skills

- `execution-algorithm-kill-switch-integration`
- `smart-order-router-failover-on-venue-outage`
---
