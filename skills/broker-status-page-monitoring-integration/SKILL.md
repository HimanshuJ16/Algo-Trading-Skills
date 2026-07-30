---
name: broker-status-page-monitoring-integration
description: Use when building automated incident response and alerting systems to
  monitor public broker status pages and status APIs (e.g. Statuspage.io endpoints),
  distinguishing external broker platform outages from internal strategy bugs.
domain: algorithmic-trading
subdomain: broker-integration
tags:
- broker-integration
- status-page
- outage-monitoring
- incident-response
- statuspage-io
- health-checks
brokers_frameworks:
- Statuspage.io API
- Python Status Monitor
version: '1.0'
author: algo-trading-skills-contributors
license: Apache-2.0
---

## When to Use

Invoke this skill when building incident detection, automated alerting, or failover logic for trading bots. When API order submissions time out or WebSockets disconnect, ops teams must immediately diagnose whether the failure stems from internal application bugs or external broker infrastructure outages. Programmatically monitoring broker status APIs provides real-time outage classification and suppresses false-alarm internal bug escalation during confirmed broker outages.

## Prerequisites

- Broker public status page JSON endpoint (e.g., `https://status.alpaca.markets/api/v2/summary.json`).
- Polling interval configuration (e.g., 60 seconds).

## Workflow

1. **Ingest Statuspage.io / Status API Summary**:
   - Issue GET to `{broker_status_url}/summary.json`.
   - Parse `page`, `status.indicator` (`none`, `minor`, `major`, `critical`), and `components` list.

2. **Classify Platform Incident Severity**:
   - Map indicator to `BrokerPlatformState`: `OPERATIONAL`, `DEGRADED`, `MAJOR_OUTAGE`.

3. **Diagnose Failures (Internal Bug vs External Outage)**:
   - When an API order exception occurs, check `BrokerPlatformState`. If status is `MAJOR_OUTAGE`, tag incident as `EXTERNAL_BROKER_OUTAGE` and trigger automatic circuit breaker without escalating internal code bug tickets.

> Full procedure: see `references/workflows.md`.
> Standards reference: see `references/standards.md`.
> Printable pre-flight checklist: see `assets/checklist.md`.

## Common Pitfalls

- **Delayed Status Page Updates**: Broker status pages often lag active outages by 5 to 15 minutes. Must combine status page polling with real-time HTTP probe error rates.
- **Unmonitored Sub-Components**: Status indicator returning "Operational" while specific sub-components (e.g. "Options Order Routing") are offline.
- **Rate-Limiting Status Feed**: Polling status pages too aggressively causing IP blocks by status aggregators.

## Verification

- Simulate Statuspage.io JSON feed parsing across Operational, Minor, and Critical indicators.
- Submit order exception during simulated broker outage and verify correct diagnosis as `EXTERNAL_BROKER_OUTAGE`.
- Run `python scripts/test_status_monitor.py` and confirm 100% pass rate.

## Related Skills

- `broker-failover-secondary-account-routing`
- `structured-logging-for-post-incident-forensics`
- `kill-switch-and-drawdown-circuit-breakers`
---

<!-- Reviewed and rigorously engineered -->
