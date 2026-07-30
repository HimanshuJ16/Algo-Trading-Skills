---
name: kafka-based-tick-distribution-at-scale
description: Use when building high-throughput distributed market data ingestion pipelines
  (50k+ ticks/sec) using Apache Kafka to partition tick events by symbol, balance
  consumer group loads, configure producer batching, and commit offset checkpoints
  cleanly.
domain: algorithmic-trading
subdomain: real-time-architecture
tags:
- real-time-architecture
- kafka
- tick-distribution
- partitioning
- consumer-groups
- high-throughput
brokers_frameworks:
- Kafka Producer/Consumer
- kafka-python
- confluent-kafka
version: '1.0'
author: algo-trading-skills-contributors
license: Apache-2.0
---

## When to Use

Invoke this skill when scaling market data ingestion beyond single-host in-memory message brokers (e.g., Redis Pub/Sub) to handle institutional tick volumes (50,000+ ticks/second) across multiple downstream analytical engines (strategy workers, historical archivers, compliance auditors). Kafka provides persistent topic partitions, symbol-keyed order preservation, offset checkpointing, and horizontal consumer group scaling.

## Prerequisites

- Apache Kafka cluster / MSK brokers.
- Topic configuration with $N$ partitions (e.g., 16 partitions per `market-ticks` topic).

## Workflow

1. **Symbol-Keyed Partitioning**:
   - Hash ticker symbol to partition key (`key = symbol.encode("utf-8")`) to ensure all ticks for `AAPL` or `BTCUSDT` land on the exact same partition, preserving sequence ordering.

2. **Configure Producer Batching**:
   - Set `linger_ms=5` and `batch_size=16384` (16KB) with `compression_type='snappy'` to maximize throughput while capping latency.

3. **Consumer Group Load Balancing**:
   - Register consumer groups (`strategy-workers`, `tick-archivers`).
   - Process partition batches concurrently and commit offsets (`commit_offset`) after successful batch processing.

> Full procedure: see `references/workflows.md`.
> Standards reference: see `references/standards.md`.
> Printable pre-flight checklist: see `assets/checklist.md`.

## Common Pitfalls

- **Unkeyed Random Partitioning**: Omitting partition keys, causing ticks for the same symbol to arrive out-of-order across different partitions.
- **Excessive Linger Delays**: Setting `linger_ms` too high (e.g., 100ms), introducing unacceptable latency for high-frequency strategy workers.
- **Auto-Commit Data Loss**: Enabling `enable.auto.commit=true` without verifying downstream batch processing success, risking dropped ticks during worker crashes.

## Verification

- Publish 1,000 ticks across 4 partitions with symbol keys and verify strict per-symbol ordering.
- Verify consumer group offset commits track processed records cleanly without duplication.
- Run `python scripts/test_kafka_tick_engine.py` and confirm 100% pass rate.

## Related Skills

- `redis-streams-multi-consumer-tick-fanout`
- `producer-consumer-tick-pipeline`
- `tick-buffering-burst-handling`
---
