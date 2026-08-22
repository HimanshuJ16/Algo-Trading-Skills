# Workflows for Consumer Group Rebalance Safety

The guard in `scripts/rebalance_guard.py` owns the ordering; your consumer loop owns the
I/O. Wire the two together as below.

## 0. Wiring

```python
guard = ConsumerGroupRebalanceGuard(
    commit_fn=lambda offsets: consumer.commit(
        offsets=[TopicPartition(TOPIC, p, o) for p, o in offsets.items()],
        asynchronous=False,          # synchronous: the rebalance will not wait otherwise
    ),
    flush_fn=lambda partition, messages: executor.drain(partition, messages),
)

consumer.subscribe(
    [TOPIC],
    on_assign=lambda c, tps: guard.on_partitions_assigned([tp.partition for tp in tps]),
    on_revoke=lambda c, tps: guard.on_partitions_revoked([tp.partition for tp in tps]),
    on_lost=lambda c, tps: guard.on_partitions_lost([tp.partition for tp in tps]),
)
```

Register **all three** callbacks. Omitting `on_lost` makes the client deliver lost
partitions to `on_revoke`, where the commit path will try to commit partitions another
member already owns (see `references/standards.md` §2).

Set `enable.auto.commit=false`. It defaults to `true`.

## 1. Partition assignment

- `guard.on_partitions_assigned(partitions)` activates the partitions and seeds buffers.
- Under cooperative rebalancing the argument holds only the **newly added** partitions.
  Activate them; never reset the active set from this list.
- The return value is the storm flag — `True` means the group is churning.

## 2. Message processing

- `guard.process_message(StreamMessage(partition, offset, idempotency_key, payload))`.
- Rejections, in the order they are checked:
  - `ValueError` — structurally invalid record (negative partition/offset, blank key).
  - `PartitionRevokedException` — the partition is fenced. Drop the message; the new
    owner will process it. **Do not** retry it locally.
  - `DuplicateMessageException` — this worker already processed that key. Expected under
    at-least-once delivery; log at DEBUG and continue, do not crash the loop.
  - `OffsetRegressionError` (a subclass of the above) — the offset is at or below the
    partition high-water mark, so the consumer was re-fed from a stale position.
    Investigate: this usually means a manual `seek()` or a misconfigured replay.
- On success the message is buffered for the flush and the partition's high-water mark
  advances.

## 3. Revocation protocol — fence, drain, commit

`guard.on_partitions_revoked(partitions)` runs, in this order:

1. **Fence all** — every listed partition is marked inactive before any I/O, so a later
   failure cannot leave a partition still accepting work.
2. **Drain each** — `flush_fn(partition, buffered)` runs synchronously. A partition whose
   flush raises is recorded as failed and is **not** committed: its work never reached
   the executor, so it must be redelivered.
3. **Commit** — `commit_fn({partition: last_processed_offset + 1})`, once, synchronously,
   for every partition that flushed cleanly and has progress to record. The `+ 1` is
   mandatory: Kafka's committed offset is the offset of the *next* message to consume.
4. **Prune** — per-partition buffers and high-water marks are discarded.

If anything failed, `OffsetCommitError` is raised **after** every partition has been
fenced and pruned; `error.failures` maps partition to reason. Treat it as an incident:
those offsets are not durable and that work will be redelivered elsewhere.

**Catch it inside the callback.** An exception that escapes a rebalance listener
propagates into the client's rebalance handling and out of `poll()`, which is not where
you want to discover a commit failure. Wrap the call, route the failure to your alerting
or kill switch, and let the rebalance finish:

```python
def _on_revoke(consumer, tps):
    try:
        guard.on_partitions_revoked([tp.partition for tp in tps])
    except OffsetCommitError as exc:
        alerting.page("revocation commit failed", failures=exc.failures)
```

Note the guard commits progress even when the in-flight buffer is empty — a partition
whose work was already drained still has an uncommitted high-water mark.

## 4. Loss protocol — fence and discard

`guard.on_partitions_lost(partitions)` fences the partitions and discards their buffers
**without flushing and without committing**. Ownership is already gone; a commit here is
at best rejected and at worst overwrites the new owner's progress. The buffered work will
be redelivered to whoever holds the partitions now.

## 5. Rebalance storm handling

- `guard.is_rebalance_storm()` returns `True` while the rolling window (default 60s)
  holds at least the threshold count (default 3) of rebalances. Each lifecycle callback
  also returns the same flag.
- A revoke followed by an assign counts as **one** rebalance — under the eager protocol a
  single rebalance fires both callbacks.
- The window is measured on `time.monotonic()`, so an NTP correction cannot fabricate or
  suppress an alert.
- On a storm, degrade rather than continue at full size: stop originating new orders,
  widen quotes, page the on-call. Then find the cause — most storms are poll starvation
  (`max.poll.interval.ms`, default 300000 ms) rather than broker instability. Reduce
  `max.poll.records` before raising `max.poll.interval.ms`.

## 6. Cross-worker duplicate protection

The guard's idempotency cache is in-process and bounded. It stops *this* worker
re-executing a redelivery; it does nothing about the worker that takes the partition
over. If a duplicate order submission is unacceptable, back the dedupe with one of:

- broker-side idempotency / Kafka transactions (`read_committed` + transactional producer),
- a shared dedupe store keyed on the order ID, or
- broker-side client order ID idempotency at the point of execution
  (see the `order-placement-idempotency` skill).
