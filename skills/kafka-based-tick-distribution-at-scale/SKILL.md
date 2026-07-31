---
name: kafka-based-tick-distribution-at-scale
description: >-
  Scalable market data messaging engine for Apache Kafka, implementing symbol-key partition routing, producer batching (128KB, 5ms linger), and real-time consumer lag monitoring.
domain: Data Management Global
subdomain: Real-Time Tick Streaming & Kafka Infrastructure
tags: ["kafka", "tick-distribution", "market-data", "partition-routing", "consumer-lag", "batching", "streaming"]
brokers_frameworks: ["Apache Kafka Python", "aiokafka / confluent-kafka", "Python Dataclasses"]
version: "1.0.0"
author: algo-trading-skills-contributors
license: Apache-2.0
---

## When to Use

Use this skill when building high-throughput market data distribution systems streaming millions of market ticks per second via Apache Kafka. Market data pipelines require strict **per-symbol message ordering** (accomplished via symbol key partitioning), optimized producer batching (`linger.ms = 5`, `batch.size = 128KB`), and real-time **consumer lag monitoring** to prevent stale market quotes from reaching execution algorithms.

## Prerequisites

- Market tick stream payload (`symbol`, `timestamp_ns`, `bid_price`, `ask_price`, `bid_size`, `ask_size`, `last_price`, `last_size`).
- Kafka broker cluster topology config (`num_partitions`, `max_lag_threshold`).

## Workflow

1. **Symbol Key Partition Routing**:
   - Route tick payload using deterministic symbol hashing:
     $$\text{Partition} = \text{hash}(\text{symbol}) \pmod{\text{Num Partitions}}$$
   - Guarantees strict chronological ordering for each ticker symbol.
2. **Producer Batching & Throughput Optimization**:
   - Batch ticks into 128 KB memory buffers with 5 ms linger windows.
3. **Consumer Lag & Backpressure Audit**:
   - Compute per-partition consumer lag: $\text{Lag} = \text{Log End Offset} - \text{Committed Offset}$.
   - If $\text{Lag} > \text{max\_lag\_threshold}$ (e.g. 10,000 ticks) $\implies$ Trigger `CONSUMER_LAG_WARNING`.
4. **Audit Report Generation**: Output structured `KafkaTickDistributionReport`.

> Full procedure: see `references/workflows.md`.
> Standards reference: see `references/standards.md`.
> Printable pre-flight checklist: see `assets/checklist.md`.

## Common Pitfalls

- **Publishing Ticks Without a Symbol Key**: Publishing market ticks without setting `Key = Symbol`, causing random partition assignment and out-of-order quote execution crashes.
- **Ignoring Consumer Lag**: Allowing consumer lag to build up silently during market volatility spikes, executing trades on stale quotes from 30 seconds ago.
- **Under-Partitioning High-Volume Tickers**: Allocating insufficient partitions for active markets (e.g. 1 partition for 500 US tickers), causing single-threaded consumer bottlenecks.

## Verification

- Instantiate `KafkaTickDistributionEngine`. Ingest 50,000 market ticks across 16 partitions. Verify symbol key partitioning routes all `AAPL` ticks to the exact same partition index. Audit high consumer lag ($15,000$ ticks lag $> 10,000$ threshold) $\implies$ verify engine flags `CONSUMER_LAG_WARNING`.
- Run `python scripts/test_kafka_tick_engine.py`.

## Related Skills

- `historical-tick-data-storage-and-compaction`
- `cross-region-data-replication-lag-monitoring`
---
