# Standards & Sources for Redis Streams Multi-Consumer Tick Fanout

## What Redis actually documents

These are documented server behaviours, not this skill's choices. They constrain what any consumer-group fanout can and cannot guarantee. Quotes verified against the Redis command reference, 2026-08.

| Area | Documented behaviour | Source |
|---|---|---|
| `>` vs any other ID | "When the special `>` id is specified without `CLAIM`, the consumer wants to receive only messages that were never delivered to any other consumer, hence, just new messages." "Any other ID, that is, 0 or any other valid ID or incomplete ID (just the millisecond time part), will have the effect of returning entries that are pending for the consumer sending the command with IDs greater than the one provided." | [XREADGROUP](https://redis.io/docs/latest/commands/xreadgroup/) |
| PEL and XACK | "the message will be stored inside the consumer group in what is called a Pending Entries List (PEL) … The client will have to acknowledge the message processing using `XACK` in order for the pending entry to be removed from the PEL." | [XREADGROUP](https://redis.io/docs/latest/commands/xreadgroup/) |
| Load balancing inside a group | "If, for instance, the stream gets the new entries A, B, and C and there are two consumers reading via a consumer group, one client will get, for instance, the messages A and C, and the other the message B, and so forth." | [XREADGROUP](https://redis.io/docs/latest/commands/xreadgroup/) |
| Ordering | "Within each stream, entries are reported in the same order they were added by `XADD` (older first)." | [XREADGROUP](https://redis.io/docs/latest/commands/xreadgroup/) |
| Claim gating and the single winner | "the message is claimed only if its idle time is greater than the minimum idle time we specify when calling `XCLAIM`. Because as a side effect `XCLAIM` will also reset the idle time … two consumers trying to claim a message at the same time will never both succeed: only one will successfully claim the message. This avoids that we process a given message multiple times in a trivial way (yet multiple processing is possible and unavoidable in the general case)." | [XCLAIM](https://redis.io/docs/latest/commands/xclaim/) |
| Delivery counter | "as a side effect, `XCLAIM` will increment the count of attempted deliveries of the message unless the `JUSTID` option has been specified … In this way messages that cannot be processed for some reason, for instance because the consumers crash attempting to process them, will start to have a larger counter and can be detected inside the system." | [XCLAIM](https://redis.io/docs/latest/commands/xclaim/) |
| Claiming a deleted entry | XCLAIM will not claim a message when "The message exists in the group PEL but not in the stream itself (i.e. the message was read but never acknowledged, and then was deleted from the stream, either by trimming or by `XDEL`)… In the latter case, the message will also be deleted from the PEL in which it was found. This feature was introduced in Redis 7.0." | [XCLAIM](https://redis.io/docs/latest/commands/xclaim/) |
| Null payloads | "the PELs retain the deleted entries' IDs, but the actual entry payload is no longer available. Therefore, when reading such PEL entries, Redis will return a null value in place of their respective data." | [XREADGROUP](https://redis.io/docs/latest/commands/xreadgroup/) |
| XAUTOCLAIM sweep and cursor | "It also returns a stream ID intended for cursor-like use as the `start` argument for its subsequent call. When there are no remaining PEL entries, the command returns the special `0-0` ID to signal completion. However, note that you may want to continue calling `XAUTOCLAIM` even after the scan is complete … because enough time passed, so older pending entries may now be eligible for claiming." Third reply element: "message IDs that no longer exist in the stream, and were deleted from the PEL in which they were found." | [XAUTOCLAIM](https://redis.io/docs/latest/commands/xautoclaim/) |
| Trimming vs consumer groups | `KEEPREF` (the XADD/XTRIM default): "removes entries from the stream according to the specified strategy (`MAXLEN` or `MINID`), regardless of whether they are referenced by any consumer groups, but preserves existing references to these entries in all consumer groups' PEL". `DELREF` also drops the PEL references; `ACKED` "only removes entries that were read and acknowledged by all consumer groups". | [XADD](https://redis.io/docs/latest/commands/xadd/) |
| Exact vs approximate MAXLEN | "`=`: Exact trimming (default) - trims to the exact threshold; `~`: Approximate trimming - more efficient, may leave slightly more entries than the threshold." `XADD mystream MAXLEN ~ 1000 *` "adds a new entry but also evicts old entries so that the stream contains only 1000 entries, or at most a few tens more." | [XADD](https://redis.io/docs/latest/commands/xadd/) |
| Stream ID monotonicity | "Redis guarantees that IDs are always incremental: the ID of any entry you insert will be greater than any previous ID… To guarantee this property, if the current top ID in the stream has a time greater than the current local time of the instance, Redis uses the top entry time instead and increments the sequence part of the ID. This may happen when, for instance, the local clock jumps backward, or after a failover." IDs are `<ms>-<seq>`, both 64-bit integers. | [XADD](https://redis.io/docs/latest/commands/xadd/) |
| Durability | "Redis uses by default asynchronous replication… Synchronous replication of certain data can be requested by the clients using the `WAIT` command. However `WAIT` is only able to ensure there are the specified number of acknowledged copies in the other Redis instances, it does not turn a set of Redis instances into a CP system with strong consistency: acknowledged writes can still be lost during a failover, depending on the exact configuration of the Redis persistence." | [Redis replication](https://redis.io/docs/latest/operate/oss_and_stack/management/replication/) |

**Documentation ambiguity worth knowing**: XCLAIM's argument reference defines `min-idle-time` as "Claim only messages that have been idle for **at least** this long", while XAUTOCLAIM's prose says it "filters out entries having an idle time **less than or equal to** min-idle-time". The two readings differ only at the exact boundary. Do not build logic that depends on the boundary case; choose a threshold with margin. This module (and its simulator) claim at `idle >= min_idle_time` for both commands.

## redis-py client surface this module targets

Signatures taken from `redis/commands/core.py` on `redis/redis-py` (master, read 2026-08). Getting these wrong is not a style issue — passing a stream *name* where `xreadgroup` wants a mapping fails against a real client.

| Method | Signature | Note |
|---|---|---|
| `xadd` | `xadd(name, fields, id="*", maxlen=None, approximate=True, nomkstream=False, minid=None, limit=None, ref_policy=None, ...)` | `approximate` defaults to **True** → `MAXLEN ~ n`. |
| `xgroup_create` | `xgroup_create(name, groupname, id="$", mkstream=False, entries_read=None)` | `id` defaults to `$` — new messages only. |
| `xreadgroup` | `xreadgroup(groupname, consumername, streams: Dict[key, id], count=None, block=None, noack=False, claim_min_idle_time=None)` | `streams` is a **dict**, not a name. |
| `xack` | `xack(name, groupname, *ids)` | Returns the number removed from the PEL. |
| `xclaim` | `xclaim(name, groupname, consumername, min_idle_time, message_ids, idle=None, time=None, retrycount=None, force=False, justid=False)` | `min_idle_time` in milliseconds. |
| `xautoclaim` | `xautoclaim(name, groupname, consumername, min_idle_time, start_id="0-0", count=None, justid=False)` | Returns `[cursor, entries, deleted_ids]`. |
| `xpending_range` | `xpending_range(name, groupname, min, max, count, consumername=None, idle=None)` | Rows: `message_id`, `consumer`, `time_since_delivered`, `times_delivered`. |

Reply shapes: RESP2 parses to `[[stream_name, [(id, {field: value}), ...]], ...]`; RESP3 (`protocol=3`) parses to `{stream_name: [[(id, {field: value}), ...]]}`. Values are `bytes` unless the client is constructed with `decode_responses=True`. A pending-but-deleted entry parses to an **empty field dict**, not to an absent entry.

## This skill's engineering rules

Everything below is an engineering choice made by this skill. **None of it is published by Redis, an exchange, or a regulator.**

| Rule | Requirement | Why |
|---|---|---|
| Explicit group start | `create_consumer_group` MUST take an explicit `start_id`, and the caller MUST decide between `$` and `0`. | The default (`$`) silently skips the backlog; the failure looks like "no data" rather than an error. |
| Error classification | Only `BUSYGROUP` MAY be swallowed on group creation. | Swallowing a connection error starts consumption against a group that does not exist. |
| Payload validation on publish | A non-finite, non-positive (unless opted in) price, an empty symbol, a negative volume or a non-positive timestamp MUST raise before `XADD`. | A stream is a fanout bus: one bad tick is delivered to every group, and each one has to defend itself separately. |
| Decode failures are surfaced, never defaulted | A missing or unparseable field MUST raise `TickDecodeError`; the caller receives it in `TickBatch.malformed`. | A null payload defaulted to `symbol="" last_price=0.0` is a fabricated print, and it reaches the risk monitor looking valid. |
| Stream ID comparison | Stream IDs MUST be compared as `(ms, seq)` integer pairs. | Lexicographically `"5-10" < "5-9"`, which silently reorders or skips entries. |
| Reply-shape agnosticism | Read paths MUST accept RESP2 and RESP3 shapes and byte-valued fields. | Otherwise a client protocol change reads zero ticks with no error. |
| Discovery before claiming | Stale IDs MUST come from `XPENDING`/`XAUTOCLAIM`, not from an application-side guess. | You cannot claim what you have not discovered; guessing IDs hides an unbounded PEL. |
| Poison ceiling | A delivery-count ceiling MUST exist before an automated reclaim loop is enabled. | `XCLAIM` increments the counter precisely so a message that kills its consumers can be detected; without a ceiling the group reclaims it forever. |
| Trimmed-pending accounting | `deleted_ids` from an XAUTOCLAIM sweep MUST be logged/alarmed, never discarded. | It is the only signal that trimming is outrunning consumption and ticks are being lost. |
| Idempotent consumers | Consumers MUST tolerate duplicate delivery. | Redis documents multiple processing as "possible and unavoidable in the general case"; claiming does not stop a merely-paused owner. |
| Simulator honesty | The simulator MUST NOT be presented as reproducing approximate trimming, blocking reads, persistence, replication or concurrency. | A test that passes against a simulator with the wrong semantics is worse than no test. |

## Tunable defaults (calibrate, do not inherit)

| Parameter | Default | Status |
|---|---|---|
| `DEFAULT_MAXLEN` | `100_000` | Engineering starting point. The correct value is (peak ticks/sec) x (slowest consumer's worst-case backlog window), with margin. Nothing mandates 100,000. |
| `approximate_trim` | `True` (`MAXLEN ~`) | Matches redis-py's default and Redis' efficiency advice. Set `False` only when the cap must be exact. |
| `min_idle_ms` for claiming | none — caller supplies | Must exceed the longest *legitimate* processing pause (GC, slow write, brief partition). Too short manufactures duplicate work; too long delays recovery. |
| `max_delivery_count` | none — caller supplies | Above this, dead-letter rather than reclaim. |
| `count` per read/sweep | `10` / `100` | Batch-size choices, not limits of anything. XAUTOCLAIM scans at most `count * 10` PEL entries per call. |
| `allow_non_positive_price` | `False` | Rejects zero/negative prices by default; opt in for spreads and instruments that genuinely settled negative (CL, 2020-04-20). |

## Scope boundary

This module is a client-side fanout and recovery helper. It does not configure Redis persistence, replication, eviction (`maxmemory-policy`) or cluster topology, and it cannot make an at-least-once bus exactly-once. It is not a compliance artifact and asserts no regulatory requirement.
