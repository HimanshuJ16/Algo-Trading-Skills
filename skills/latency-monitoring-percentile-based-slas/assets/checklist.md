# Pre-Flight Checklist

## Sample capture
- [ ] Were intervals measured with a **monotonic** clock (`CLOCK_MONOTONIC_RAW`, `perf_counter_ns`) rather than a wall clock?
- [ ] For two-host measurements, is the divergence of both clocks from UTC known?
- [ ] Is the sampler's fixed cadence recorded, if it has one?

## Sample sufficiency
- [ ] Does the window hold at least **100 samples** for a P99 verdict, and **1,000** for P99.9?
- [ ] Is `INSUFFICIENT_SAMPLES_FOR_SLA` treated as "not measured" rather than as a pass?
- [ ] Are `is_p99_resolvable` / `is_p999_resolvable` checked before quoting a tail figure?

## Input integrity
- [ ] Are NaN/Inf samples rejected rather than filtered?
- [ ] Are negative samples treated as invalidating the **whole window**, not just themselves?

## Percentiles
- [ ] Is the estimator nearest rank (HdrHistogram-compatible), and is `percentile_method` recorded with the result?
- [ ] If interpolation is used instead, is it understood that the reported value may never have occurred?
- [ ] Are SLA comparisons made on unrounded values?

## Coordinated omission
- [ ] Could the sampler have stopped sampling during a stall?
- [ ] If it has a fixed cadence, is `expected_sample_interval_us` set — and applied exactly once?

## Budgets and verdicts
- [ ] Are the SLA budgets **calibrated for this system**, not inherited from this skill's defaults?
- [ ] Is it understood that no regulator or exchange publishes a latency SLA?
- [ ] Is a P50 breach with healthy tails surfaced as its own finding, not an approval?

## Measurement noise floor
- [ ] Is `clock_uncertainty_us` set to the combined two-clock uncertainty?
- [ ] Is the SLA budget larger than that noise floor — i.e. measurable at all?

## Jitter
- [ ] Are **both** σ and IQR monitored?

## Fleet aggregation
- [ ] Are cross-node percentiles computed over **pooled raw samples**, never by averaging per-node percentiles?
