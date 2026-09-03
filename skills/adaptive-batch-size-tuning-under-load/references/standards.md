# Real-Time Architecture Standards — adaptive-batch-size-tuning-under-load

## The tuning signal

Tuning is driven by **batch fullness at the flush boundary**,
`F = depth_at_flush / current_batch_size`, smoothed by an EWMA — not by buffer
occupancy against `queue_capacity`.

This is the load-bearing design decision. `add_item` returns the batch as soon
as the buffer reaches `B`, so buffer depth is bounded by `B` by construction
and `depth / queue_capacity ≤ B / queue_capacity` always. A controller tuned on
that quantity is reading its own output, not the load: it is positive feedback
on `B`, and under saturation it drives `B` to `B_min` and `T_flush` to `T_max`.
`F` instead separates the two regimes that matter:

- `F = 1.0` — the size threshold fired: the producer filled `B` records before
  `T_flush` elapsed. Sizing is the binding constraint ⇒ **load is high**.
- `F < 1.0` — the timeout fired first and cut a partial batch. Time is the
  binding constraint ⇒ **load is low**.

`queue_fill_ratio_raw` / `queue_fill_ratio_ewma` are still exported, as
backpressure gauges against `queue_capacity`. They do not drive tuning.

## Tuning curve (defaults)

| Load Level | Signal | Batch Size Action | Flush Timeout Action |
|---|---|---|---|
| High Load | `F_ewma > 0.70` **and** `EWMA(L) ≤ L_target` | multiply by **1.5×** (cap `B_max`) | reduce by **20%** (`× 0.8`, floor `T_min`) |
| Low Load | `F_ewma < 0.10` | divide by **1.2×** (floor `B_min`) | increase by **20%** (`× 1.2`, cap `T_max`) |
| Deadband | `0.10 ≤ F_ewma ≤ 0.70` | **no change** | **no change** |
| Latency Spike | `EWMA(L) > L_target` | multiply by **0.8×** (floor `B_min`) | unchanged; **expansion is barred** until latency recovers |

### Why expansion is gated on latency headroom

`expand_multiplier × latency_throttle_multiplier = 1.5 × 0.8 = 1.2 > 1`. With
one latency observation per flush — the normal case — a throttle step cannot
undo an expansion step, so an ungated controller ratchets `B` to `B_max` and
parks there with sink latency permanently above target. Gating the high-load
branch on `EWMA(L) ≤ L_target` makes the throttle authoritative and gives the
loop a fixed point at the batch size where sink latency ≈ `L_target`.

Do **not** "fix" this instead by setting `expand_multiplier × throttle ≤ 1`;
that would make the controller unable to grow under a healthy sink.

## Vendor alignment

The bounds are chosen to sit on the same axes as the sinks this engine feeds.
Verified against vendor documentation (checked 2026-09-03):

- **ClickHouse adaptive async-insert timeout**: `async_insert_busy_timeout_min_ms`
  = `50`, `async_insert_busy_timeout_max_ms` = `200` (documented as `1000` on
  ClickHouse Cloud), with `async_insert_use_adaptive_busy_timeout` = `1`. This
  skill's `T_min = 0.05 s` / `T_max = 1.0 s` spans the same range.
- **ClickHouse buffer caps**: `async_insert_max_query_number` = `450`. For
  `async_insert_max_data_size` the ClickHouse docs are internally inconsistent —
  the settings reference lists `10485760` (10 MiB) while the async-insert guide
  says 100 MiB. Read your server's `system.settings` rather than trusting
  either number; the point that matters here is only that the vendor bounds the
  buffer at all.
- **ClickHouse durability**: `wait_for_async_insert` defaults to `1`, which
  acknowledges only after the flush to disk. Setting it to `0` acknowledges on
  buffering, with "no guarantee the data will be persisted, errors only surface
  during flush, and there is no dead-letter queue for failed inserts". Same
  operational advice applies here: with `= 0`, `record_write_latency()` measures
  nothing useful and the throttle is blind.
- **Apache Kafka producer**: `batch.size` default `16384` (16 KiB);
  `linger.ms` default `0` before Apache Kafka 4.0 and `5` from 4.0 onward
  (KIP-1030).

Sources:

- ClickHouse settings reference — <https://clickhouse.com/docs/operations/settings/settings>
- ClickHouse asynchronous inserts guide — <https://clickhouse.com/docs/optimize/asynchronous-inserts>
- Apache Kafka producer configs — <https://kafka.apache.org/41/configuration/producer-configs/>
- KIP-1030 — <https://cwiki.apache.org/confluence/display/KAFKA/KIP-1030:+Change+constraints+and+default+values+for+various+configurations>

## Smoothing

- `fill_ewma_alpha` defaults to `0.3` and smooths the **batch fullness** signal.
- `latency_ewma_alpha` defaults to `0.2` (lower alpha = more smoothing, slower
  convergence).
- Both must be in `(0, 1]`; validation rejects `α = 0`. Tests set them to `1.0`
  for immediate reaction.
- Both EWMAs are **seeded with their first observation** rather than starting at
  0. A latency EWMA starting from 0 under-reads a slow sink for roughly the
  first `1/α` samples — with `α = 0.2`, a single 80 ms write against a 50 ms
  target would have registered as 16 ms and let the batch keep growing. For a
  safety throttle, that bias points the wrong way.

Convergence, for choosing `α`: after `n` samples an EWMA has closed
`1 - (1-α)^n` of a step change, so `α = 0.3` is within 10% of a step after
`n = ⌈ln 0.1 / ln 0.7⌉ = 7` samples. Note the unit: the fullness EWMA advances
**once per flush**, not per record and not per second, so seven flushes — not
seven ticks — is the settling time.

## Back-pressure pattern

The engine emits batches; it does **not** write to the sink. Back-pressure is
the responsibility of the consumer loop, with three proven patterns. Note that
`QueueFullError` is raised *before* the item is buffered, so the caller still
owns it in every pattern below.

### Pattern 1 — Flush-and-retry (preferred)

```python
try:
    batch = tuner.add_item(item)
except QueueFullError:
    pending = tuner.flush_now()      # cut a batch to make room
    if pending:
        sink_write_with_metric(pending)
    batch = tuner.add_item(item)     # retry; may raise again if sink is stuck
```

### Pattern 2 — Drop-on-full (loss-tolerant logs only)

```python
try:
    batch = tuner.add_item(item)
except QueueFullError:
    metrics.drop_count += 1
    continue  # item is discarded
```

### Pattern 3 — Spill-to-disk

```python
try:
    batch = tuner.add_item(item)
except QueueFullError:
    spill_buffer.append(item)
    continue
```

### Why not add a shrink-on-`QueueFullError` loop?

Because the engine already has one closed loop — `record_write_latency` shrinks
the batch under sustained pressure. A second controller reacting to the same
underlying condition oscillates against the first. Pick **one** mechanism.

## Memory bounds rationale

Both ClickHouse and Kafka bound their client-side buffers (see Vendor alignment
above); an unbounded `deque` here would be a divergence from vendor practice and
a straightforward OOM path under a sink outage. The engine exposes
`max_queue_size` (default 5000 records) and raises `QueueFullError` past the cap
— pair this with `backpressure-drop-degrade-policy`.

`queue_capacity` must be `<= max_queue_size` (validated at construction).
Otherwise the exported fill gauge can never reach 1.0 and any alert threshold
set against it is calibrated to an unreachable number.

## Concurrency

- Engine holds a single `threading.Lock` around queue mutation and tuning.
- Critical section is bounded: deque pop, EWMA update, tuning decision. The
  sink write happens **outside** the lock, and so does the optional `on_flush`
  callback — running the callback under the lock would block every other
  producer for the duration of a sink write, and would deadlock outright if the
  callback re-entered the engine (for example to read `get_status()` for
  metrics).
- Consequence: under concurrent producers, `on_flush` invocations are **not**
  ordered relative to one another. Use a single producer thread, or serialise
  inside the callback, if cross-batch write order matters.
- Many producers may share one engine instance (multi-producer → single-sink).
- One engine per **sink**, not per producer thread.
- The engine owns no timer thread; `flush_if_due()` must be driven by the
  caller (see `references/workflows.md` §5).
- The engine is not async-aware; do not `await` inside methods.

## Observability surface

`BatchTunerStatus.as_dict()` is the canonical JSON shape:

```json
{
  "current_batch_size": 327,
  "current_flush_timeout_sec": 0.05,
  "queue_depth": 120,
  "queue_capacity": 2000,
  "queue_fill_ratio_raw": 0.06,
  "queue_fill_ratio_ewma": 0.058,
  "batch_fill_ratio_ewma": 1.0,
  "ewma_write_latency_ms": 26.9,
  "total_flushed_records": 184200,
  "total_flush_events": 1208,
  "total_tuning_transitions": 12
}
```

Recommended SLOs:

- `queue_fill_ratio_ewma` < 0.85 → healthy (backpressure headroom)
- `ewma_write_latency_ms` < `target_write_latency_ms` (default 50 ms) → healthy
- `total_tuning_transitions_per_hour` < 60 → healthy (many transitions per
  minute is a warning signal)
- `current_batch_size` == `min_batch_size` while the feed is busy → **alert**:
  that is the signature of a mis-wired control signal or a stuck throttle, not
  of low load.
