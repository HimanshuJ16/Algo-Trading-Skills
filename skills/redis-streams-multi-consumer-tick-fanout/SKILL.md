---
name: redis-streams-multi-consumer-tick-fanout
description: >-
  Use when one feed must reach several independent consumers with a bounded replay
  window. Covers Redis Streams consumer groups, XACK as the only thing that drains the
  pending entries list, and XAUTOCLAIM recovery of a crashed worker's backlog.
license: Apache-2.0
metadata:
  domain: algorithmic-trading
  subdomain: real-time-architecture
  tags: redis-streams, tick-fanout, consumer-group, xadd, xack, xclaim, xautoclaim, market-data-pipeline
  brokers_frameworks: "Redis Streams (XADD/XREADGROUP/XACK/XCLAIM/XAUTOCLAIM/XPENDING); redis-py; Python Dataclasses"
  version: "2.0.0"
  author: algo-trading-skills-contributors
---

## When to Use

Use this skill when one market-data feed has to reach several *independent* consumers — a strategy engine, a risk monitor, a tick logger — and each of them must see every tick, with a bounded replay window if a consumer restarts. That is exactly what a Redis Stream with several consumer groups gives you: each group has its own last-delivered-id and its own Pending Entries List (PEL), so groups fan out; consumers *inside* one group split the work between them.

The engine wraps `XADD`, `XGROUP CREATE`, `XREADGROUP`, `XACK`, `XCLAIM`, `XAUTOCLAIM` and `XPENDING` behind validated Python calls, using the real `redis-py` signatures, and ships an in-memory simulator (`MockRedisStreamEngine`) that reproduces the documented consumer-group semantics so the fanout, acknowledgement and recovery logic can be tested without a server.

Two properties decide whether Redis Streams fits at all, and both are documented behaviour rather than tuning:

1. **Delivery is at-least-once, never exactly-once.** `XCLAIM` re-delivers, and a crash between processing and `XACK` guarantees a second delivery. Every consumer must be idempotent.
2. **Ordering is per stream, not per consumer.** Entries are reported "in the same order they were added by `XADD`", but a group with two consumers hands one of them A and C and the other B. Two workers in one group therefore process one symbol's ticks concurrently and out of order.

## When NOT to Use

- **When per-symbol ordering must survive horizontal scaling.** One consumer per group preserves order and caps throughput at one worker; more than one does not. If you need both ordering and parallelism, shard by symbol across *separate streams* (one consumer group each), or use a keyed, partitioned log — see `kafka-based-tick-distribution-at-scale`.
- **As a durable system of record.** Redis replication is asynchronous by default, and Redis' own documentation states that even with `WAIT`, "acknowledged writes can still be lost during a failover, depending on the exact configuration of the Redis persistence". Stream entries, last-delivered-ids, PEL contents and delivery counters are all ordinary Redis data and share that fate. Archive ticks separately (`historical-tick-data-storage-and-compaction`).
- **When the consumer can fall further behind than the stream is long.** `MAXLEN` trimming defaults to `KEEPREF`: entries leave the stream while their PEL references remain. A pending entry whose payload has been trimmed away cannot be claimed — Redis 7.0+ simply deletes it from the PEL — so the tick is lost, silently, unless you monitor for it.
- **On the critical path of a latency-sensitive strategy without measuring it.** This is a networked broker hop with fsync/replication behaviour of its own; it is a fanout bus, not a shared-memory ring (`memory-mapped-ring-buffer-for-ultra-low-latency`).
- **With the shipped numbers unchanged.** `DEFAULT_MAXLEN = 100_000` and every idle threshold in the examples are engineering starting points. No Redis, exchange or regulatory document mandates them.

## Prerequisites

- A Redis server (5.0+ for consumer groups, 6.2+ for `XAUTOCLAIM`, 7.0+ for the PEL cleanup of trimmed entries) and `redis-py`, or the bundled `MockRedisStreamEngine` for tests.
- Tick payload: `symbol`, `last_price`, `volume`, `timestamp` (venue event time in Unix seconds — Redis stream IDs carry the *Redis server's* clock, not the venue's).
- One consumer group name per independent consumer, and a stable, unique consumer name per worker process. Reusing a name across processes silently merges two workers' pending lists.
- A decision on `decode_responses`. `redis-py` returns `bytes` unless the client sets it; the manager decodes either way, but anything else reading those replies must agree.

## Workflow

1. **Create each group with an explicit start position**:
   - `create_consumer_group(name, start_id="$")` — redis-py's default — means the group sees only ticks published *after* creation. Pass `"0"` to replay what is still in the stream. Getting this wrong is silent: the group simply reports nothing until the next tick.
   - Only `BUSYGROUP` is treated as "already exists". A connection or permission failure is re-raised, because swallowing it starts consumption against a group that does not exist.
2. **Publish with a cap you chose deliberately**:
   - `publish_tick(tick)` validates before writing: a non-finite or non-positive price, an empty symbol or a negative volume raises rather than entering the stream. (Set `allow_non_positive_price=True` for instruments that legitimately quote at or below zero — calendar spreads, or CL futures on 2020-04-20.)
   - `approximate_trim=True` emits `MAXLEN ~ n`, which Redis documents as leaving "a few tens more" entries than the threshold. Use `approximate_trim=False` only when the cap must be exact, and accept the trimming cost.
3. **Consume with `>` and treat the batch as two lists**:
   - `consume_batch(group, consumer)` returns decodable `ticks` *and* `malformed` entries. A malformed entry is still in the PEL: dead-letter it deliberately (log the ID, `XACK` it) rather than letting it be re-claimed forever.
   - After a restart, call once with `start_id="0"` to re-read this consumer's own pending entries, then switch back to `">"` — the recovery loop the XREADGROUP reference describes.
4. **Acknowledge, and check what the ACK returned**:
   - `acknowledge_ticks(group, *ids)` returns how many entries left the PEL. A 0 on a first-time ACK is information, not noise: the entry had already been claimed by another consumer, which means it is being processed twice.
5. **Recover a crashed worker's backlog — discover, then claim**:
   - `pending_summary(group, min_idle_ms=...)` (XPENDING) is how you find the stale IDs. You cannot claim what you have not discovered; `claim_stale_ticks` takes explicit IDs by design.
   - `recover_stale_ticks(...)` (XAUTOCLAIM) sweeps instead. Loop on `result.cursor` until it is `"0-0"`, then keep sweeping on a timer: entries that were not idle enough this pass become claimable later.
   - `result.deleted_ids` are pending entries whose payload was trimmed out of the stream. Those ticks are unrecoverable, and a non-zero count means trimming is outrunning consumption — alarm on it.
   - Claiming resets idle time, so two racing claimers cannot both win. It does **not** stop the previous owner: a worker that was paused rather than dead may still be mid-processing the same tick.
6. **Break the reclaim loop on poison entries**:
   - `XCLAIM` increments the delivery counter. `find_poison_entries(group, max_delivery_count=N)` surfaces entries that keep being re-claimed; route those to a dead-letter path instead of handing them to the next worker to crash.

> Full procedure: see `references/workflows.md`.
> Standards reference: see `references/standards.md`.
> Printable pre-flight checklist: see `assets/checklist.md`.

## Common Pitfalls

- **Assuming "not acknowledged" means "will be redelivered".** It does not. `>` returns only entries never delivered to the group, so an un-ACKed entry sits in the PEL untouched until somebody claims it. A consumer loop with no XCLAIM/XAUTOCLAIM path does not retry — it leaks.
- **Treating a consumer group as an ordered per-symbol channel.** Scale a group to two workers and one instrument's ticks are processed concurrently, out of order, by both. Ordering survives inside a stream, not across consumers.
- **Trimming and pending entries fighting each other.** `MAXLEN` (KEEPREF, the default) removes entries whether or not a group still owes an ACK on them. The PEL then holds IDs whose payload is gone: XCLAIM refuses them and deletes them from the PEL, XAUTOCLAIM reports them as deleted, and the tick is simply lost. Cap the stream to the *slowest* consumer's worst-case backlog, not to the fastest.
- **Decoding a null payload into a tick.** A trimmed-but-pending entry reads back with no fields. Defaulting that to `symbol="" last_price=0.0` hands a risk monitor a fabricated zero-priced print; decoding must raise instead.
- **Ignoring `decode_responses`.** Without it `redis-py` returns `bytes`, so `fields.get("symbol")` misses on every entry — the same fabricated-zero-tick failure, from the client side.
- **Assuming one XREADGROUP reply shape.** `redis-py` returns `[[name, entries], ...]` under RESP2 and `{name: [entries]}` under RESP3 (`protocol=3`). Code written for one reads zero ticks under the other, without an error.
- **Reusing a consumer name across processes.** The PEL is keyed by consumer name. Two processes sharing one name inherit each other's pending entries, and a "crash recovery" sweep hands live work to a second worker.
- **Claiming with an idle threshold shorter than a normal processing pause.** A GC pause, a slow database write or a brief network partition all look exactly like a crash. Too short a threshold manufactures duplicate processing; too long delays recovery. Both are real costs — pick the point deliberately.
- **Re-claiming a poison entry forever.** An entry that crashes whoever touches it is idle again the moment the claimer dies. Without a delivery-count ceiling, the group re-processes it until someone notices.
- **Treating the stream as durable storage.** Replication is asynchronous; a failover can lose recently published entries *and* the PEL/last-delivered-id state that would have let you recover them.

## Verification

- Publish one tick, consume it from two groups $\implies$ both receive it (fanout). Consume 3 ticks with two consumers in *one* group $\implies$ they split 2/1, never both getting all three.
- ACK a tick, then read again with `>` $\implies$ nothing is returned, from that consumer or any other. Read again *without* ACKing $\implies$ still nothing, because `>` means never-delivered.
- Read with `start_id="0"` $\implies$ the reader's own pending entries come back; another consumer's do not.
- Create a group with `start_id="$"` after publishing $\implies$ the backlog is skipped; with `"0"` $\implies$ it is replayed.
- Consume without ACK, advance the clock 29,999 ms against `min_idle_ms=30_000` $\implies$ no claim; at exactly 30,000 ms $\implies$ claimed, owner changes, idle resets to 0 and the delivery count goes 1 → 2. Claim twice in a row $\implies$ only the first succeeds.
- Trim a still-pending entry out of the stream $\implies$ `claim_stale_ticks` returns nothing and the ID leaves the PEL; `recover_stale_ticks` reports it in `deleted_ids`; a history read yields a `malformed` entry, never a zero-priced tick.
- Re-claim one entry three times $\implies$ `find_poison_entries(max_delivery_count=3)` flags it, `max_delivery_count=10` does not.
- Feed the manager a RESP3 map reply and a byte-valued RESP2 reply $\implies$ both decode to the same tick.
- Run `python -m unittest discover -s skills/redis-streams-multi-consumer-tick-fanout/scripts`.

## Related Skills

- `kafka-based-tick-distribution-at-scale`
- `producer-consumer-tick-pipeline`
- `consumer-group-rebalance-safety`
- `backpressure-drop-degrade-policy`
- `graceful-shutdown-draining-in-flight-ticks`
- `historical-tick-data-storage-and-compaction`
