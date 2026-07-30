---
name: consumer-group-rebalance-safety
description: Quantitative streaming infrastructure module for handling Kafka consumer
  group rebalances safely, preventing zombie execution, flushing in-flight orders,
  and enforcing offset commit idempotency.
domain: Infrastructure
subdomain: Event-Driven Systems & Streaming
tags:
- kafka
- consumer-group
- rebalance-safety
- event-driven
- idempotency
- zombie-consumer
- streaming
brokers_frameworks:
- Apache Kafka / Redpanda
- Generic Event Stream
version: 1.0.0
author: algo-trading-skills-contributors
license: Apache-2.0
---

## When to Use

Use this skill in event-driven quantitative trading architectures (e.g. processing streaming market data or order fills over Apache Kafka / Redpanda) where worker nodes belong to a Consumer Group. Unhandled consumer group rebalances can cause **zombie execution** (a node continuing to send orders after losing partition ownership) or **duplicate order execution** (a new worker reprocessing uncommitted order events). This module implements a safety guard around rebalance lifecycle hooks (`on_partitions_revoked`, `on_partitions_assigned`).

## Prerequisites

- Disables auto-commit (`enable.auto.commit = false`).
- Topic partition assignment tracking and message idempotency key tracking (`order_id` / `event_id`).

## Workflow

1. **Rebalance Event Trigger**: Kafka coordinator initiates partition rebalance (node join/leave/pause).
2. **Revocation Phase (`on_partitions_revoked`)**:
   - Immediately set `is_partition_active[p] = False` to fence the worker thread from accepting new trades.
   - Synchronously flush in-flight execution orders in the buffer.
   - Commit current offsets for revoked partitions synchronously.
3. **Assignment Phase (`on_partitions_assigned`)**:
   - Reset local state and load assigned partition offsets.
   - Set `is_partition_active[p] = True` for newly assigned partitions.
4. **Rebalance Storm Detection**: Track rebalance event frequency; if rebalances exceed threshold (e.g. $> 3$ in 60 seconds), alert on worker cluster instability.

> Full procedure: see `references/workflows.md`.
> Standards reference: see `references/standards.md`.
> Printable pre-flight checklist: see `assets/checklist.md`.

## Common Pitfalls

- **Continuing Processing During Revocation**: Allowing the event loop to execute trades on a partition that has already been revoked by the group coordinator.
- **Asynchronous Commit in `onPartitionsRevoked`**: Using async offset commits during revocation. If the rebalance completes before the async commit succeeds, another worker will reprocess the same messages.
- **Relying on Auto-Commit**: Leaving `enable.auto.commit=true`. Auto-commit will randomly commit offsets regardless of whether the trading engine finished processing the order batch.

## Verification

- Instantiate `ConsumerGroupRebalanceGuard`. Assign partitions `[0, 1]`. Process message `ORDER_101` on partition `0` (recorded in processed set). Trigger `on_partitions_revoked([0])`. Verify that partition `0` is fenced (`is_active = False`) and in-flight buffers are flushed. Attempt to process another order on partition `0` and verify it is rejected with a `PartitionRevokedException`.
- Run `python scripts/test_rebalance_guard.py`.

## Related Skills

- `binary-protocol-parsing-for-low-latency-feeds`
- `cross-region-data-replication-lag-monitoring`
---
