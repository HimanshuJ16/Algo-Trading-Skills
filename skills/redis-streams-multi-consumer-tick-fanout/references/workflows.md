# Workflows for Redis Streams Multi-Consumer Tick Fanout

The order matters. Each step lists the decision that step actually forces; skip
the decision and Redis picks a default that is usually wrong for market data.

## 1. Topology: one stream, one group per independent consumer

- One stream per feed (or per symbol shard). Every consumer group attached to it
  receives **every** entry independently — that is the fanout.
- Inside a group, consumers compete. Two workers in `grp_strategy` split the
  stream between them and process one symbol's ticks concurrently and out of
  order. If order matters, run one consumer per group and shard across streams.
- Consumer names must be unique and stable per process. The PEL is keyed by
  consumer name, so two processes sharing one name inherit each other's pending
  entries.

```python
mgr = RedisTickFanoutManager(redis_client=redis.Redis(decode_responses=True),
                             stream_name="mkt:us_equities",
                             maxlen=250_000)
for group in ("grp_strategy", "grp_risk", "grp_tick_log"):
    mgr.create_consumer_group(group, start_id="$")   # or "0" to replay
```

**Decision**: `$` (only ticks published from now on) or `0` (replay everything
still in the stream). `$` is redis-py's default and the failure mode is silent —
the group reports nothing until the next tick arrives.

## 2. Publish: validate first, cap deliberately

```python
msg_id = mgr.publish_tick(TickData(symbol="AAPL", last_price=150.25,
                                   volume=100, timestamp=venue_epoch_seconds))
```

- Validation happens before `XADD`: empty symbol, non-finite or non-positive
  price, negative volume and non-positive timestamp all raise. A stream is a
  fanout bus — a bad tick published once has to be rejected by every consumer
  separately.
- `timestamp` is the **venue** event time. The stream ID's millisecond part is
  the Redis server's clock and is not a substitute.
- `MAXLEN` sizing is the memory/recoverability trade: cap = peak ticks/sec x the
  slowest consumer's worst-case backlog window, with margin. `approximate_trim`
  (`MAXLEN ~`) is cheaper and may leave "a few tens more" entries; exact trimming
  costs more.

## 3. Consume: `>` for new work, `0` once after a restart

```python
batch = mgr.consume_batch("grp_strategy", consumer_name, count=200)
for msg_id, tick in batch.ticks:
    process(tick)                       # must be idempotent
    mgr.acknowledge_tick("grp_strategy", msg_id)
for msg_id, reason in batch.malformed:
    dead_letter(msg_id, reason)         # then XACK it, deliberately
```

Startup sequence, as the XREADGROUP reference describes it: read once with
`start_id="0"` to drain this consumer's own pending entries, and once that comes
back empty, switch to `">"` for the live feed.

`>` returns only entries **never delivered to the group**. An entry you read but
did not acknowledge is not redelivered by a later `>` read — it stays in the PEL
until somebody claims it. A consume loop with no recovery path does not retry;
it leaks.

## 4. Acknowledge: the return value is information

```python
acked = mgr.acknowledge_ticks("grp_strategy", *ids)
if acked != len(ids):
    # Some entry was already claimed by another consumer: it is being
    # processed twice. Expected occasionally; alarm if it is not rare.
    ...
```

## 5. Recover: discover, then claim

Two routes, same guarantees.

**Targeted** — inspect the PEL, then claim specific IDs:

```python
stale = mgr.pending_summary("grp_strategy", count=500, min_idle_ms=30_000)
claimed = mgr.claim_stale_ticks("grp_strategy", my_name, 30_000,
                                [e.message_id for e in stale])
```

**Sweeping** — XAUTOCLAIM with a cursor (Redis 6.2+):

```python
cursor = "0-0"
while True:
    result = mgr.recover_stale_ticks("grp_strategy", my_name, 30_000,
                                     count=100, start_id=cursor)
    for msg_id, tick in result.claimed:
        process(tick)
        mgr.acknowledge_tick("grp_strategy", msg_id)
    if result.deleted_ids:
        alarm("ticks trimmed before ACK", ids=result.deleted_ids)  # lost
    cursor = result.cursor
    if cursor == "0-0":
        break
```

Run the sweep on a timer even after the cursor returns `0-0`: entries that were
not idle enough on this pass become claimable later.

Facts that shape the threshold:

- Claiming resets idle time, so two racing claimers cannot both win.
- Claiming does **not** stop the previous owner. A GC pause or a brief partition
  is indistinguishable from a crash, so the paused worker may still be
  mid-processing. Idempotency is not optional.
- Fewer claimed entries than requested is normal: not pending, not idle enough,
  or trimmed out of the stream.

## 6. Break the reclaim loop

```python
for entry in mgr.find_poison_entries("grp_strategy", max_delivery_count=5):
    dead_letter(entry.message_id, f"delivered {entry.delivery_count}x")
    mgr.acknowledge_tick("grp_strategy", entry.message_id)
```

`XCLAIM` increments the delivery counter specifically so an entry that kills
every consumer that touches it can be detected. Without a ceiling, the group
re-processes it indefinitely.

## 7. Operate

Monitor, at minimum:

- **PEL depth per group** (`pending_summary`) — the backlog nobody has finished.
- **Oldest pending idle time** — how far recovery is behind.
- **`deleted_ids` count** — ticks trimmed before acknowledgement. Non-zero means
  data loss; retune `MAXLEN` or consumer capacity.
- **Max delivery count** — poison entries.
- **Stream length vs `MAXLEN`** — how much replay headroom is left.

Shutdown: stop reading, finish and acknowledge in-flight entries, then exit.
Anything left un-acknowledged is another worker's XCLAIM problem — see
`graceful-shutdown-draining-in-flight-ticks`.

## 8. Testing against the simulator

`MockRedisStreamEngine` reproduces last-delivered-id, PEL, delivery counters,
idle-time gating, KEEPREF trimming and null payloads for trimmed entries, with an
injectable millisecond clock so idle assertions are exact instead of
`sleep`-dependent.

It does **not** reproduce approximate trimming, `BLOCK`, persistence,
replication, failover, cluster behaviour or concurrency. Test those against a
real server; a simulator with the wrong semantics is worse than no test.
