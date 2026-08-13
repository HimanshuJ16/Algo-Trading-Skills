# Deployment and Sign-off Checklist — Adaptive Sampling Under Extreme Tick Rates

## Prerequisites

- [ ] Confirm the source is a trade-tick stream and document why sampled output is acceptable for each downstream consumer.
- [ ] Document symbol identity, sequence domain, event-time semantics, price scale, volume scale, and feed restart behavior.
- [ ] Measure downstream capacity, target latency, queue capacity, and the configured `target_max_rate_per_sec`.
- [ ] Confirm durable handling for emitted samples, validation errors, flush records, and volume/notional reconciliation.
- [ ] Confirm downstream consumers understand `sampling_factor`, `aggregated_tick_count`, and `is_flush`.

## Validation

- [ ] Run `python -m unittest discover -s skills/adaptive-sampling-under-extreme-tick-rates/scripts`.
- [ ] Verify invalid symbols, non-finite prices/volumes/timestamps, non-positive values, duplicate sequences, and backwards timestamps are rejected.
- [ ] Verify passthrough and sampled boundaries, including `rate == target` and `rate > target`.
- [ ] Verify output volume and price-volume notional reconcile to raw input within the documented tolerance.
- [ ] Run multi-symbol and concurrent-ingestion tests with no state contamination.

## Deployment

- [ ] Create one engine per compatible sequence domain and load a versioned target-rate configuration.
- [ ] Keep persistence/network operations outside the sampler lock.
- [ ] Enable metrics for input/output rate, sampling factor, aggregate count, validation failures, flushes, lag, and reconciliation.
- [ ] Confirm feed-gap recovery pauses/quarantines affected symbols before resuming sampling.
- [ ] Confirm downstream risk, market-status, and compliance consumers do not use sampled output where full fidelity is required.

## Rollback and Recovery

- [ ] On feed reset or sequence-domain change, flush accepted residuals and persist them before `reset_symbol`.
- [ ] On reconciliation mismatch, stop downstream use of affected aggregates and retain raw/replay evidence.
- [ ] On process shutdown, run `flush_all()` and verify every returned aggregate is persisted.
- [ ] Keep the last known-good target-rate configuration and configuration version available for rollback.
- [ ] Resume only after sequence, timestamps, feed health, downstream lag, and reconciliation are healthy.

## Post-Deployment Verification

- [ ] Compare raw and sampled volume/notional totals by symbol and session.
- [ ] Review output reduction, consumer latency, queue depth, validation failures, and flush frequency.
- [ ] Confirm no duplicate, out-of-order, or malformed ticks enter the accepted state path.
- [ ] Review symbols with persistent overload and recalibrate capacity or use a broader backpressure policy.
- [ ] Record reviewer, deployment version, calibration version, and sign-off date.

## Sign-off

- Reviewed by: ___________________________
- Date: ___________________________