# Pre-Flight Checklist

- [ ] Are heartbeat probes active on all cross-region database and Kafka topic links?
- [ ] Are both regions' hosts NTP/PTP-synchronised, with the measured sync bound configured as `clock_skew_tolerance_ms`?
- [ ] Are negative measured lags surfaced as `CLOCK_SKEW_SUSPECT` rather than clamped to zero?
- [ ] Does every heartbeat window hold at least `min_sample_count` (default 100) samples before a P99 is trusted?
- [ ] Are P95 and P99 percentiles calculated across rolling heartbeat windows the caller has already sorted/truncated?
- [ ] Is an empty heartbeat window treated as `UNKNOWN_NO_DATA` (fail over) rather than `HEALTHY`?
- [ ] Are non-finite (NaN/Inf) timestamps rejected loudly instead of silently producing a `HEALTHY` verdict?
- [ ] Is secondary replica status marked `UNSAFE_STALE` when P99 lag reaches $500\text{ ms}$ (inclusive threshold)?
- [ ] Are `samples_over_unsafe_threshold` and `max_lag_ms` reviewed alongside the P99, so rare multi-second stalls inside the discarded 1% are not read as "no stale reads"?
- [ ] Have the $100\text{ ms}$ / $500\text{ ms}$ bands been re-calibrated to your own RPO and measured baseline rather than adopted as published standards?
- [ ] Is automatic read-failover to primary database actually wired to `is_read_failover_recommended` — this module only emits the verdict?
