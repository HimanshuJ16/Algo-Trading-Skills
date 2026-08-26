# Pre-Flight Checklist

## Timestamp normalisation (the step that decides whether any of this means anything)
- [ ] Are all four stamps converted to **microseconds on one common epoch** before constructing a `LatencySample`?
- [ ] For a midnight-relative venue stamp (ITCH-style), are the session date and venue timezone supplied from outside the feed?
- [ ] Is the midnight/session rollover handled, so the boundary does not turn a positive latency into a negative one?
- [ ] Is the NIC's PTP hardware clock disciplined to the system clock (`phc2sys`), or are hardware timestamps converted before use?
- [ ] Is `timestamp_quantum_us` below 1 µs — i.e. can the float64 representation separate adjacent microseconds at this magnitude?
- [ ] If sub-microsecond figures are needed, are timestamps rebased against a **session epoch** rather than the Unix epoch (0.25 µs spacing at 1.8e15)?

## Clock-domain integrity
- [ ] Is `reject_clock_inconsistent_windows` left at `True`?
- [ ] Is `clock_inconsistent_sample_count` checked on every vendor, not just the ones that breached?
- [ ] When a segment goes negative, has the **pair of clocks** it names been diagnosed before re-auditing?
- [ ] Is `VENDOR_CLOCK_DOMAIN_ERROR` treated as "not measured" rather than as a pass?

## Sample sufficiency
- [ ] Does the window hold at least **100 samples** for a P99 verdict, and **1,000** for P99.9?
- [ ] Is `INSUFFICIENT_SAMPLES_FOR_SLA` treated as "not measured" rather than as a pass?
- [ ] Is `is_audited_percentile_resolvable` checked before quoting a tail figure to a vendor?

## Input integrity
- [ ] Are NaN/Inf samples rejected rather than filtered?
- [ ] Are timestamps in the plausible magnitude range — no nanosecond stamps in microsecond fields?

## Percentiles
- [ ] Is the estimator nearest rank (HdrHistogram-compatible), and is `percentile_method` recorded with the result?
- [ ] If interpolation is used instead, is it understood that the reported value may never have occurred?
- [ ] Are SLA comparisons made on unrounded values?

## Budgets and verdicts
- [ ] Is `max_allowed_p99_latency_us` **calibrated for this feed and this co-location site**, not left at the shipped 500 µs?
- [ ] Is it understood that no regulator, exchange or vendor publishes a microsecond feed latency SLA?
- [ ] Does `audited_percentile` match the percentile the actual obligation attaches to?

## Measurement noise floor
- [ ] Is `clock_uncertainty_us` set to the combined two-clock uncertainty?
- [ ] Is the budget larger than that noise floor — i.e. measurable at all?
- [ ] Is it understood that the vendor's gateway clock carries **no** regulated accuracy guarantee, so the `VENDOR_TRANSPORT` uncertainty is the largest of the three?

## Attribution
- [ ] Is `dominant_tail_segment` used to name the slow hop, rather than comparing segment means?
- [ ] Is it understood that percentiles are not additive, so the segment P99s do not sum to the total P99?

## Aggregation
- [ ] Are vendor-level percentiles computed over **pooled raw samples**, never by averaging per-symbol or per-node percentiles?
- [ ] Are vendor IDs consistent, so one feed does not split into two distributions?

## Jitter
- [ ] Are **both** σ and IQR monitored?
