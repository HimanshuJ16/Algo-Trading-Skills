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

### 2. Choose the capacity constants

Concrete starting values from vendor docs / our test experience:

| Sink | `B_min` | `B_max` | `T_min` | `T_max` | `L_target` |
|------|---------|---------|---------|---------|------------|
| TimescaleDB | 10 | 5000 | 0.05 s | 1.0 s | 50 ms |
| ClickHouse (async_insert=1) | 100 | 10000 | 0.05 s | 1.0 s | 100 ms |
| Kafka producer (with linger) | 16 KB | 256 KB | 0 ms | 5 ms | 25 ms |
| Redis Streams XADD batch | 50 | 2000 | 0.05 s | 0.5 s | 30 ms |

These are starting points. Run the verification flow of SKILL.md against
production traffic; tune `B_min` upward if writes are too small to be useful,
or `T_max` downward if `EWMA(L)` regularly exceeds `L_target`.

### 3. Compute the smoothed fill ratio

```python
def _update_fill_ewma_locked(self):
    raw = len(self._queue) / float(self._config.queue_capacity)
    self._ewma_fill_ratio = self._ewma_update(
        self._ewma_fill_ratio, raw, self._config.fill_ewma_alpha
    )
```

`alpha = 0.3` (default) means the EWMA settles to within 10% of a step input
after ~7 observations. At a flush rate of 10/s, that's ~0.7 s — fine for a
load-balancing controller. If load oscillates faster than that, the engine
will look "late"; lower `alpha` (more smoothing).

### 4. Apply the tuning curve

Pseudocode (mirroring `_tune_parameters_locked`):

```
if R_ewma > R_high:                     # high load
    B = min(B_max, floor(B * 1.5))
    T = max(T_min, T * 0.8)
elif R_ewma < R_low:                    # low load
    B = max(B_min, floor(B / 1.2))
    T = min(T_max, T * 1.2)
else:                                   # deadband
    pass
```

Latency throttle (in `record_write_latency`):

```
if EWMA(L) > L_target:
    B = max(B_min, floor(B * 0.8))
    # T is unchanged
```

### 5. Decide when to flush

Two flush triggers; the engine picks whichever first:

```
flush if len(queue) >= current_batch_size
   or elapsed >= current_flush_timeout_sec
```

`flush_now()` short-circuits for explicit checkpoints. `close()` drains the
whole queue on shutdown.

### 6. Logging shape

Every transition emits a structured log:

```
event=tuning_transition
previous_batch_size=... new_batch_size=...
previous_flush_timeout_sec=... new_flush_timeout_sec=...
queue_fill_ratio_ewma=...
```

Latency throttle uses WARNING level (operator-visible):

```
event=latency_throttle
smoothed_write_latency_ms=... target_write_latency_ms=...
previous_batch_size=... new_batch_size=...
```

Configure your logger to surface these as Prometheus counters / log-based alerts.

### 7. Failure modes & escalation

| Symptom | Probable cause | Action |
|---------|---------------|--------|
| `total_tuning_transitions` keeps climbing | Noisy upstream or `target_write_latency_ms` mis-set | Raise `target_write_latency_ms` toward typical sink latency. |
| `QueueFullError` rising | Sink drain slower than producers | Implement the back-pressure pattern from `standards.md`; consider sink upgrade. |
| `EWMA(L)` far below `L_target` for long periods | Latency throttle never fires; engine can't react | Lower `L_target` or check whether `record_write_latency` is being called at all. |
| `current_batch_size` stuck at `B_max` | Sustained congestion at sink, scheduler is filling the queue faster than draining | Check sink health (CPU, IOPS, connections). Likely a sink-side problem. |
| `current_batch_size` stuck at `B_min` | Idle / cold traffic | Verify producer loop is alive. |

### 8. Deploy checklist

Before shipping:

- [ ] Run unit tests (`python -m unittest discover -s scripts -v`) — 17/17 pass.
- [ ] Inject production traffic for ≥10 minutes in staging; log `total_tuning_transitions`.
- [ ] Confirm `EWMA(L)` P99 < `L_target` over a 5-minute window.
- [ ] Confirm `get_status()` round-trips through your metrics pipeline.
- [ ] Configure an alert on `QueueFullError > 0` (see `backpressure-drop-degrade-policy` for the policy on what to do next).

## Production implementation reference

- Engine: `scripts/batch_tuner.py` (`AdaptiveBatchTunerEngine`, `TuningConfig`,
  `BatchTunerStatus`, `QueueFullError`).
- Tests: `scripts/test_batch_tuner.py` (17 unit tests).
- Operational checklist: `assets/checklist.md`.

## Cross-references

- Producer/consumer architecture: `producer-consumer-tick-pipeline`
- Kafka-specific guidance: `kafka-based-tick-distribution-at-scale`
- Back-pressure policy: `backpressure-drop-degrade-policy`
- Latency budget monitoring: `latency-monitoring-percentile-based-slas`
