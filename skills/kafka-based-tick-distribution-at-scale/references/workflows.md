# Deep Workflow Reference — kafka-based-tick-distribution-at-scale

This file holds the full technical procedure referenced by `SKILL.md`.

## Full Procedure

1. **Symbol-Keyed Partition Routing**:
   - Hash ticker symbol to partition key (`hash(symbol) % num_partitions`).
   - Guarantee that all ticks for an asset land on the same partition for sequence preservation.

2. **High-Throughput Producer Batching**:
   - Set `linger_ms=5`, `batch_size=16384`, and `compression_type='snappy'`.

3. **Multi-Consumer Group Offset Checkpointing**:
   - Ingest messages in batches per consumer group.
   - Commit offsets (`commit_offset`) after successful downstream processing.

## Production Implementation Reference

- Reference code: `scripts/kafka_tick_engine.py` (`KafkaTickProducerConsumerEngine`, `KafkaTickMessage`).
- Automated unit tests: `scripts/test_kafka_tick_engine.py`.
