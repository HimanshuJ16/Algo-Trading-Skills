---
name: adaptive-batch-size-tuning-under-load
description: Use when writing market data or order logs to downstream databases
  (TimescaleDB, ClickHouse) or message brokers to dynamically adapt write batch
  sizes and flush timeouts based on queue pressure and sink write latency.
  Hysteresis-shaped, EWMA-smoothed, back-pressure-aware. Single-writer / multi-producer
  thread-safe engine, not a generic embedder.
domain: algorithmic-trading
subdomain: real-time-architecture
tags:
- real-time-architecture
- adaptive-batching
- dynamic-tuning
- throughput-optimization
- database-sink
- backpressure
- ewma
brokers_frameworks: []
jurisdictions: [global]  # technique is jurisdiction-agnostic
version: "1.1.0"
author: algo-trading-skills-contributors
license: Apache-2.0
---

## When to Use

Invoke this skill when persistently writing high-volume tick feeds or trading
logs into downstream databases (TimescaleDB, ClickHouse, InfluxDB) or message
queues (Kafka, Redis Streams). Hardcoding a static batch size is wrong across
regimes — it causes high persistence latency when markets are quiet (waiting
for fixed batch limits to fill) and DB I/O overload during flash crashes.

The skill produces an `AdaptiveBatchTunerEngine` whose job is to scale the
batch size `B_t` and flush timeout `T_flush` in response to two signals:

1. **Smoothed queue fill ratio** `R_ewma = EWMA(Q_current / Q_capacity)`
2. **Smoothed downstream write latency** (target: `target_write_latency_ms`)

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

## Prerequisites

- A **downstream sink write function** accepting batches of records (caller loop).
- **Capacity constants** declared up front:
  - `B_min` — minimum batch size (records; default 10)
  - `B_max` — maximum batch size (records; default 1000)
  - `T_min` / `T_max` — flush-timeout bounds (default 50ms / 1000ms; aligned with
    ClickHouse's adaptive-busy-timeout range)
  - `L_target` — downstream write-latency target (default 50ms)
- **Queue capacity** consistent throughout the lifecycle; pass via `TuningConfig.queue_capacity`.
- **Sink latency instrumentation** — caller must call `record_write_latency(ms)`
  on every successful (or failed) write.

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

3. **Adapt batch size** (under the hood):
   - **High load** (`R_ewma > 0.70`): `B_{t+1} = min(B_max, ⌊B_t × 1.5⌋)`; reduce `T_flush`.
   - **Low load** (`R_ewma < 0.10`): `B_{t+1} = max(B_min, ⌊B_t / 1.2⌋)`; extend `T_flush`.
   - **Deadband** (`0.10 ≤ R_ewma ≤ 0.70`): no tuning — EWMA smoothing prevents
     oscillation around the boundary.

4. **Apply latency feedback** (overrides fill-ratio tuning under sustained pressure):
   - If `EWMA(L) > L_target`: `B_{t+1} = max(B_min, ⌊B_t × 0.8⌋)`. Latency throttle
     triggers **even inside the deadband**, which is its purpose.

5. **Flush triggers**:
   - **Threshold**: `#items ≥ current_batch_size`.
   - **Timeout**: `elapsed ≥ current_flush_timeout_sec`.
   - **Forced flush**: `tuner.flush_now()` for shutdown / checkpoint boundaries.

6. **Shutdown**:
   ```python
   leftover = tuner.close()
   if leftover: sink_write(leftover)
   ```

> Full procedure with rationale: `references/workflows.md`.
> Concrete numeric thresholds: `references/standards.md`.
> Operational checklist: `assets/checklist.md`.

## Decision Points

| Situation | Action |
|-----------|--------|
| Sink write latency consistently > `L_target` | Engine is auto-throttling (×0.8). If still failing, lower `target_write_latency_ms` to force aggressive throttle, or activate circuit breaker upstream. |
| `QueueFullError` raised | Caller is producing faster than sink drains. Implement back-pressure (block producer), degradation (drop new items), or fall back to a slower sink. |
| Fill ratio hovers at boundary (0.10 or 0.70) | Deadband is working — there should be **no** batch-size oscillation. Verify via `tuner.get_status().total_tuning_transitions`. |
| Overflow `.record_write_latency(0)` — i.e. hot cache made flush look instant | Engine still trusts the smoothed EWMA — single low-variance samples won't reset the throttle. |
| Sustained high fill with no rule violations | Lower `max_batch_size` for the workload, or migrate to a faster sink. |
| Test opposing `add_item()` from many threads | Engine is thread-safe; one engine per sink. Do **not** instantiate per add. |

## Common Pitfalls

- **Unbounded max batch size** → queued memory can exceed sink RAM. Always set
  `max_queue_size`; the engine raises `QueueFullError` at the cap.
- **Ignoring latency feedback** → batch escalates under DB lock contention,
  exhausting the connection pool. The `target_write_latency_ms` knob is the
  tripwire; tune it to your sink's healthy floor.
- **Rapid oscillations** (solved by the new deadband / EWMA combination): if
  fill ratio flickers around one threshold and the engine thrashes, increase
  `fill_ewma_alpha` (lower alpha = more smoothing) — but never go to 0.
- **Latency oscillation** under repeated `record_write_latency()` spikes: if
  smoothed latency is at `L_target - epsilon` and individual writes push it
  slightly above, the engine is repeatedly halving-and-climbing. Adjust
  `target_write_latency_ms` to align with the **typical** sink latency, not
  the ideal.
- **Synchronous batch-handling logic**: do not block on a slow sink while
  holding the queue; return the batch and flush outside any locks. The engine
  holds its lock only for batch extraction + tuning decisions.
- **Caller-driven capacity drift**: the previous API required `queue_capacity`
  on every call. The new API bakes it into `TuningConfig`; verify upstream that
  the call sites have been migrated.
- **Cold-start EWMA** (`fill_ewma_ratio == 0`): smoothing starts at zero and the
  first burst of adds looks like "low load" until the EWMA warms up. Set
  `initial_fill_ewma` (deferred) or accept that the first minute of operation
  is slightly micro-tuned.
- **ClickHouse async_insert `wait_for_async_insert = 0`** (fire-and-forget):
  the engine's `record_write_latency()` will see 0 ms for "successful" flushes,
  masking real DB-side problems. Use `wait_for_async_insert = 1`.

## Verification

Run the unit tests:

```bash
python -m unittest discover -s skills/adaptive-batch-size-tuning-under-load/scripts -v
```

What they assert:

- Low-load regime shrinks batch size below initial.
- High-load regime expands batch size above initial.
- Latency throttle fires at and above target.
- EWMA smoothing with `alpha = 0.5` produces the expected smoothed value.
- **Bounded queue**: `add_item` past `max_queue_size` raises `QueueFullError`.
- **Deadband**: in the [0.10, 0.70] zone, no tuning transitions occur.
- `flush_now()` extracts up to `current_batch_size` items; `close()` drains.
- `reset()` returns to initial state.
- `RecordingWriteLatency` rejects negative values.
- Configuration validation: `min > max`, `alpha in (0, 1]`, threshold ordering.
- Status JSON-serializable (Prometheus-ready).

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
   ranges within 5 minutes of traffic beginning.
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
- `kill-switch-and-drawdown-circuit-breakers` — strategy-level circuit breaker
  upstream of this engine; pair them so strategy stops suppress writes.
- `latency-monitoring-percentile-based-slas` — for monitoring the sink's
  P99/P999 against `L_target`.
- `model-inference-latency-budget-for-live-trading` — analogous pattern for
  model inference instead of DB writes.
