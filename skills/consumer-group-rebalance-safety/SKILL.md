---
name: consumer-group-rebalance-safety
description: >-
  Use when trading workers consume order or tick events from a Kafka consumer group and
  a rebalance can move a partition mid-flight. Fences revoked partitions, drains
  in-flight work, and commits the next offset rather than the last processed one.
license: Apache-2.0
metadata:
  domain: algorithmic-trading
  subdomain: real-time-architecture
  tags: kafka, consumer-group, rebalance-safety, event-driven, idempotency, zombie-consumer, offset-management, streaming
  brokers_frameworks: "Apache Kafka / Redpanda; confluent-kafka-python; Generic Event Stream"
  version: "2.0.0"
  author: algo-trading-skills-contributors
---

## When to Use

Use this skill when worker nodes in an event-driven trading architecture consume from
a Kafka (or Redpanda) **consumer group** and the events drive side effects that must
not happen twice — order submissions, position updates, fills applied to a book.

A rebalance moves partition ownership between members. Between the moment a worker
stops owning a partition and the moment it notices, two failure modes are live:

- **Zombie execution** — the worker keeps submitting orders for a partition another
  member already owns.
- **Duplicate execution** — the new owner replays events the old owner processed but
  never committed.

Both are ordinary consequences of at-least-once delivery, and both are decided by what
happens inside `on_partitions_revoked` / `on_partitions_assigned` / `on_partitions_lost`.
`scripts/rebalance_guard.py` is the state machine that makes that ordering explicit.

## When NOT to Use

- **As a Kafka client.** The guard performs no network I/O. It calls back into
  *your* `commit_fn` and `flush_fn`; you still wire it to `consumer.commit(asynchronous=False)`
  and your executor. Constructed without a `commit_fn`, it fences and drains but
  commits nothing, and logs a warning saying so.
- **As cross-worker deduplication.** `processed_idempotency_keys` is an in-process
  bounded LRU. It stops *this* worker re-executing a redelivery. It does nothing about
  the worker that takes the partition over after a rebalance — which is the headline
  scenario. Cross-worker safety needs broker-side idempotency, a transactional
  read-process-write, or a shared dedupe store keyed on the order ID.
- **When you are not using consumer groups.** Manual `assign()` with no group
  management has no rebalance to guard.
- **For exactly-once semantics.** This is at-least-once made survivable. Exactly-once
  requires Kafka transactions (`isolation.level=read_committed` plus a transactional
  producer), which this module does not implement.

## Prerequisites

- `enable.auto.commit=false`. It defaults to **true** with a 5s interval, which commits
  offsets on a timer with no knowledge of whether your executor finished the batch.
- A rebalance listener registered for **all three** callbacks. In
  `confluent-kafka-python`, `subscribe()` takes `on_assign`, `on_revoke` and `on_lost`;
  if `on_lost` is not supplied, **lost-partition events are delivered to `on_revoke`
  instead** — so a commit-on-revoke handler will try to commit partitions it no longer
  owns.
- A stable application-level idempotency key per event (`order_id` / `event_id`).
- Whether your group runs the **eager** or **cooperative** protocol, because it changes
  which partitions arrive in the callback (see `references/standards.md`).

## Workflow

1. **Assignment (`on_partitions_assigned`).** Activate the partitions and seed empty
   buffers. Under cooperative rebalancing this fires only for *newly* added partitions,
   so never treat the argument as the full assignment — activate, don't replace.

2. **Processing (`process_message`).** Checks run **fence first**, then duplicate key,
   then offset monotonicity. Fence-first matters: a revoked partition must be rejected
   even when the message would also have failed the duplicate check, because the
   rejection reason is what the caller logs and acts on.

   A non-increasing offset is rejected as `OffsetRegressionError`. Kafka offsets increase
   strictly within a partition, so an offset at or below the high-water mark means the
   consumer was re-fed from a stale position; accepting it would drag the commit pointer
   backwards and replay everything after it.

3. **Revocation (`on_partitions_revoked`) — fence, then drain, then commit, in that
   order.** All partitions are fenced *before* any I/O, so a flush or commit failure can
   never leave a partition still accepting work. Then per partition: flush the buffer to
   the executor, and only if that succeeds, commit. A partition whose flush fails is
   **not** committed — its work never reached the executor, so it must be redelivered.
   Failures are aggregated and raised as `OffsetCommitError` after every partition has
   been fenced and pruned.

4. **Commit the *next* offset.** The guard commits `last_processed_offset + 1`. Kafka's
   committed offset is the offset of the next message to consume; committing the last
   processed offset itself makes the new owner replay that message — one duplicate order
   per partition per rebalance, which is exactly the bug this skill exists to prevent.

5. **Loss (`on_partitions_lost`) — fence and discard, do not commit.** This fires when
   ownership was already lost (session timeout, `max.poll.interval.ms` overrun, fatal
   error). Another member may already own the partitions, so committing is at best
   rejected and at worst overwrites the new owner's progress. Buffered work is dropped;
   it will be redelivered there.

6. **Storm detection.** `is_rebalance_storm()` returns a value rather than only logging,
   so the caller can degrade — pause new orders, widen quotes, page — instead of scraping
   logs. A revoke followed by an assign is counted as **one** rebalance: under the eager
   protocol a single rebalance fires both, and counting both doubles the apparent rate.

> Full step-by-step procedure: see `references/workflows.md`.
> Protocol semantics, config defaults and sources: see `references/standards.md`.
> Printable pre-flight checklist: see `assets/checklist.md`.

## Common Pitfalls

- **Committing the last processed offset instead of `+ 1`.** The single highest-value
  line in this module. It looks correct, it passes casual tests, and it replays one
  message per partition on every rebalance forever.
- **Committing in the lost-partition path.** Another member owns those partitions. The
  trap is not writing the bad code deliberately — it is registering only `on_revoke`
  and having the client route lost partitions into it.
- **Committing asynchronously during revocation.** The rebalance completes without
  waiting for the in-flight commit; the new owner starts from the older committed
  offset and reprocesses. Revocation commits must be synchronous.
- **Committing before the flush completes.** Marks work durable that never reached the
  executor — silent data loss, the opposite failure from duplication and much harder to
  notice.
- **Leaving `enable.auto.commit=true`.** The default. It commits on a 5s timer with no
  knowledge of your batch's progress, and can commit offsets for events the executor
  never finished.
- **Trusting an in-process dedupe cache to prevent duplicates across a rebalance.** The
  new owner is a different process with an empty cache. An unbounded cache is also a
  slow memory leak in a consumer that runs for weeks.
- **Treating the fence as a barrier.** `process_message` checks the fence on entry; a
  message admitted a microsecond earlier is still in flight. That is why revocation
  drains rather than assuming the worker is idle.
- **Assuming the revoked set is the full assignment.** Under cooperative rebalancing
  the callback receives only the partitions actually moving.
- **Measuring the storm window on `time.time()`.** An NTP correction or VM resume steps
  the wall clock and fabricates or suppresses alerts. Use a monotonic clock.
- **Blaming the rebalance instead of its cause.** Repeated rebalances usually mean poll
  starvation: processing a batch takes longer than `max.poll.interval.ms` (default 5
  minutes, `max.poll.records` default 500), the member is evicted, and its partitions
  are reassigned mid-flight.

## Verification

- Run the unit suite:
  `python -m unittest discover -s skills/consumer-group-rebalance-safety/scripts`
- Assert the committed offset is `last_processed + 1`, not `last_processed`. Process
  offset 100, revoke, and confirm the commit callback received `{partition: 101}`.
- Assert `on_partitions_lost` performs **no** commit and **no** flush, yet still fences
  the partition and discards its buffer.
- Assert a failing `flush_fn` prevents that partition's commit while unaffected
  partitions still commit, and that `OffsetCommitError` names only the failed ones.
- Assert every partition is fenced even when the commit raises.
- Assert revocation commits progress even when the in-flight buffer is already empty.
- Assert a revoke-then-assign pair counts as one rebalance, not two.
- Assert the fence holds against concurrent producer threads and that nothing is
  appended to a buffer after revocation discarded it.

## Related Skills

- `order-placement-idempotency`
- `producer-consumer-tick-pipeline`
- `graceful-shutdown-draining-in-flight-ticks`
- `redis-streams-multi-consumer-tick-fanout`
- `sequence-number-gap-detection-for-feeds`
