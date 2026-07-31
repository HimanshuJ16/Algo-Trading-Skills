---
name: redis-streams-multi-consumer-tick-fanout
description: >-
  Production-grade Redis Streams market data fanout manager with consumer group handling, XADD publishing, XREADGROUP consumption, XACK acknowledgment, and XCLAIM stale entry recovery for crashed workers.
domain: Infrastructure & DevOps
subdomain: Low-Latency Tick Distribution & Messaging
tags: ["redis-streams", "tick-fanout", "consumer-group", "xadd", "xack", "xclaim", "market-data-pipeline"]
brokers_frameworks: ["Redis Streams (XADD/XREADGROUP/XACK/XCLAIM)", "Python Dataclasses"]
version: "1.0.0"
author: algo-trading-skills-contributors
license: Apache-2.0
---

## When to Use

Use this skill when distributing real-time market data ticks to multiple independent consumer groups (strategy engines, risk monitors, logging pipelines) via Redis Streams. Redis Streams provide persistent, ordered message delivery with consumer group semantics: each group receives every message independently (fanout), and individual consumers within a group compete for messages (load balancing). This engine handles XADD publishing, consumer group creation, XREADGROUP consumption, XACK acknowledgment, and XCLAIM recovery for stale pending entries from crashed worker processes.

## Prerequisites

- Redis Streams instance (or in-memory `MockRedisStreamEngine` for testing).
- Tick data (`symbol`, `last_price`, `volume`, `timestamp`).
- Consumer group names and consumer worker IDs.

## Workflow

1. **Stream & Consumer Group Setup**:
   - Create stream and register consumer groups (`grp_strategy`, `grp_risk`, etc.) via `XGROUP CREATE`.
2. **Tick Publishing (XADD)**:
   - Publish tick data to Redis Stream with optional `MAXLEN` cap for memory management.
3. **Fanout Consumption (XREADGROUP)**:
   - Each consumer group independently reads all published ticks; workers within a group compete.
4. **Acknowledgment (XACK)**:
   - Workers acknowledge processed ticks, removing them from the Pending Entries List (PEL).
5. **Stale Entry Recovery (XCLAIM)**:
   - Reclaim un-ACKed entries from crashed workers after idle threshold exceeded.

> Full procedure: see `references/workflows.md`.
> Standards reference: see `references/standards.md`.
> Printable pre-flight checklist: see `assets/checklist.md`.

## Common Pitfalls

- **Unbounded Stream Growth**: Failing to set `MAXLEN` on XADD, causing Redis memory exhaustion.
- **Missing XACK**: Consuming ticks without acknowledging them, causing PEL to grow indefinitely.
- **Crashed Worker Orphaned Messages**: Not implementing XCLAIM recovery, leaving ticks permanently unprocessed after worker crashes.

## Verification

- Instantiate `RedisTickFanoutManager`. Publish AAPL tick, consume from 2 independent groups $\implies$ verify both groups receive the same tick (fanout). XACK a tick $\implies$ verify removed from PEL. Crash a worker, wait, XCLAIM $\implies$ verify stale tick re-assigned.
- Run `python scripts/test_redis_tick_fanout.py`.

## Related Skills

- `kafka-based-tick-distribution-at-scale`
- `producer-consumer-tick-pipeline`
---
