---
name: kafka-based-tick-distribution-at-scale
description: >-
  Symbol-keyed tick distribution over Apache Kafka: partition routing that reproduces each client library's actual hash (librdkafka CRC32 vs Java/kafka-python murmur2), producer settings that make per-symbol ordering real rather than assumed, and a consumer-lag, staleness and hot-partition audit.
domain: Data Management Global
subdomain: Real-Time Tick Streaming & Kafka Infrastructure
tags: ["kafka", "tick-distribution", "market-data", "partition-routing", "consumer-lag", "batching", "streaming"]
brokers_frameworks: ["Apache Kafka Python", "aiokafka / confluent-kafka", "Python Dataclasses"]
version: "2.0.0"
author: algo-trading-skills-contributors
license: Apache-2.0
---

## When to Use

Use this skill when fanning market data out to many consumers over Kafka and the strategy layer depends on **per-symbol ordering** — the guarantee that every tick for one instrument arrives in the order the venue produced it. Kafka delivers that guarantee only *within a partition*, so the whole design rests on every tick for a symbol landing on the same partition, every time, from every producer in the fleet.

That is three separate conditions, and this skill exists because two of them are routinely assumed rather than checked:

1. **Every record carries a key.** An empty key is not "partition 0" — under librdkafka's default `consistent_random` partitioner it is *randomly* partitioned.
2. **Every producer hashes that key the same way.** There is no single "Kafka partitioner". The Java client, `kafka-python` and `aiokafka` use **murmur2**; librdkafka — and therefore `confluent-kafka-python` — defaults to **CRC32**. With 16 partitions, `AAPL` lands on partition **12** under one and partition **1** under the other.
3. **The producer cannot reorder on retry.** Kafka's own documentation: with `max.in.flight.requests.per.connection > 1`, `enable.idempotence=false` and retries enabled, "there is a risk of message reordering after a failed send due to retries." librdkafka ships exactly that combination by default.

The engine routes ticks under an explicitly named partitioner, emits the producer config that actually satisfies condition 3, and audits consumer lag, wall-clock staleness and partition skew.

## When NOT to Use

- **As a broker client.** This module opens no socket. It computes where a correctly configured producer *would* publish and evaluates offsets you supply. Its partition indices are predictions, valid only if the producer sends the same key bytes with the same partitioner.
- **As the sole staleness alarm, using lag alone.** Lag in messages is not staleness in time: 10,000 ticks is sub-second on a mega-cap and half a session on an illiquid name. Set `max_tick_age_ms` — that is the guardrail that answers "am I about to trade on an old quote?".
- **On an existing topic, without first checking the partitioner in use.** Changing the partitioner moves every symbol. Confirm which hash the current producers use before pointing this engine at a live topic.
- **With the shipped numbers unchanged.** `batch_size_bytes = 131_072`, `linger_ms = 5`, `max_lag_threshold_ticks = 10_000` and `partition_skew_threshold = 2.0` are tunable defaults. No exchange, regulator or Kafka document mandates any of them; Kafka's own `batch.size` default is 16 KB and librdkafka's is 1 MB.
- **For strict global ordering across symbols.** Partitions are ordered independently. Cross-symbol sequencing (e.g. an index and its constituents) is not preserved and cannot be recovered from partition order alone.

## Prerequisites

- Market tick stream payload (`symbol`, `timestamp_ns`, `bid_price`, `ask_price`, `bid_size`, `ask_size`, `last_price`, `last_size`). `symbol` and `timestamp_ns` are the two fields this engine routes and audits on, and are validated; the price and size fields are carried through untouched.
- Topic topology (`num_partitions`) and the **client library each producer uses** — the partitioner must be chosen, not inherited.
- Consumer offsets, and knowledge of which kind they are: committed group offsets (the group-offset view, e.g. `kafka-consumer-groups.sh`) or the consumer's current position (what the `records-lag` metrics report — Kafka notes these are "based on current offset and not committed offset"). Confirm which your tooling reports, then set `offset_basis` accordingly.

## Workflow

1. **Pin the partitioner before routing anything**:
   - Choose `PARTITIONER_CRC32` (librdkafka / confluent-kafka) or `PARTITIONER_MURMUR2` (Java, kafka-python, aiokafka) explicitly. `CLIENT_DEFAULT_PARTITIONER` records which library defaults to which.
   - If more than one client library writes to the topic, run `diagnose_partitioner_divergence(symbols)` **first**. Every symbol it returns has no ordering guarantee across the fleet. An empty result is agreement *at that partition count only*, not a licence to leave the choice implicit.
2. **Canonicalize the key once, at the edge**:
   - Route on `normalize_symbol_key(symbol)` and publish *that exact string* as the record key. The broker hashes the bytes the producer sent, not the bytes this module normalized — computing routing from `"aapl"` while publishing key `b"aapl"` alongside another service's `b"AAPL"` splits the instrument in two.
   - An empty or whitespace-only symbol raises rather than routing to partition 0, because the real client would scatter it at random.
3. **Emit producer settings that make ordering real**:
   - `build_producer_config(client)` returns the batching settings *plus* the three ordering preconditions Kafka requires together: `enable.idempotence=true`, `acks=all`, `max.in.flight.requests.per.connection <= 5`. Keying without these does not preserve order.
   - It raises rather than emitting a CRC32 config for a murmur2-only client — that would create the exact split step 1 detects.
   - `linger.ms = 5` buys throughput by *adding up to 5 ms of latency* to every tick. That is a deliberate trade, not a free optimization; drop it toward 0 on a latency-critical path.
4. **Audit the stream, in severity order**:
   - **Offset integrity first.** A consumed offset ahead of the log end offset is impossible in Kafka and means the offsets are wrong (wrong topic, reset group, mismatched epoch). The engine reports `OFFSET_INCONSISTENCY_WARNING` and outranks every other status: a broken measurement must not be reported as either healthy or alarming.
   - **Consumer lag**: $\text{Lag} = \text{Log End Offset} - \text{Consumed Offset}$, breached on strictly `>` `max_lag_threshold_ticks`. Committed-offset lag runs up to one commit interval hotter than current-position lag — do not tune a tight threshold against the wrong basis.
   - **Clock skew**: a tick timestamped *ahead* of this host's clock produces a negative age, and a negative age can never exceed a positive budget — so the staleness guard silently switches itself off exactly when the clocks it depends on are wrong. Reported as `CLOCK_SKEW_WARNING`; widen `clock_skew_tolerance_ms` to your PTP/NTP sync tolerance to avoid firing on normal jitter.
   - **Staleness**: age of the oldest tick in the batch against `max_tick_age_ms`. Off unless you set a budget.
   - **Partition skew**: busiest partition load ÷ mean load. Suppressed when there are fewer distinct symbols than partitions, because an uneven spread is then arithmetically forced rather than a defect.
   - **Upstream disorder**: a symbol whose event timestamp goes backwards is reported. Partition ordering preserves arrival order; it cannot repair a feed that was already out of order.
5. **Before changing the partition count**, run `symbols_remapped_by_partition_growth(symbols, new_n)`. Kafka does not reorganize existing data when partitions are added, so every symbol returned has history stranded on its old partition while new ticks arrive elsewhere.
6. **Emit `KafkaTickDistributionReport`**, which records the partitioner and offset basis it used alongside the findings, so a reader can tell what the numbers mean.

> Full procedure: see `references/workflows.md`.
> Standards reference: see `references/standards.md`.
> Printable pre-flight checklist: see `assets/checklist.md`.

## Common Pitfalls

- **Assuming there is one "Kafka default partitioner".** There are two. A fleet running `confluent-kafka` producers alongside `kafka-python` producers writes `AAPL` to two partitions on the same topic, and nothing anywhere raises an error — the ordering guarantee is simply gone, and it surfaces later as an inexplicable quote sequence in the strategy.
- **Believing the key alone guarantees ordering.** It does not. Under librdkafka's defaults (`enable.idempotence=false`, `max.in.flight=1000000`), a transient send failure and retry can reorder ticks *within* a single partition. Ordering needs idempotence, `acks=all` and bounded in-flight requests as well.
- **Publishing ticks without a symbol key.** Not a hot partition 0 — under the default `consistent_random` partitioner an empty key is randomly partitioned, so one instrument is smeared across the whole topic.
- **Expanding partitions on a live keyed topic.** Adding partitions rehashes keys. Kafka does not move existing data, and KIP-253 states plainly that in-order delivery is not guaranteed across a partition expansion.
- **Reading lag as staleness.** A 10,000-tick threshold is meaningless without knowing the symbol's tick rate. Alarm on time, and alarm on the right offset basis: committed-offset lag includes up to a full commit interval of records the consumer has already processed, so a tight threshold on that basis fires on the commit cadence rather than on a real backlog.
- **Clamping impossible lag to zero.** `max(0, lag)` on a consumed offset that exceeds the log end offset turns a broken monitor into a permanently green one. Treat it as a measurement failure, not as zero lag.
- **Letting clock skew disable the staleness guard.** The staleness check subtracts the tick timestamp from the local clock. If the feed handler's clock runs ahead of the consumer's, the age goes negative, and a negative age never exceeds a positive budget — the alarm reads "fresh" forever. Skew must be reported, not absorbed.
- **Under-partitioning the hot names.** Symbol-key routing distributes *symbols*, not *volume*. A handful of mega-caps can dominate one partition however many partitions exist, and no amount of consumer scaling helps a single hot partition.
- **Trusting Kafka to fix upstream disorder.** A partition preserves the order records were appended in. If the feed handler already delivered ticks out of sequence, they are appended — and delivered — out of sequence.

## Verification

- Instantiate `KafkaTickDistributionEngine(num_partitions=16)`. Verify `get_symbol_partition_id("AAPL") == 12` under the default CRC32 partitioner, and that `partition_for_key(b"AAPL", 16, PARTITIONER_MURMUR2) == 1` $\implies$ confirm `diagnose_partitioner_divergence(["AAPL"])` reports the split.
- Verify the murmur2 port against Apache Kafka's own published vectors: `murmur2(b"foobar") == -790332482` and `murmur2(b"a-little-bit-long-string") == -985981536` (signed 32-bit, as the Java client returns).
- Ingest 2,000 `AAPL` ticks with `max_lag_threshold_ticks=1000` and a consumed offset of 0 $\implies$ `CONSUMER_LAG_WARNING`. Repeat at exactly 1,000 ticks $\implies$ `KAFKA_STREAM_HEALTHY` (the rule is strictly greater-than).
- Supply a consumed offset **ahead** of the log end offset $\implies$ verify `OFFSET_INCONSISTENCY_WARNING`, not `KAFKA_STREAM_HEALTHY`.
- Publish a batch whose oldest tick is 900 ms old against `max_tick_age_ms=250` $\implies$ `STALE_TICK_WARNING`.
- Publish a tick timestamped 5,000 ms **ahead** of `now_ns` $\implies$ verify `CLOCK_SKEW_WARNING` and a negative `max_tick_age_ms`, not `KAFKA_STREAM_HEALTHY`.
- Route 400 ticks of one hot symbol plus 20 quiet symbols across 4 partitions $\implies$ `PARTITION_UNBALANCED_WARNING`; route 2 symbols across 16 partitions $\implies$ skew suppressed, `KAFKA_STREAM_HEALTHY`.
- Verify a rejected batch does not partially publish: a batch containing an unroutable symbol raises and leaves every partition's log end offset unchanged.
- Run `python -m unittest discover -s skills/kafka-based-tick-distribution-at-scale/scripts`.

## Related Skills

- `producer-consumer-tick-pipeline`
- `redis-streams-multi-consumer-tick-fanout`
- `consumer-group-rebalance-safety`
- `backpressure-drop-degrade-policy`
- `historical-tick-data-storage-and-compaction`
- `cross-region-data-replication-lag-monitoring`
