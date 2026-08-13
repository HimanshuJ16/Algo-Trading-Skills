# Real-Time Architecture Standards — adaptive-batch-size-tuning-under-load

## Tuning curve (defaults)

| Load Level | Signal | Batch Size Action | Flush Timeout Action |
|---|---|---|---|
| High Load | smoothed `R_ewma > 0.70` | multiply by **1.5×** (cap `B_max`) | reduce by **20%** (`× 0.8`, floor `T_min`) |
| Low Load | smoothed `R_ewma < 0.10` | divide by **1.2×** (floor `B_min`) | increase by **20%** (`× 1.2`, cap `T_max`) |
| Deadband | `0.10 ≤ R_ewma ≤ 0.70` | **no change** | **no change** |
| Latency Spike | smoothed `EWMA(L) > L_target` | multiply by **0.8×** (floor `B_min`) | unchanged (latency throttle overrides fill ratio) |

These multipliers align with vendor defaults:

- ClickHouse async-insert adaptive-busy-timeout default: `50 ms` ↔ `200 ms` (self-hosted) / `1000 ms` (Cloud). Defaults here use `T_min = 0.05 s`, `T_max = 1.0 s` — same axes.
- Apache Kafka `linger.ms` default `0` (≤4.0) / `5` (≥4.0); rule of thumb `linger ≥ server processing time`.
- ClickHouse document safeguard: `wait_for_async_insert = 1` is the back-pressure mechanism; `= 0` is "very risky". Same operational advice applies here.

## Smoothing

- `fill_ewma_alpha` defaults to `0.3` (the fill ratio is smoothed in `_update_fill_ewma_locked`).
- `latency_ewma_alpha` defaults to `0.2` (latency is more reactive; lower alpha = slower convergence).
- Both must be in `(0, 1]`; validation rejects `α = 0`. Tests typically set them to `1.0` for immediate reaction.

## Back-pressure pattern

The engine emits batches; it does **not** write to the sink. Back-pressure is
the responsibility of the consumer loop, with three proven patterns:

### Pattern 1 — Block-on-full (preferred)

```python
try:
    batch = tuner.add_item(item)
except QueueFullError:
    flush_pending()  # push whatever is already in flight
    # On retry, queue has drained.
    batch = tuner.add_item(item)
```

### Pattern 2 — Drop-on-full (loss-tolerant logs only)

```python
try:
    batch = tuner.add_item(item)
except QueueFullError:
    metrics.drop_count += 1
    continue  # never flushes
```

### Pattern 3 — Spill-to-disk

```python
try:
    batch = tuner.add_item(item)
except QueueFullError:
    spill_buffer.append(item)
    continue
```

### Why not adapt basin-level tuning instead?

Because the engine already does that — `record_write_latency` shrinks the
batch under sustained pressure. Adding a higher-level shrink-on-`QueueFullError`
loop creates oscillation between two controllers. Pick **one** mechanism.

## Memory bounds rationale

ClickHouse caps `async_insert_max_data_size = 100 MiB` and
`async_insert_max_query_number = 450`; Kafka pre-allocates buffers proportional
to `batch.size`. An unbounded `deque` here is a divergence from vendor practice.
The engine exposes `max_queue_size` (default 5000 records) and raises
`QueueFullError` past the cap — pair this with `backpressure-drop-degrade-policy`.

## Concurrency

- Engine holds a single `threading.Lock` around `add_item` and `flush_now`.
- Critical section is bounded: deque pop, status read, EWMA update, tuning
  decision. Caller-side sink write happens **outside** the lock.
- Many producers may share one engine instance (multi-producer → single-sink).
- One engine per **sink**, not per producer thread.
- The engine is not currently async-aware; do not `await` inside methods.

## Observability surface

`BatchTunerStatus.as_dict()` is the canonical JSON shape:

```json
{
  "current_batch_size": 350,
  "current_flush_timeout_sec": 0.16,
  "queue_depth": 1200,
  "queue_capacity": 2000,
  "queue_fill_ratio_raw": 0.6,
  "queue_fill_ratio_ewma": 0.58,
  "ewma_write_latency_ms": 47.313,
  "total_flushed_records": 184200,
  "total_flush_events": 1024,
  "total_tuning_transitions": 17
}
```

Recommended SLOs:

- `queue_fill_ratio_ewma` < 0.85 → healthy
- `ewma_write_latency_ms` < `target_write_latency_ms` (default 50ms) → healthy
- `total_tuning_transitions_per_hour` < 60 → healthy (significant transitions/min is a warning signal)

## Category

`real-time-architecture` — see top-level `mappings/` directory.
