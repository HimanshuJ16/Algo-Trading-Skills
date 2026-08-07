---
name: cross-region-data-replication-lag-monitoring
description: Quantitative observability module for measuring cross-region database
  and message broker (Aurora, Kafka, Redis) replication lag, tracking P95/P99 SLAs,
  and triggering stale-read failovers.
domain: Infrastructure & Real-Time Architecture
subdomain: Cross-Region Telemetry
tags:
- cross-region
- data-replication
- replication-lag
- p99-sla
- stale-reads
- aurora
- kafka-mirrormaker
brokers_frameworks:
- Kafka
- Aurora
- Python Dataclasses
version: "1.0.0"
author: algo-trading-skills-contributors
license: Apache-2.0
---

## When to Use

Use this skill in distributed quantitative trading architectures operating multi-region databases or event streams (e.g., AWS Aurora Global Database, Kafka MirrorMaker 2, Redis Cross-Region Replication). Streaming market data, trade fills, or account balance updates across regions (e.g., `us-east-1` to `eu-west-1`) incurs network replication latency. If secondary replicas lag behind primary writers, trading algorithms reading from stale secondary nodes risk executing duplicate orders or using outdated position states. This module computes P95/P99 replication lag and triggers automated read-failovers.

## Prerequisites

- Synchronized cross-region heartbeat timestamp records (`primary_timestamp_ms`, `replica_timestamp_ms`).
- Maximum allowable replication lag SLA (e.g. P99 $\le 500\text{ ms}$).

## Workflow

1. **Heartbeat Ingestion**:
   - Ingest heartbeat payload for region pair ($R_{\text{primary}} \to R_{\text{replica}}$).
2. **Replication Lag Calculation**:
   - Calculate latency: $\Delta t = t_{\text{replica}} - t_{\text{primary}}$.
3. **P95 / P99 Metric Computation**:
   - Compute P95 and P99 percentiles across rolling sample window $N$.
4. **Replica Health Classification**:
   - If $\text{P99} \le 100\text{ ms} \implies$ `HEALTHY`.
   - If $100\text{ ms} < \text{P99} \le 500\text{ ms} \implies$ `DEGRADED_WARNING`.
   - If $\text{P99} > 500\text{ ms} \implies$ `UNSAFE_STALE` (Block reads / Failover to primary).

> Full procedure: see `references/workflows.md`.
> Standards reference: see `references/standards.md`.
> Printable pre-flight checklist: see `assets/checklist.md`.

## Common Pitfalls

- **Reading Stale Balances**: Allowing trading bots in secondary regions to read un-replicated position balances during replication spikes, causing over-leveraging.
- **Relying on Mean Replication Lag**: Evaluating average lag instead of P99 tail latency, missing intermittent 5-second replication lag spikes.
- **Un-synchronized Host Clocks**: Measuring cross-region replication lag without validating PTP/NTP clock synchronization across servers.

## Verification

- Instantiate `CrossRegionReplicationLagMonitor`. Input 100 heartbeat records for `us-east-1` $\to$ `eu-west-1` with normal latency around 40 ms. Verify report status is `HEALTHY` (P99 $\approx 45\text{ ms}$). Inject 5 delayed heartbeats of $1200\text{ ms}$ (P99 $> 500\text{ ms}$). Verify monitor flags `UNSAFE_STALE` and emits a read-failover alert.
- Run `python scripts/test_data_replication_monitoring.py`.

## Related Skills

- `cross-datacenter-clock-sync-validation`
- `cost-monitoring-for-cloud-trading-infrastructure`
---
