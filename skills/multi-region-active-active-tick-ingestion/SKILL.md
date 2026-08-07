---
name: multi-region-active-active-tick-ingestion
description: Use when deploying multi-region high-availability market data infrastructure
  to ingest active-active parallel tick streams from redundant cloud regions, deduplicating
  ticks by signature, and arbitrating lowest-latency message arrival.
domain: algorithmic-trading
subdomain: real-time-architecture
tags:
- real-time-architecture
- active-active
- multi-region
- deduplication
- latency-arbitration
- high-availability
brokers_frameworks:
- Active-Active Ingest Engine
- Python Async Real-Time
version: "1.0.0"
author: algo-trading-skills-contributors
license: Apache-2.0
---

## When to Use

Invoke this skill when operating zero-downtime, fault-tolerant quantitative trading engines that ingest market data across multiple geographic cloud regions simultaneously (e.g. AWS `us-east-1` and `us-west-2`). Running single-region ingestion introduces a single point of failure (SPOF) during regional AWS/GCP outages or Fiber cuts. This skill ingests active-active streams, deduplicates twin tick signatures, forwards the earliest-arriving tick to the strategy engine, and tracks inter-region latency telemetry.

## Prerequisites

- Active-active market data ingest nodes in at least two distinct cloud regions.
- Unique tick signature keys (`symbol` + `timestamp` + `sequence_id` or `price`).

## Workflow

1. **Ingest Dual-Region Tick Streams**:
   - Receive ticks simultaneously from Region A (e.g. `us-east-1`) and Region B (e.g. `us-west-2`).

2. **Generate Tick Signature Key**:
   - Compute hash key $K = \text{MD5}(\text{symbol} \parallel \text{timestamp} \parallel \text{price} \parallel \text{volume})$.

3. **Deduplicate & Emit First Arrival**:
   - If key $K$ has not been seen in the rolling deduplication window, emit tick immediately to strategy engine and register $(K, t_{\text{arrival}}, \text{region})$.
   - If key $K$ has already been seen (duplicate from secondary region), discard duplicate and record inter-region latency differential $\Delta t = t_{\text{second}} - t_{\text{first}}$.

4. **Monitor Regional Health Telemetry**:
   - Track rolling win-rates (percentage of ticks where Region A arrived before Region B) and flag region degradation if one region stops emitting.

> Full procedure: see `references/workflows.md`.
> Standards reference: see `references/standards.md`.
> Printable pre-flight checklist: see `assets/checklist.md`.

## Common Pitfalls

- **Unbounded Deduplication Windows**: Storing tick signatures indefinitely without a sliding expiration window, exhausting memory.
- **Clock Skew False Duplicates**: Failing to normalize timestamps across regions when regional server clocks drift.
- **Dropping Out-of-Order Fast Ticks**: Discarding a faster tick because signature generation introduced CPU bottleneck latency.

## Verification

- Submit twin ticks from Region A (t=0.0s) and Region B (t=0.005s), verifying Region A tick is emitted and Region B tick is deduplicated.
- Measure inter-region differential $\Delta t = 5.0\text{ms}$.
- Run `python scripts/test_active_active_ingest.py` and confirm 100% pass rate.

## Related Skills

- `multi-region-failover-for-broker-connectivity`
- `clock-skew-correction-for-tick-timestamps`
- `kafka-based-tick-distribution-at-scale`
---
