# Pre-Flight Checklist

## Topology — does the fanout mean what you think it means?

- [ ] Is there one consumer group per **independent** consumer (strategy, risk, logging), so each sees every tick?
- [ ] Does any group run more than one consumer? If so, is out-of-order, concurrent processing of a single symbol acceptable? (Consumers in a group split the stream; ordering survives inside the stream, not across consumers.)
- [ ] Is each worker's consumer name **unique and stable** across restarts? (Two processes sharing a name inherit each other's pending entries.)
- [ ] Was each group created with an explicit start position — `$` (new ticks only) or `0` (replay the backlog)? The wrong choice reports nothing rather than erroring.
- [ ] Is only `BUSYGROUP` swallowed on group creation, with connection/permission errors re-raised?

## Publish & trimming

- [ ] Is every tick validated before `XADD` (finite, positive price unless deliberately opted out; non-empty symbol; non-negative volume; positive timestamp)?
- [ ] Is `timestamp` the **venue** event time, not the ingest time or the stream ID's millisecond part?
- [ ] Is `MAXLEN` sized from peak ticks/sec x the **slowest** consumer's worst-case backlog, rather than inherited from the 100,000 default?
- [ ] Is `approximate=True` (`MAXLEN ~`) acceptable? It may leave a few tens more entries than the threshold.
- [ ] Is it understood that trimming (KEEPREF, the default) removes entries **regardless of unacknowledged PEL references** — a pending entry trimmed away is a lost tick?

## Consume & acknowledge

- [ ] Does the consumer read with `>` for new work, and once with `"0"` after a restart to drain its own pending entries?
- [ ] Is every processed entry acknowledged with `XACK`? (`>` never redelivers, so an unacknowledged entry only leaves the PEL via XACK or a claim.)
- [ ] Is the `XACK` return value checked? Fewer acknowledgements than IDs means someone else already claimed the entry — duplicate processing.
- [ ] Are undecodable entries surfaced (`TickBatch.malformed`) and dead-lettered deliberately, rather than silently dropped or defaulted to a zero-priced tick?
- [ ] Is the client's `decode_responses` setting known, and does the read path handle both RESP2 (`[[name, entries]]`) and RESP3 (`{name: [entries]}`) shapes?

## Recovery from a crashed worker

- [ ] Is there an actual recovery path (`XPENDING` → `XCLAIM`, or `XAUTOCLAIM`)? Without one, an un-acknowledged tick is never retried.
- [ ] Is the sweep re-run on a timer after the cursor returns `0-0` (entries become claimable as they age)?
- [ ] Is `min_idle_ms` longer than the longest **legitimate** processing pause (GC, slow write, brief partition)?
- [ ] Is every consumer idempotent? Claiming does not stop a merely-paused owner, and Redis calls multiple processing "possible and unavoidable in the general case".
- [ ] Is there a delivery-count ceiling (`find_poison_entries`) routing repeat offenders to a dead-letter path instead of the next worker?
- [ ] Are `deleted_ids` from a sweep alarmed? They are ticks trimmed before acknowledgement — unrecoverable.

## Operations & durability

- [ ] Are PEL depth, oldest pending idle time, max delivery count, `deleted_ids` count and stream length monitored per group?
- [ ] Is shutdown draining in-flight entries and acknowledging them before exit?
- [ ] Is it accepted that Redis replication is asynchronous, and that a failover can lose recent entries **and** the PEL/last-delivered-id state? Is there a separate archive of record?
- [ ] Are `maxmemory` and the eviction policy configured so the stream key cannot be evicted out from under the consumers?
- [ ] Have blocking reads, persistence, replication, failover and concurrency been tested against a **real** Redis, not the in-memory simulator?
