---
name: adaptive-batch-size-tuning-under-load
description: Use when writing market data or order logs to downstream databases (TimescaleDB,
  ClickHouse) or message brokers to dynamically adapt write batch sizes and flush
  timeouts based on queue pressure and sink write latency.
domain: algorithmic-trading
subdomain: real-time-architecture
tags:
- real-time-architecture
- adaptive-batching
- dynamic-tuning
- throughput-optimization
- database-sink
- load-balancing
brokers_frameworks:
- Adaptive Batch Tuner
- Python Real-Time Engine
version: '1.0'
author: algo-trading-skills-contributors
license: Apache-2.0
---

## When to Use

Invoke this skill when persisting high-volume tick feeds or trading logs into downstream databases (e.g. TimescaleDB, ClickHouse, InfluxDB) or message queues. Hardcoding static batch sizes introduces high latency during quiet market hours (waiting for fixed batch limits to fill) and DB I/O overload during market flash crashes. An adaptive batch tuner dynamically scales batch size $B_t$ and flush interval $T_{\text{flush}}$ in response to queue backlog pressure and sink latency feedback.

## Prerequisites

- Downstream sink write function accepting batches of records.
- Min batch size $B_{\text{min}}$, max batch size $B_{\text{max}}$, and max flush timeout $T_{\text{max}}$.

## Workflow

1. **Monitor Queue Pressure & Backlog Ratio**:
   - Compute relative queue fill ratio $R = \frac{Q_{\text{current}}}{Q_{\text{capacity}}}$.

2. **Adapt Batch Size & Flush Interval**:
   - High Load ($R > 0.70$): Increase batch size $B_{t+1} = \min(B_{\text{max}}, \lfloor B_t \times 1.5 \rfloor)$ and reduce max flush delay to maximize I/O throughput.
   - Low Load ($R < 0.10$): Decrease batch size $B_{t+1} = \max(B_{\text{min}}, \lfloor B_t / 1.2 \rfloor)$ to minimize tick persistence latency.

3. **Incorporate Latency Feedback Guard**:
   - If downstream DB write latency exceeds target threshold $L_{\text{target}}$ (e.g. 50ms), cap batch size to prevent DB lock contention.

4. **Execute Flush Trigger**:
   - Flush batch when accumulated items $\ge B_t$ or elapsed time $\ge T_{\text{flush}}$.

> Full procedure: see `references/workflows.md`.
> Standards reference: see `references/standards.md`.
> Printable pre-flight checklist: see `assets/checklist.md`.

## Common Pitfalls

- **Unbounded Max Batch Size**: Allowing batch size to grow beyond database RAM limits, causing DB out-of-memory crashes.
- **Ignoring Write Latency Feedback**: Scaling up batch size during DB index lock contention, escalating DB connection pool exhaustion.
- **Rapid Oscillations**: Lacking damping factors on batch size adjustments, causing constant thrashing between min and max batch sizes.

## Verification

- Simulate low queue load ($R < 10\%$), verify batch size shrinks to $B_{\text{min}}$ for fast flush.
- Simulate high queue load ($R > 70\%$), verify batch size expands to $B_{\text{max}}$ for high throughput.
- Run `python scripts/test_batch_tuner.py` and confirm 100% pass rate.

## Related Skills

- `kafka-based-tick-distribution-at-scale`
- `producer-consumer-tick-pipeline`
- `tick-buffering-burst-handling`
---
