# Production Workflows for Adaptive Sampling Under Extreme Tick Rates

## Ingestion Pipeline

1. **Initialize**: Create one `AdaptiveTickSamplerEngine` for each compatible sequence domain and record the target-rate configuration version.
2. **Validate feed identity upstream**: Confirm symbol, instrument, sequence domain, event timestamp, price scale, volume scale, and feed health before calling the sampler.
3. **Ingest**: Call `ingest_tick` for each valid trade. The call is synchronized and either returns a `SampledTick` or returns `None` while accumulating a sampled block.
4. **Persist/consume outside the lock**: Immediately hand emitted samples to the downstream queue or sink. Do not perform network I/O while holding the engine lock.
5. **Reconcile aggregates**: Maintain per-symbol input and output volume/notional counters and alert on tolerance breaches.
6. **Monitor**: Emit metrics for input/output rate, sampling factor, aggregate count, validation failures, flushes, queue latency, and consumer lag.

## Feed Recovery Workflow

1. Detect transport failure, sequence gap, duplicate policy violation, stale timestamp, or venue restart.
2. Stop treating the affected symbol as a continuous valid stream; quarantine or pause downstream decisions as required.
3. Flush accepted residual state using `flush(symbol)` and persist the synthetic output.
4. Reconcile the missing range using the venue/vendor recovery mechanism; the sampler must not invent missing trades.
5. Call `reset_symbol(flush=False)` only after the residual has been explicitly handled and the sequence domain is intentionally restarting.
6. Resume ingestion at the documented restart point and verify that sequence and timestamp monotonicity hold.

## Shutdown and Checkpoint Workflow

```python
flushed_ticks = sampler.flush_all()
for tick in flushed_ticks:
    downstream_sink.write(tick)
```

Persist the configuration version, flush timestamp, symbol, aggregate count, volume, notional, and reconciliation status. A flush record is synthetic and must not be mistaken for a raw exchange event.

## Failure Handling Matrix

| Failure | Sampler behavior | Required integration behavior |
|---|---|---|
| Invalid symbol/price/volume/timestamp | Raises `TypeError` or `ValueError`; state is unchanged | Reject and alert; do not retry unchanged input. |
| Duplicate/decreasing sequence | Raises `ValueError` when enforcement is enabled | Quarantine and reconcile the feed or intentionally reset the sequence domain. |
| Backwards event timestamp | Raises `ValueError` | Investigate clock/feed ordering; do not move rate state backwards. |
| Sampling overload | Emits aggregate samples with metadata | Apply downstream backpressure and monitor lag; sampling is not unlimited buffering. |
| Symbol retirement | `reset_symbol(flush=True)` returns residual aggregate and removes state | Persist residual output before deleting symbol state. |
| Process shutdown | `flush_all()` returns deterministic residual aggregates | Persist all outputs and verify volume/notional reconciliation. |

## Replay and Load-Test Workflow

1. Replay a baseline stream below target capacity and assert one output per valid trade.
2. Replay bursts above target capacity and verify factor changes, aggregate counts, output volume, and output notional.
3. Inject zero timestamps, NaN/∞ values, negative volume, duplicate sequence IDs, sequence gaps, and backwards timestamps; verify rejection and unchanged state.
4. Test multiple symbols concurrently and confirm state isolation and no race-induced accounting drift.
5. Exercise checkpoint, symbol reset, feed restart, and process shutdown; verify no residual volume is lost.
6. Compare raw-input and sampled-output reconciliation within the declared numerical tolerance before deployment.