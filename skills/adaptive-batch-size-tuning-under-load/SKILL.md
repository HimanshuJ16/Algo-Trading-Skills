---
name: adaptive-batch-size-tuning-under-load
description: >-
  Use when writing high-volume ticks or order logs into TimescaleDB, ClickHouse or Kafka
  and one static batch size either stalls in quiet markets or floods the sink in a
  burst; adapts batch size and flush timeout from EWMA sink latency.
license: Apache-2.0
metadata:
  domain: algorithmic-trading
  subdomain: real-time-architecture
  tags: real-time-architecture, adaptive-batching, dynamic-tuning, throughput-optimization, database-sink, backpressure, ewma
  brokers_frameworks: ""
  version: "2.0.0"
  author: algo-trading-skills-contributors
---

## When to Use

Invoke this skill when persistently writing high-volume tick feeds or trading
logs into downstream databases (TimescaleDB, ClickHouse, InfluxDB) or message
queues (Kafka, Redis Streams). Hardcoding a static batch size is wrong across
regimes — it causes high persistence latency when markets are quiet (waiting
for fixed batch limits to fill) and DB I/O overload during flash crashes.

The skill produces an `AdaptiveBatchTunerEngine` whose job is to scale the
batch size `B_t` and flush timeout `T_flush` in response to two signals:

1. **Smoothed batch fill ratio at the flush boundary**
   `F_ewma = EWMA(depth_at_flush / B_t)` — did the producer fill the batch
   before the timeout, or did the timeout cut a half-empty batch?
2. **Smoothed downstream write latency** (target: `target_write_latency_ms`),
   which acts as the throttle and outranks signal 1.

### Why fullness, and not queue depth

The obvious signal — buffer occupancy against a nominal `queue_capacity` — does
not work in this architecture, and getting this wrong inverts the controller.
`add_item` hands the batch back the instant the buffer reaches `B`, so the
buffer depth is bounded by `B` by construction: `depth / queue_capacity` can
never exceed `B / queue_capacity` and therefore measures *the tunable*, not the
load. Tuning on it is positive feedback on `B` itself, and under saturation it
drives `B` down to `B_min` — the exact opposite of the intent. Batch fullness
at the flush boundary is the signal that actually separates "the producer
outran the batch size" (`F = 1.0` ⇒ high load) from "the timeout expired
half-empty" (`F < 1.0` ⇒ low load).

## When NOT to Use

- **Single-shot or batch-mode loads** — if the total record count is bounded
  and known upfront, fine-tune one batch size statically. The adaptive engine
  adds no value when input is finite.
- **Synchronous request/response flows** — this is fire-and-forget throughput
  tuning, not per-request optimisation. Use RPC-style tuning instead.
- **Distributed stream processing stages** (Flink, ksqlDB, Spark Structured
  Streaming) — they have their own internal flush predicates; this engine is
  for the **client-side** write path into the sink, not for the consumer side
  of an internal stage.
- **Latency-critical tick-to-trade paths** — those need synchronously bounded
  writes; the entire batch-and-flush taxonomy is wrong. See
  `tick-to-trade-latency-measurement` instead.
- **When the sink is a queue with built-in batching** (Kafka producer with
  `linger.ms`/`batch.size`) AND the queue is the only consumer — let Kafka's
  producer do the tuning.
- **When strict cross-batch write ordering is required across multiple
  producer threads** — the engine detaches each batch before returning it and
  runs `on_flush` outside its lock, so concurrent producers may write out of
  order. Use a single producer thread, or serialise inside your callback.

## Prerequisites

- A **downstream sink write function** accepting batches of records (caller loop).
- **Capacity constants** declared up front:
  - `B_min` — minimum batch size (records; default 10)
  - `B_max` — maximum batch size (records; default 1000)
  - `T_min` / `T_max` — flush-timeout bounds (default 50ms / 1000ms; aligned with
    ClickHouse's adaptive-busy-timeout range)
  - `L_target` — downstream write-latency target (default 50ms). This is the
    binding constraint on expansion; set it to a latency your sink can actually
    sustain, not an aspiration.
- **Queue bounds**: `max_queue_size` is the hard cap that raises
  `QueueFullError`; `queue_capacity` is the denominator of the exported
  backpressure gauge and must be `<= max_queue_size` (validated).
- **Sink latency instrumentation** — caller must call `record_write_latency(ms)`
  on every write, successful or failed. Without it the throttle never fires and
  the batch expands until it hits `B_max`.
- **A scheduler tick** if the producer can go quiet — see Workflow step 5.

## Workflow

1. **Construct the engine** with a `TuningConfig`:

   ```python
   from batch_tuner import AdaptiveBatchTunerEngine, TuningConfig

   tuner = AdaptiveBatchTunerEngine(TuningConfig(
       min_batch_size=10,
       max_batch_size=1000,
       initial_batch_size=100,
       target_write_latency_ms=50.0,
       queue_capacity=2000,
       max_queue_size=5000,
   ))
   ```

2. **Produce-loop pattern** (the contract):

   ```python
   try:
       batch = tuner.add_item(item)
   except QueueFullError as exc:
       handle_overload()  # backpressure / degrade / drop
       continue

   if batch is None:
       continue  # not yet a flush boundary

   t0 = time.monotonic()
   try:
       sink_write(batch)
   finally:
       tuner.record_write_latency((time.monotonic() - t0) * 1000.0)
   ```

   The `finally` matters: a failed write is still a latency observation, and
   skipping it on the error path is how the throttle goes blind exactly when
   the sink is sick.

3. **Adapt batch size** (under the hood, evaluated at each flush boundary):
   - **High load** (`F_ewma > 0.70` **and** `EWMA(L) ≤ L_target`):
     `B_{t+1} = min(B_max, ⌊B_t × 1.5⌋)`; reduce `T_flush`.
   - **Low load** (`F_ewma < 0.10`): `B_{t+1} = max(B_min, ⌊B_t / 1.2⌋)`;
     extend `T_flush`.
   - **Deadband** (`0.10 ≤ F_ewma ≤ 0.70`): no tuning — EWMA smoothing plus the
     deadband prevents oscillation around the boundary.

4. **Apply latency feedback** — this is what closes the loop:
   - If `EWMA(L) > L_target`: `B_{t+1} = max(B_min, ⌊B_t × 0.8⌋)`, and the
     high-load expansion branch is **barred** until latency returns under
     target. The bar is not decorative: expansion multiplies by 1.5 while the
     throttle multiplies by 0.8, and `1.5 × 0.8 = 1.2 > 1`, so without it one
     throttle per flush can never undo one expansion and `B` ratchets to
     `B_max` with sink latency pinned above target.
   - The throttle fires **even inside the deadband**, which is its purpose.

5. **Flush triggers**:
   - **Threshold**: `#items ≥ current_batch_size` (evaluated inside `add_item`).
   - **Timeout**: `elapsed ≥ current_flush_timeout_sec`. The engine owns **no
     timer thread**, so this is only evaluated when you call in. If the
     producer can stall — thin instrument, feed outage, the lull after the
     close — buffered records would otherwise sit in memory indefinitely and
     die with the process. Drive `tuner.flush_if_due()` from a scheduler at an
     interval at or below `min_flush_timeout_sec`:

     ```python
     batch = tuner.flush_if_due()   # None if not yet due / nothing buffered
     if batch:
         sink_write_with_metric(batch)
     ```

   - **Forced flush**: `tuner.flush_now()` for checkpoint boundaries. Returns up
     to `current_batch_size` items and deliberately does **not** tune — a batch
     that is partial because you asked for it says nothing about producer speed.

6. **Shutdown**:
   ```python
   leftover = tuner.close()          # drains the WHOLE buffer, not one batch
   for chunk in chunked(leftover, sink_max_rows):
       sink_write(chunk)
   ```
   `close()` is not capped at `current_batch_size`, so the returned list may be
   larger than your sink accepts in one call — chunk it. After `close()`,
   `add_item` raises `RuntimeError`; call `reset()` to reuse the engine.

> Full procedure with rationale: `references/workflows.md`.
> Concrete numeric thresholds: `references/standards.md`.
> Operational checklist: `assets/checklist.md`.

## Decision Points

| Situation | Action |
|-----------|--------|
| Sink write latency consistently > `L_target` | Engine is auto-throttling (×0.8) and expansion is barred. If latency still does not recover, the sink — not the batch size — is the problem: check IOPS, locks, connection pool. |
| `current_batch_size` pinned at `B_max` with latency under target | Correct behaviour: the sink absorbs everything you can give it. Raise `B_max` only if the sink documents a larger optimal write unit. |
| `current_batch_size` pinned at `B_min` | Either genuinely idle traffic, or the throttle is stuck on. Compare `ewma_write_latency_ms` with `target_write_latency_ms` before assuming idleness. |
| `QueueFullError` raised | Caller is producing faster than sink drains. Implement back-pressure (block producer), degradation (drop new items), or fall back to a slower sink. |
| `batch_fill_ratio_ewma` sits inside [0.10, 0.70] | Deadband is working — there should be **no** batch-size oscillation. Verify via `total_tuning_transitions`. |
| `record_write_latency` reports ~0 ms for every write | The sink is not actually acknowledging durability (see the ClickHouse `wait_for_async_insert = 0` pitfall). The throttle is blind; fix the instrumentation before trusting the tuner. |
| Producer thread can stall | You must call `flush_if_due()` on a timer, or the timeout trigger never fires. |
| Many threads calling `add_item()` | Engine is thread-safe; one engine per sink. Do **not** instantiate per add. Cross-batch write ordering is not guaranteed. |

## Common Pitfalls

- **Tuning on queue depth instead of batch fullness** → the controller inverts
  and drives the batch *down* under load. See "Why fullness, and not queue
  depth" above; this is the single most important design point in the skill.
- **Assuming `close()` returns one batch** → it drains the entire buffer, which
  may exceed what your sink accepts in a single call. Chunk the result. (The
  converse bug — capping the shutdown drain at `current_batch_size` — silently
  strands records, which for an order log is data loss.)
- **Never calling `flush_if_due()`** → the flush timeout is dead weight, and a
  stalled producer leaves records buffered until the process exits.
- **Skipping `record_write_latency()` on the error path** → the throttle goes
  blind exactly when the sink is failing.
- **Unbounded max batch size** → queued memory can exceed sink RAM. Always set
  `max_queue_size`; the engine raises `QueueFullError` at the cap.
- **Feeding a non-finite latency** → rejected with `ValueError`. A `NaN` would
  otherwise poison the EWMA permanently (`NaN > target` is always `False`,
  silently disabling the throttle for the life of the process) and serialise as
  invalid JSON in the metrics export.
- **Rapid oscillations**: if fullness flickers around one threshold and the
  engine thrashes, lower `fill_ewma_alpha` (lower alpha = more smoothing) —
  but never to 0.
- **Latency oscillation** under repeated `record_write_latency()` spikes: if
  smoothed latency sits at `L_target - epsilon` and individual writes push it
  slightly above, the engine repeatedly throttles and climbs. Set
  `target_write_latency_ms` from the **typical** sink latency, not the ideal.
- **Blocking inside `on_flush`**: the callback runs outside the engine lock, so
  it will not deadlock or block other producers — but it does run on the
  calling producer's thread, so a slow sink write there still stalls that
  producer. Prefer the returned-batch pattern for the actual write.
- **ClickHouse async_insert `wait_for_async_insert = 0`** (fire-and-forget):
  `record_write_latency()` will see near-0 ms for "successful" flushes, masking
  real DB-side problems. Use `wait_for_async_insert = 1`.

## Verification

Run the unit tests:

```bash
python -m unittest discover -s skills/adaptive-batch-size-tuning-under-load/scripts -v
```

43 tests. What they assert:

- **Control-law direction** (the regression that matters): saturating load
  expands the batch toward `B_max` and shortens `T_flush`; quiet,
  timeout-driven load shrinks it and lengthens `T_flush`.
- **Closed loop**: with a batch-size-dependent sink latency, the equilibrium
  batch size settles strictly inside `(B_min, B_max)` with smoothed latency at
  or under target.
- **Deadband**: batches cut ~50% full produce zero tuning transitions; exact
  boundary values (0.10, 0.70) are inside the deadband.
- **Latency throttle**: fires above target, not at exactly target, stops at
  `B_min`, and the EWMA is seeded with its first observation (hand-computed
  expected values, not a re-derivation of the implementation).
- **Shutdown**: `close()` drains records the batch size would have stranded,
  preserves order, is idempotent, and `add_item` after `close()` raises.
- **Flush triggers**: `flush_if_due()` releases an idle buffer; `flush_now()`
  returns a partial batch and does not tune.
- **Bounded queue**: `add_item` past `max_queue_size` raises `QueueFullError`
  and does not buffer the rejected item.
- **Validation**: bound ordering, alpha range, non-finite/negative latency,
  `queue_capacity > max_queue_size`, and mis-signed tuning multipliers.
- **Concurrency**: an `on_flush` callback may re-enter the engine without
  deadlocking; a raising callback does not lose the batch; 8 concurrent
  producers × 500 items lose and duplicate nothing.
- **Status** is strict-JSON serializable (`allow_nan=False`, Prometheus-ready).

Confirm with the operational checklist in `assets/checklist.md` before deploying.

## Success Criteria

A tuning engine is considered **healthy in production** when:

1. `total_tuning_transitions` is bounded — under steady load it should be O(tens
   per hour), **not** O(thousands). Sustained high transition count ⇒ noisy
   upstream or mis-tuned `target_write_latency_ms`.
2. Sink write latency P99 < `L_target` over a rolling 1-hour window.
3. `QueueFullError` rate is 0 (upstream throughput matches or exceeds sink
   drain). If non-zero, page on-call.
4. `current_batch_size` and `current_flush_timeout_sec` settle inside their
   ranges within 5 minutes of traffic beginning — and are **not** pinned at
   `B_min` while the feed is busy, which is the signature of a mis-wired
   control signal.
5. `get_status()` exports cleanly to a JSON metrics pipeline.

## Related Skills

- `kafka-based-tick-distribution-at-scale` — the parent batch architecture for
  Kafka paths; this skill is the *client-side* tuning companion.
- `producer-consumer-tick-pipeline` — the broader produce/consume pipeline;
  this skill is the leaf that decides *when* to flush to the sink.
- `tick-buffering-burst-handling` — what to do when the queue genuinely
  overflows; `QueueFullError` from this skill should be the trigger.
- `backpressure-drop-degrade-policy` — design the policy that decides what
  to do when `QueueFullError` fires.
- `graceful-shutdown-draining-in-flight-ticks` — the shutdown counterpart;
  `close()` is this engine's contribution to that drain.
- `kill-switch-and-drawdown-circuit-breakers` — strategy-level circuit breaker
  upstream of this engine; pair them so strategy stops suppress writes.
- `latency-monitoring-percentile-based-slas` — for monitoring the sink's
  P99/P999 against `L_target`.
- `model-inference-latency-budget-for-live-trading` — analogous pattern for
  model inference instead of DB writes.
