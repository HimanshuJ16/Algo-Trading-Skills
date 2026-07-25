---
name: consumer-group-rebalance-safety
description: >-
  Use when operating distributed message stream consumer groups (Kafka, Redis Streams) to handle partition rebalance events safely, flushing in-flight batches and committing offset checkpoints before partition reassignment to prevent duplicate tick processing.
domain: algorithmic-trading
subdomain: real-time-architecture
tags: ["real-time-architecture", "consumer-group", "rebalance-safety", "kafka", "redis-streams", "offset-commit", "partition-reassignment"]
brokers_frameworks: ["Consumer Rebalance Guard", "Python Async Stream"]
version: "1.0"
author: algo-trading-skills-contributors
license: Apache-2.0
---

## When to Use

Invoke this skill when deploying multi-worker distributed stream consumer groups (Kafka consumer groups, Redis Streams consumer groups) for market data or order updates. When consumer node instances auto-scale, crash, or restart, the broker triggers a Consumer Group Rebalance to reassign topic partitions across active workers. Processing or failing to commit in-flight messages during a rebalance window causes duplicate tick execution or dropped messages. This skill intercepts rebalance hooks to pause, flush, and commit offsets cleanly.

## Prerequisites

- Distributed stream consumer group setup with partition assignment listeners (`on_partitions_revoked`, `on_partitions_assigned`).
- Offset checkpoint commit interface.

## Workflow

1. **Register Rebalance Listener Hooks**:
   - Register callbacks for partition revocation (`on_partitions_revoked`) and partition assignment (`on_partitions_assigned`).

2. **Handle Partition Revocation (`on_partitions_revoked`)**:
   - Immediately pause new message fetches.
   - Process remaining in-flight message batch to completion.
   - Synchronously commit last processed offset checkpoint for revoked partitions.

3. **Handle Partition Assignment (`on_partitions_assigned`)**:
   - Initialize partition state for newly assigned partitions.
   - Resume message consumption from verified committed offset checkpoint.

4. **Verify Zero Duplicate / Dropped Ticks**:
   - Confirm no messages from revoked partitions are processed during the rebalance window.

> Full procedure: see `references/workflows.md`.
> Standards reference: see `references/standards.md`.
> Printable pre-flight checklist: see `assets/checklist.md`.

## Common Pitfalls

- **Uncommitted In-Flight Batches**: Allowing a rebalance to complete while holding uncommitted in-flight tick batches, causing duplicate processing when the new partition owner takes over.
- **Blocking Rebalance Callbacks**: Executing slow synchronous network operations inside the revocation callback, exceeding `max.poll.interval.ms` and triggering perpetual rebalance loops.
- **Ignoring Rebalance State Guards**: Continuing to emit order signals from a worker thread after its partition assignment has been revoked.

## Verification

- Simulate consumer group rebalance trigger (`REVOKE` $\to$ `ASSIGN`), verifying in-flight batch flush and offset commit before revocation completes.
- Verify zero message duplication during partition handover.
- Run `python scripts/test_rebalance_guard.py` and confirm 100% pass rate.

## Related Skills

- `kafka-based-tick-distribution-at-scale`
- `redis-streams-multi-consumer-tick-fanout`
- `graceful-shutdown-draining-in-flight-ticks`
---
