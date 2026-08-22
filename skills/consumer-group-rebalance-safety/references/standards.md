# Standards & Protocol Semantics — consumer-group-rebalance-safety

Everything below is the documented behaviour of Apache Kafka's consumer client, with
sources. Broker and client defaults change between releases — re-check against the
version you actually deploy before relying on a default.

## 1. Offset commit semantics

| Rule | Requirement |
|---|---|
| Committed value | The committed offset MUST be the offset of the **next** message to consume, i.e. `last_processed_offset + 1`. |
| Revocation commit | Offset commits performed in the revocation callback MUST be synchronous — the rebalance does not wait for an in-flight asynchronous commit. |
| Auto-commit | `enable.auto.commit` MUST be `false` on any consumer whose events drive order execution. |

> "The committed offset should be the next message your application will consume, i.e.
> `lastProcessedMessageOffset + 1`." — [KafkaConsumer javadoc, "Manual Offset Control"](https://kafka.apache.org/40/javadoc/org/apache/kafka/clients/consumer/KafkaConsumer.html)

Committing `last_processed_offset` instead is the classic off-by-one here: it is
accepted by the broker, looks correct in a dashboard, and causes the next owner of the
partition to reprocess exactly one message per partition on every rebalance.

## 2. Rebalance listener callbacks

`ConsumerRebalanceListener` has three callbacks, and they are not interchangeable.

| Callback | When | Commit offsets? |
|---|---|---|
| `onPartitionsRevoked` | The consumer is giving up partitions it still owns. Also fires on `close()` and `unsubscribe()`. | **Yes** — documented as recommended, "to prevent duplicate data". |
| `onPartitionsAssigned` | After reassignment completes, before fetching resumes; only as a result of a `poll()` call. | No. Seed state / look up offsets here. |
| `onPartitionsLost` | Exceptional loss of ownership — session timeout or fatal error. | **No.** The partitions are no longer owned; the docs state you "should not need to store the offsets since we know these partitions are no longer owned by the consumer at that time." |

Source: [ConsumerRebalanceListener javadoc](https://kafka.apache.org/40/javadoc/org/apache/kafka/clients/consumer/ConsumerRebalanceListener.html),
[KIP-429: Kafka Consumer Incremental Rebalance Protocol](https://cwiki.apache.org/confluence/display/KAFKA/KIP-429:+Kafka+Consumer+Incremental+Rebalance+Protocol).

### The `on_lost` routing trap (Python clients)

In `confluent-kafka-python`, `Consumer.subscribe()` accepts `on_assign`, `on_revoke`
and `on_lost`. Per the client documentation, `on_lost` provides "handling in the case
the partition assignment has been lost" — and **if it is not specified, lost partition
events are delivered to `on_revoke` instead**.

The consequence is specific: a handler that unconditionally commits in `on_revoke`, on
a deployment that never registered `on_lost`, will attempt to commit partitions another
member already owns. Register all three callbacks explicitly.

Source: [confluent-kafka-python API documentation](https://docs.confluent.io/platform/current/clients/confluent-kafka-python/html/index.html).

## 3. Eager vs cooperative rebalancing

| Protocol | Revocation behaviour |
|---|---|
| Eager (`RangeAssignor`, `RoundRobinAssignor`, `StickyAssignor`) | `onPartitionsRevoked` is invoked at the **start** of every rebalance with the consumer's **entire** assignment. All members stop processing — the "stop-the-world" rebalance. |
| Cooperative (`CooperativeStickyAssignor`, KIP-429) | Callbacks are invoked at the **end** of the rebalance, and only for the partitions actually **moving** to another member. Partitions the member keeps are never revoked. |

Since Kafka 3.0 the default `partition.assignment.strategy` is
`RangeAssignor, CooperativeStickyAssignor` — the *list*, with `RangeAssignor` first, so
a group only upgrades to cooperative once every member offers it.

**Implication for this skill:** never treat the partition list passed to a callback as
the member's full assignment. Activate and fence the partitions you were given; do not
replace the active set.

Kafka 4.0 makes KIP-848 (the broker-coordinated, fully incremental rebalance protocol)
generally available; consumers opt in with `group.protocol=consumer`. Under that
protocol `heartbeat.interval.ms`, `session.timeout.ms` and `partition.assignment.strategy`
are no longer consumer-side settings. The fence/drain/commit ordering in this skill is
unaffected — it is a property of the callbacks, not of how assignments are computed.

Sources: [KIP-848](https://cwiki.apache.org/confluence/display/KAFKA/KIP-848%3A+The+Next+Generation+of+the+Consumer+Rebalance+Protocol),
[Consumer Rebalance Protocol (Kafka 4.1 docs)](https://kafka.apache.org/41/operations/consumer-rebalance-protocol/),
[Apache Kafka 4.0.0 release announcement](https://kafka.apache.org/blog/2025/03/18/apache-kafka-4.0.0-release-announcement/).

## 4. Consumer configuration defaults that cause rebalances

Defaults as documented for Apache Kafka 4.1 ([Consumer Configs](https://kafka.apache.org/41/configuration/consumer-configs/)):

| Config | Default | Relevance |
|---|---|---|
| `enable.auto.commit` | `true` | Must be disabled. Commits on a timer regardless of executor progress. |
| `auto.commit.interval.ms` | `5000` | The window of work auto-commit can wrongly mark durable. |
| `max.poll.interval.ms` | `300000` (5 min) | "The maximum delay between invocations of `poll()` when using consumer group management." Exceed it and the member is evicted — the usual root cause of a rebalance storm. |
| `max.poll.records` | `500` | A batch that is slow per record can breach `max.poll.interval.ms`. Lower it before raising the interval. |
| `session.timeout.ms` | `45000` | Ownership loss without a graceful revocation → `onPartitionsLost`. |
| `heartbeat.interval.ms` | `3000` | Heartbeats run on a background thread and do **not** prove progress; only `poll()` does. |
| `partition.assignment.strategy` | `RangeAssignor, CooperativeStickyAssignor` | Determines which partitions reach the callbacks (see §3). |

> Heartbeats keep the member in the group while the processing thread is stuck. A worker
> that is heartbeating but not polling still gets evicted at `max.poll.interval.ms`.

## 5. Engineering standards enforced by this module

| Standard | Requirement |
|---|---|
| Fencing precedes I/O | All revoked partitions are marked inactive before any flush or commit, so a failure cannot leave a partition accepting work. |
| Drain before commit | Offsets are committed only after the partition's buffer flushed successfully. A failed flush blocks that partition's commit. |
| Failures surface | Flush and commit failures are aggregated into `OffsetCommitError`, never swallowed. The offsets are not durable and the work will be redelivered. |
| Monotonic offsets | A message at or below the partition high-water mark is rejected (`OffsetRegressionError`) rather than moving the commit pointer backwards. |
| Monotonic clock | The rebalance-rate window uses `time.monotonic()`; wall-clock steps must not fabricate or suppress storm alerts. |
| Bounded dedupe cache | The idempotency cache is capped and evicts oldest-first. It is process-local and is **not** a cross-worker duplicate guard. |
| Thread safety | Rebalance callbacks run on the poll thread while processing runs on a worker thread; all state is guarded by a re-entrant lock. |

## Category

`Infrastructure / Event-Driven Systems & Streaming` — see the top-level `mappings/`
directory for how categories roll up across the skill library.
