# Pre-Flight Checklist: consumer-group-rebalance-safety

Use during design review or PR approval for any consumer group whose events drive order
execution or position state.

## Consumer configuration
- [ ] Is `enable.auto.commit` explicitly set to `false`? (It defaults to **true**.)
- [ ] Are `on_assign`, `on_revoke` **and `on_lost`** all registered? Without `on_lost`,
      the client routes lost partitions into `on_revoke`, where the commit path will try
      to commit partitions another member already owns.
- [ ] Is it known whether the group runs the eager or cooperative protocol, and does the
      code avoid treating a callback's partition list as the full assignment?
- [ ] Are `max.poll.interval.ms` (default 300000) and `max.poll.records` (default 500)
      sized against the actual worst-case per-batch processing time?

## Offset commits
- [ ] Is the committed value `last_processed_offset + 1`, never `last_processed_offset`?
- [ ] Is the revocation commit **synchronous**? An async commit may not land before the
      rebalance completes.
- [ ] Does the buffer flush complete **before** the commit, so a failed flush cannot mark
      unexecuted work durable?
- [ ] Is a commit or flush failure surfaced (raised/alerted) rather than logged and
      swallowed?
- [ ] Is progress committed on revocation even when the in-flight buffer is empty?
- [ ] Does the lost-partition path commit **nothing**?

## Fencing
- [ ] Are all revoked partitions fenced **before** any flush or commit I/O?
- [ ] Does processing on a fenced partition raise rather than silently no-op?
- [ ] Is the fence checked before the duplicate check, so a revoked partition can never
      execute?
- [ ] Is it understood that the fence is a check-on-entry, not a barrier — and is that
      why revocation drains instead of assuming the worker is idle?
- [ ] Is shared state lock-guarded, given callbacks run on the poll thread and processing
      on a worker thread?

## Idempotency
- [ ] Does every event carry a stable application-level key (`order_id` / `event_id`)?
- [ ] Is the dedupe cache **bounded**? An unbounded set leaks in a consumer that runs for
      weeks.
- [ ] Is it documented and understood that this cache is **process-local** and gives no
      protection once a partition moves to another worker?
- [ ] Is cross-worker duplicate protection provided by Kafka transactions, a shared dedupe
      store, or broker-side client-order-ID idempotency?
- [ ] Is a non-increasing offset rejected rather than allowed to move the commit pointer
      backwards?

## Observability & tests
- [ ] Is rebalance frequency exposed as a **value** (not just a log line) so the system can
      degrade automatically?
- [ ] Is the rebalance window measured on a monotonic clock?
- [ ] Is a revoke+assign pair counted as one rebalance, not two?
- [ ] Do tests assert the committed offset is `last + 1`, that `on_partitions_lost`
      commits nothing, and that a failed flush blocks only its own partition's commit?
- [ ] Do 100% of unit tests pass under
      `python -m unittest discover -s skills/consumer-group-rebalance-safety/scripts`?
