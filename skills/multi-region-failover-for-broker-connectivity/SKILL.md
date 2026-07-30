---
name: multi-region-failover-for-broker-connectivity
description: Use when a trading bot requires high-availability broker connectivity
  by failing over to a backup network path or region if the primary connection degrades
  or becomes unavailable.
domain: algorithmic-trading
subdomain: deployment-ops
tags:
- deployment
- failover
- high-availability
- multi-region
- broker-connectivity
brokers_frameworks:
- AWS
- GCP
- Azure
- Custom HA
version: '1.0'
author: algo-trading-skills-contributors
license: Apache-2.0
---

## When to Use

Invoke this skill whenever a trading bot's connectivity to a broker is mission-critical and
a single-region deployment creates unacceptable downtime risk. Network outages, cloud region
failures, or broker endpoint degradation can halt trading entirely. This skill implements:
- Primary and backup endpoint registration with health probing.
- Automatic failover when primary health checks fail consecutively.
- Failback to primary when it recovers, with configurable cooldown.

## Prerequisites

- At least two broker endpoints (primary + backup) in different regions or paths.
- Health check mechanism (ping, heartbeat, or lightweight API call).
- Configurable failure threshold and cooldown period.

## Workflow

1. **Register Endpoints**: Define primary and backup broker endpoints.
2. **Continuous Health Probing**: Periodically probe active endpoint health.
3. **Detect Failure**: After N consecutive failures, trigger failover.
4. **Switch to Backup**: Route all traffic to backup endpoint.
5. **Monitor Primary Recovery**: Continue probing primary; failback when healthy.
6. **Cooldown**: Prevent flapping with minimum time before failback.

> Full procedure: see `references/workflows.md`.
> Standards: see `references/standards.md`.
> Checklist: see `assets/checklist.md`.

## Common Pitfalls

- **Split Brain**: Both regions actively trading simultaneously after partial failover.
- **Flapping**: Rapidly switching between primary and backup due to intermittent issues.
- **Stale Position State**: Backup region not having current position state from primary.

## Verification

- Simulate primary failure and verify automatic failover to backup.
- Simulate primary recovery and verify failback after cooldown.
- Run `python scripts/test_region_failover.py` and confirm 100% pass rate.

## Related Skills

- `blue-green-deployment-for-live-strategy-updates`
- `websocket-reconnect-without-duplicate-subscriptions`
---
