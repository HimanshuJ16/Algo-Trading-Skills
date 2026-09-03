# Deep Workflow Reference — adaptive-batch-size-tuning-under-load

SKILL.md is the **interface contract**; this file holds the **engineering
rationale** and full procedure.

## Full procedure

### 1. Instrument the sink

Before tuning: instrument sink write calls with a stopwatch. Engine smoothness
is only as good as the latency signal.

```python
def sink_write_with_metric(batch):
    t0 = time.monotonic()
    try:
        sink_write(batch)
    except Exception as exc:
        log_sink_failure(exc)
        raise
    finally:
        latency_ms = (time.monotonic() - t0) * 1000.0
        tuner.record_write_latency(latency_ms)
```

The `finally` is deliberate. A write that fails after 900 ms is the most
informative latency sample you will get; recording it only on the success path
blinds the throttle exactly when the sink is degrading.

`record_write_latency` rejects negative and non-finite values. A `NaN` — easy
to produce by pulling a latency out of an empty metrics window — would
otherwise persist in the EWMA forever, and because `NaN > target` is `False`
the throttle would be silently disabled for the life of the process.

### 2. Choose the capacity constants

Concrete starting values. `B_min`/`B_max`/`T_min`/`T_max` are engine settings;
the sink columns are the vendor axes they are meant to line up with (see
`references/standards.md` for the verified vendor defaults and sources).

| Sink | `B_min` | `B_max` | `T_min` | `T_max` | `L_target` |
|------|---------|---------|---------|---------|------------|
| TimescaleDB | 10 | 5000 | 0.05 s | 1.0 s | 50 ms |
| ClickHouse (`wait_for_async_insert=1`) | 100 | 10000 | 0.05 s | 1.0 s | 100 ms |
| Redis Streams XADD batch | 50 | 2000 | 0.05 s | 0.5 s | 30 ms |

For a Kafka producer, prefer `linger.ms` / `batch.size` (defaults `5` ms from
Kafka 4.0, and 16 KiB) over this engine — the producer already batches, and
stacking two batching controllers on one path is the oscillation trap described
in `standards.md`. Use this engine on a Kafka path only when you are batching
*application-level* records upstream of the producer for a reason of your own.

These are starting points. `L_target` is the parameter that actually decides
where the batch size settles, because expansion is barred whenever smoothed
latency is above it — set it from a latency your sink sustains in steady state,
measured, not from an aspiration.

### 3. Compute the smoothed fullness signal

```python
fullness = min(1.0, depth_before_extract / current_batch_size)
self._ewma_batch_fill_ratio = ewma(self._ewma_batch_fill_ratio,
                                   fullness, fill_ewma_alpha)
```

`fullness == 1.0` means the size threshold fired (the producer outran `B`);
`< 1.0` means the timeout fired first. See `standards.md` → "The tuning signal"
for why buffer occupancy against `queue_capacity` cannot work here.

`alpha = 0.3` (default) means the EWMA settles within 10% of a step input after
7 observations, since `1 - (1-α)^n ≥ 0.9` first holds at `n = 7`. The unit is
**flushes**, not ticks and not seconds: the signal advances once per flush
boundary. At 10 flushes/s that is ~0.7 s; at one flush every 200 ms it is ~1.4 s.
If load oscillates faster than the settling time the engine will look "late" —
lower `alpha` for more smoothing, raise it for faster reaction.

Both EWMAs are seeded with their first observation rather than starting at zero;
`standards.md` → "Smoothing" explains why the zero start biases the throttle the
wrong way.

### 4. Apply the tuning curve

Pseudocode (mirroring `_tune_parameters_locked`):

```
has_headroom = EWMA(L) <= L_target

if F_ewma > F_high and has_headroom:   # high load, sink coping
    B = min(B_max, floor(B * 1.5))
    T = max(T_min, T * 0.8)
elif F_ewma < F_low:                   # low load
    B = max(B_min, floor(B / 1.2))
    T = min(T_max, T * 1.2)
else:                                  # deadband, or no latency headroom
    pass
```

Latency throttle (in `record_write_latency`):

```
if EWMA(L) > L_target:
    B = max(B_min, floor(B * 0.8))
    # T is unchanged; expansion stays barred until EWMA(L) recovers
```

The `has_headroom` conjunct is what makes the pair stable — without it,
`1.5 × 0.8 = 1.2 > 1` ratchets `B` to `B_max`. See `standards.md` → "Why
expansion is gated on latency headroom".

Worked equilibrium, sink latency `5 + 0.15·B` ms against `L_target = 50` ms,
saturating producer, defaults otherwise: `B` converges to ~327 with smoothed
latency ~27 ms and ~12 tuning transitions over ~1200 flushes. That transition
count is the number to compare against in staging; O(thousands) means the
signal is noisy or `L_target` is set below what the sink can hold.

### 5. Decide when to flush

Three ways a batch leaves the engine:

```
add_item()      -> flushes if len(queue) >= B  or  elapsed >= T_flush ; tunes
flush_if_due()  -> flushes iff elapsed >= T_flush and queue non-empty ; tunes
flush_now()     -> flushes up to B unconditionally                   ; does NOT tune
close()         -> drains the ENTIRE queue, then bars further adds    ; does NOT tune
```

**The engine owns no timer thread.** `T_flush` is evaluated only when the caller
calls in, so with a purely `add_item`-driven loop the timeout trigger cannot
fire while the producer is quiet — buffered records would sit in memory until
the next tick arrives, or die with the process. Drive `flush_if_due()` from a
scheduler at an interval at or below `min_flush_timeout_sec`:

```python
# e.g. on a dedicated thread, or from the consumer loop's idle branch
while running:
    batch = tuner.flush_if_due()
    if batch:
        sink_write_with_metric(batch)
    sleep(tuner_config.min_flush_timeout_sec / 2)
```

`flush_now()` deliberately does not tune: a batch that is partial because a
checkpoint asked for it says nothing about how fast the producer is running,
and feeding that into the fullness EWMA would spuriously shrink `B`.

### 6. Shut down without losing records

```python
leftover = tuner.close()
for chunk in chunked(leftover, SINK_MAX_ROWS):
    sink_write_with_metric(chunk)
```

`close()` drains the **whole** buffer in one list — it is not capped at
`current_batch_size`. That cap is exactly the bug worth guarding against: the
latency throttle can shrink `B` below the depth already buffered, so a
size-capped shutdown drain returns part of the buffer and silently strands the
rest. For a tick or order-log sink that is data loss, and it is invisible
because the process is exiting anyway.

Because the drain is uncapped, the returned list may exceed what the sink
accepts in one statement — chunk it caller-side. After `close()`, `add_item`
raises `RuntimeError` (anything buffered post-shutdown would never be flushed);
`reset()` reopens the engine.

### 7. Logging shape

Every fill-driven transition emits a structured log:

```
event=tuning_transition
previous_batch_size=... new_batch_size=...
previous_flush_timeout_sec=... new_flush_timeout_sec=...
batch_fill_ratio_ewma=...
```

Latency throttle uses WARNING level (operator-visible):

```
event=latency_throttle
smoothed_write_latency_ms=... target_write_latency_ms=...
previous_batch_size=... new_batch_size=...
```

A failing `on_flush` callback logs `batch_tuner.on_flush_callback_failed` with a
traceback and does not lose the batch — the batch is still returned to the
caller.

Configure your logger to surface these as Prometheus counters / log-based alerts.

### 8. Failure modes & escalation

| Symptom | Probable cause | Action |
|---------|---------------|--------|
| `current_batch_size` sits at `B_min` while the feed is busy | Throttle stuck on, or `L_target` set below achievable sink latency | Compare `ewma_write_latency_ms` to `target_write_latency_ms`. If latency is genuinely high, this is correct behaviour and the sink is the problem. |
| `current_batch_size` pinned at `B_max`, latency above target | Should not happen — expansion is gated on latency headroom. Check for a custom `expand_multiplier` / `latency_throttle_multiplier` | Restore defaults; re-run the equilibrium check in §4. |
| `total_tuning_transitions` keeps climbing | Noisy fullness signal, or `L_target` sitting right at typical sink latency | Lower `fill_ewma_alpha` for more smoothing; set `L_target` from the typical, not the ideal, sink latency. |
| `QueueFullError` rising | Sink drain slower than producers | Implement a back-pressure pattern from `standards.md`; consider a sink upgrade. |
| `ewma_write_latency_ms` stuck near 0 with a real sink | `wait_for_async_insert = 0`, or latency recorded only on the success path | Fix the instrumentation; the throttle is blind until you do. |
| Records appear only when the feed is active | `flush_if_due()` is not being driven | Add the scheduler tick from §5. |
| A producer thread hangs inside `add_item` | Slow work inside an `on_flush` callback (it runs on the producer's thread) | Move the sink write to the returned-batch pattern. |

### 9. Deploy checklist

Before shipping:

- [ ] Run unit tests (`python -m unittest discover -s scripts -v`) — 43/43 pass.
- [ ] Inject production traffic for ≥10 minutes in staging; log `total_tuning_transitions`.
- [ ] Confirm `current_batch_size` rises under a burst rather than collapsing to `B_min`.
- [ ] Confirm `EWMA(L)` P99 < `L_target` over a 5-minute window.
- [ ] Confirm `get_status()` round-trips through your metrics pipeline with `allow_nan=False`.
- [ ] Confirm a kill -TERM path calls `close()` and writes the drained remainder.
- [ ] Configure an alert on `QueueFullError > 0` (see `backpressure-drop-degrade-policy` for the policy on what to do next).

## Production implementation reference

- Engine: `scripts/batch_tuner.py` (`AdaptiveBatchTunerEngine`, `TuningConfig`,
  `BatchTunerStatus`, `QueueFullError`).
- Tests: `scripts/test_batch_tuner.py` (43 unit tests).
- Operational checklist: `assets/checklist.md`.

## Cross-references

- Producer/consumer architecture: `producer-consumer-tick-pipeline`
- Kafka-specific guidance: `kafka-based-tick-distribution-at-scale`
- Back-pressure policy: `backpressure-drop-degrade-policy`
- Shutdown drain: `graceful-shutdown-draining-in-flight-ticks`
- Latency budget monitoring: `latency-monitoring-percentile-based-slas`
