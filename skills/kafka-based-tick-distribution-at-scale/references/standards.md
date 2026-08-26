# Standards & Sources for Kafka Tick Distribution

## What is actually documented by Kafka and its clients

These are documented client behaviours, not this skill's choices. They constrain what any symbol-keyed tick topic can and cannot guarantee. Verified 2026-08.

| Area | Documented behaviour | Source |
|---|---|---|
| Keyed partitioning | "If no partition is specified but a key is present, choose a partition based on a hash of the key. If no partition or key is present, choose the sticky partition that changes when at least batch.size bytes are produced to the partition." | [Apache Kafka 4.0 producer configs — `partitioner.class`](https://kafka.apache.org/40/generated/producer_config.html) |
| Ordering vs retries | "Note that if this configuration is set to be greater than 1 and `enable.idempotence` is set to false, there is a risk of message reordering after a failed send due to retries (i.e., if retries are enabled); if retries are disabled or if `enable.idempotence` is set to true, ordering will be preserved. Additionally, enabling idempotence requires the value of this configuration to be less than or equal to 5, because broker only retains at most 5 batches for each producer." | [Apache Kafka 4.0 producer configs — `max.in.flight.requests.per.connection`](https://kafka.apache.org/40/generated/producer_config.html) |
| Idempotence preconditions | "Note that enabling idempotence requires `max.in.flight.requests.per.connection` to be less than or equal to 5 (with message ordering preserved for any allowable value), `retries` to be greater than 0, and `acks` must be 'all'." | [Apache Kafka 4.0 producer configs — `enable.idempotence`](https://kafka.apache.org/40/generated/producer_config.html) |
| librdkafka partitioners | "`consistent` - CRC32 hash of key (Empty and NULL keys are mapped to single partition), `consistent_random` - CRC32 hash of key (Empty and NULL keys are randomly partitioned), `murmur2` - Java Producer compatible Murmur2 hash of key (NULL keys are mapped to single partition), `murmur2_random` - Java Producer compatible Murmur2 hash of key (NULL keys are randomly partitioned. This is functionally equivalent to the default partitioner in the Java Producer.)" — default `consistent_random`. | [librdkafka CONFIGURATION.md](https://github.com/confluentinc/librdkafka/blob/master/CONFIGURATION.md) |
| Java / kafka-python hash | `Utils.murmur2` with seed `0x9747b28c`, `m = 0x5bd1e995`, `r = 24`, little-endian 4-byte reads, returning a signed 32-bit int; the partition is `toPositive(hash) % numPartitions` where `toPositive(n) = n & 0x7fffffff`. kafka-python's `DefaultPartitioner` performs the identical `idx &= 0x7fffffff; idx %= len(all_partitions)`. | [Kafka `Utils.java`](https://github.com/apache/kafka/blob/trunk/clients/src/main/java/org/apache/kafka/common/utils/Utils.java), [kafka-python `partitioner/default.py`](https://github.com/dpkp/kafka-python/blob/master/kafka/partitioner/default.py) |
| Partition expansion | Kafka's FAQ: keyed messages are deterministically mapped to a partition by key hash, but "if the number of partitions changes, this delivery guarantee may no longer hold", and Kafka does not reorganize existing data on expansion. KIP-253: "this in-order delivery is not guaranteed if we expand partition of the topic." | [Apache Kafka FAQ](https://cwiki.apache.org/confluence/display/KAFKA/FAQ), [KIP-253](https://cwiki.apache.org/confluence/display/KAFKA/KIP-253:+Support+in-order+message+delivery+with+partition+expansion) |
| Lag metric basis | Kafka distinguishes the two bases explicitly: `records-lag-max` carries the note "This is based on current offset and not committed offset". A lag computed from committed group offsets is therefore **not** the same quantity as the client-side metric. `kafka-consumer-groups.sh` is documented only as showing "the position of all consumers in a consumer group as well as how far behind the end of the log they are" — Kafka's operations guide does not spell out the exact semantics of its `CURRENT-OFFSET` column, so **confirm the basis your own tooling reports** rather than assuming. | [Apache Kafka 4.0 consumer metrics](https://kafka.apache.org/40/generated/consumer_metrics.html), [Basic Kafka Operations](https://kafka.apache.org/38/operations/basic-kafka-operations/) |

## Client defaults differ on every axis that matters

Not one of these is a "Kafka default" — they are per-library defaults, and mixing libraries is where symbol-keyed ordering silently dies.

| Setting | Apache Kafka 4.0 (Java) | librdkafka / confluent-kafka |
|---|---|---|
| Key hash | murmur2 | **CRC32** (`consistent_random`) |
| `enable.idempotence` | `true` | **`false`** |
| `acks` | `all` | `-1` (equivalent to `all`) |
| `max.in.flight.requests.per.connection` | `5` | **`1000000`** |
| `batch.size` | `16384` (16 KB) | `1000000` (1 MB) |
| `linger.ms` | `5` | `5` |
| `compression.type` | `none` | `none` |

Sources: [Kafka 4.0 producer configs](https://kafka.apache.org/40/generated/producer_config.html), [librdkafka CONFIGURATION.md](https://github.com/confluentinc/librdkafka/blob/master/CONFIGURATION.md).

**Consequence**: a `confluent-kafka` producer left on defaults both routes keys differently from a Java/`kafka-python` producer *and* can reorder within a partition on retry, because `enable.idempotence=false` combined with `max.in.flight=1000000` is precisely the combination Kafka documents as reordering-prone.

## This skill's engineering rules

Everything below is an engineering choice made by this skill. **None of it is published by Apache Kafka, an exchange, or a regulator.**

| Rule | Requirement | Why |
|---|---|---|
| Explicit partitioner | The partitioner MUST be named, never inherited from a client default. | The two client families disagree; an implicit choice splits each symbol across two partitions with no error. |
| Key canonicalization | Routing MUST use `normalize_symbol_key(symbol)`, and the producer MUST publish that exact string as the key. | The broker hashes the bytes the producer sent, not the bytes this module normalized. |
| Empty key | An empty or whitespace-only symbol MUST raise. | Under the default `consistent_random` partitioner an empty key is randomly partitioned — it does not fail loudly, it scatters the instrument. |
| Ordering preconditions | Any emitted producer config MUST set `enable.idempotence=true`, `acks=all`, `max.in.flight.requests.per.connection <= 5` together. | Kafka documents all three as jointly required; keying alone does not preserve order under retry. |
| Cross-client config | A CRC32 configuration MUST NOT be emitted for a murmur2-only client. | kafka-python/aiokafka expose no CRC32 setting; emitting one anyway silently produces the split this skill detects. |
| Offset integrity | A consumed offset ahead of the log end offset MUST be reported, not clamped to zero lag. | `max(0, lag)` turns a monitor wired to the wrong topic or a reset group into a permanently green one. |
| Status precedence | Offset inconsistency MUST outrank the lag warning. | A measurement known to be wrong must be reported as neither healthy nor alarming. |
| Staleness | Time-based staleness MUST be available alongside message-count lag. | 10,000 ticks is sub-second on a mega-cap and half a session on an illiquid name. |
| Clock skew | A tick timestamped ahead of the local clock MUST be reported, not read as fresh. | A negative age can never exceed a positive budget, so skew silently disables the staleness guard. |
| Skew audit scope | Skew MUST NOT be flagged when distinct symbols < partitions. | An uneven spread is then arithmetically forced, not a defect; flagging it is a false alarm. |
| Batch atomicity | A batch containing an invalid tick MUST leave partition state unchanged. | A partially published batch leaves offsets that no longer describe what was sent. |
| Throughput reporting | Any reported throughput MUST be measured. | A constant multiple of the batch size is a fabricated metric, not a measurement. |

## Tunable defaults (calibrate, do not inherit)

| Parameter | Default | Status |
|---|---|---|
| `batch_size_bytes` | `131_072` (128 KB) | Engineering starting point — 8x the Java default, 1/8th the librdkafka default. Not mandated by anyone. |
| `linger_ms` | `5` | Matches both client defaults. Buys batching by **adding up to 5 ms of latency per tick**; lower it on a latency-critical path. |
| `max_lag_threshold_ticks` | `10_000` | Heuristic capacity guardrail. Meaningless without the symbol's tick rate — calibrate per liquidity tier. |
| `max_tick_age_ms` | `None` (off) | The real staleness guardrail. Set it per venue; opt-in so it is a deliberate choice. |
| `partition_skew_threshold` | `2.0` (max/mean) | Heuristic. Only evaluated when distinct symbols >= partitions. |
| `clock_skew_tolerance_ms` | `0.0` (strict) | Engineering choice. Widen to the host's PTP/NTP sync tolerance so normal jitter does not fire the alarm every batch. |
| `compression_type` | `"lz4"` | Engineering choice; Kafka's default is `none`. Tick payloads compress well, at a CPU and latency cost. |

## Scope boundary

This module is an offline router and auditor. It opens no broker connection, and its partition indices are predictions of where a correctly configured client would publish — correct only when the producer sends the same key bytes under the same partitioner. It is not a compliance artifact and asserts no regulatory requirement.
