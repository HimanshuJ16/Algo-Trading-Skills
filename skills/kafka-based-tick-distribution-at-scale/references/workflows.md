# Workflows for Kafka Tick Distribution

## 0. Establish what the topic already does

Before routing a single tick at an existing topic:

- Identify **which client library each producer uses**. `CLIENT_DEFAULT_PARTITIONER` records the mapping: `java`, `kafka-python` and `aiokafka` default to murmur2; `confluent-kafka` and `librdkafka` default to CRC32.
- Match the engine's `partitioner` to what the topic's existing producers actually use. Changing it moves every symbol.
- Record `num_partitions`. Every partition index this engine produces is only valid at that count.

## 1. Pin the partitioner and check for fleet divergence

```python
engine = KafkaTickDistributionEngine(num_partitions=16, partitioner=PARTITIONER_CRC32)
split = engine.diagnose_partitioner_divergence(universe)
```

Every symbol returned would be routed to a different partition by a CRC32 client than by a murmur2 client. Those symbols have **no ordering guarantee** across a mixed fleet. Resolve by standardising the partitioner across all producers — not by ignoring the list.

An empty result means the two families happen to agree *at this partition count*. It is not evidence that leaving the partitioner implicit is safe; the agreement disappears the moment the partition count changes.

## 2. Canonicalize the key at the edge

Route on `normalize_symbol_key(symbol)` and publish **that exact string** as the record key. The broker hashes what the producer sent. Two services publishing `b"aapl"` and `b"AAPL"` are publishing two different keys, which land on two partitions, whatever this module computed.

An empty or whitespace-only symbol raises here rather than routing to partition 0 — under librdkafka's default `consistent_random` partitioner an empty key is *randomly* partitioned, so the failure mode is silent scattering, not a hot partition.

## 3. Build producer settings that make ordering real

```python
config = engine.build_producer_config("confluent-kafka")
```

The returned mapping carries the batching settings **and** the three preconditions Kafka documents as jointly required for ordering under retry:

- `enable.idempotence=true`
- `acks="all"`
- `max.in.flight.requests.per.connection <= 5`

Without these, a transient send failure and retry can reorder ticks *within* a partition, and the symbol-key design does not protect against it. librdkafka's shipped defaults (`enable.idempotence=false`, `max.in.flight=1000000`) are exactly the reordering-prone combination.

The method raises rather than emitting a CRC32 config for `kafka-python`/`aiokafka`/`java`: those clients expose no CRC32 partitioner setting, so honouring the request would require a custom partitioner callable, and silently returning a murmur2 config would create the split step 1 exists to detect.

**Latency note**: `linger.ms = 5` adds up to 5 ms to every tick's publish path in exchange for larger batches. On a latency-critical route, lower it deliberately rather than inheriting it.

## 4. Route and audit

```python
report = engine.publish_and_audit_ticks(ticks, simulated_consumed_offsets=offsets, now_ns=now)
```

The batch is fully validated before any partition state is mutated, so a rejected batch leaves the engine untouched — no partially published batch, no offsets that misdescribe what was sent.

Checks run in this severity order, and every triggered condition is recorded in `report.warnings` even when a higher-severity one wins `report.status`:

1. **`OFFSET_INCONSISTENCY_WARNING`** — a consumed offset ahead of the log end offset. Impossible in Kafka; means the offsets are wrong (wrong topic, reset group, mismatched epoch). It outranks everything because a measurement known to be broken must be reported as neither healthy nor alarming. Clamping it to zero lag is how a misconfigured monitor stays green forever.
2. **`CONSUMER_LAG_WARNING`** — `log_end_offset - consumed_offset` strictly greater than `max_lag_threshold_ticks`. Interpret against `offset_basis`: a lag taken from committed group offsets runs up to one commit interval hotter than one taken from the consumer's current position, and Kafka notes its `records-lag` family is "based on current offset and not committed offset". Confirm which basis your tooling reports before tuning a tight threshold.
3. **`CLOCK_SKEW_WARNING`** — the oldest tick is timestamped *ahead* of this host's clock by more than `clock_skew_tolerance_ms`. Reported ahead of the staleness verdict for the same reason offset inconsistency is reported ahead of lag: the metric is not trustworthy. Skew does not merely distort the age, it drives it negative, and a negative age can never exceed a positive budget — so the staleness guard turns itself off precisely when the clocks are wrong.
4. **`STALE_TICK_WARNING`** — the oldest tick in the batch exceeds `max_tick_age_ms`. Off unless a budget is set. This, not message-count lag, is the check that answers "am I about to trade on an old quote?".
5. **`PARTITION_UNBALANCED_WARNING`** — busiest partition load ÷ mean load above `partition_skew_threshold`. Suppressed when distinct symbols < partitions, where an uneven spread is arithmetic rather than a defect. Symbol-key routing distributes *symbols*, not *volume*, so a few mega-caps can dominate one partition at any partition count.
6. **Upstream disorder** — a symbol whose event timestamp goes backwards, tracked across successive batches. Reported as a warning, never as a partition-health status: a partition preserves the order records were appended in and cannot repair a feed that was already out of sequence.

## 5. Before changing the partition count

```python
remapped = engine.symbols_remapped_by_partition_growth(universe, new_num_partitions)
```

Kafka does not reorganize existing data when partitions are added, so every symbol returned has history stranded on its old partition while new ticks arrive on a different one. KIP-253 states that in-order delivery is not guaranteed across a partition expansion. Drain or replay those symbols deliberately; do not expand a keyed tick topic mid-session and assume ordering survives.

## 6. Read the report

`KafkaTickDistributionReport` records the `partitioner` and `offset_basis` it used alongside its findings, so a downstream reader can tell what the numbers mean. Note two field semantics precisely:

- `assigned_partition_id` is the partition of the **last tick in the batch only**. For a multi-symbol batch use `symbols_partition_map` or `partition_tick_counts`.
- `throughput_ticks_per_sec` is the **measured routing rate of this call** — this module's own loop — not broker or end-to-end pipeline throughput.
