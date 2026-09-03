# Tick-to-Trade Latency Measurement Checklist

## Capture path
- [ ] **Six capture points recorded**, not five: $T_0$ NIC hardware RX, $T_1$ user-space read, $T_2$ decode complete, $T_3$ signal + pre-trade risk complete, $T_4$ order encoded / socket write, $T_5$ NIC hardware TX.
- [ ] **Hardware timestamping confirmed at the adapter**, not inferred: `ethtool -T <iface>` reports `SOF_TIMESTAMPING_RX_HARDWARE` / `SOF_TIMESTAMPING_TX_HARDWARE`. The software path returns a populated `SO_TIMESTAMPING` field whether or not the NIC is doing the work.
- [ ] **In-host counter chosen and documented** for $T_1 \dots T_4$: `CLOCK_MONOTONIC_RAW`, or `rdtsc` with the invariant bit (`CPUID.80000007H:EDX[8]`) confirmed and the frequency calibrated. `time.time()` / `CLOCK_REALTIME` is disqualified — NTP steps it and a stage can measure negative.
- [ ] **PHC-to-host conversion documented**, with the daemon (`ptp4l` / `phc2sys` / `sfptpd`) and the offset source named. The kernel does not do this conversion for you.
- [ ] **Combined two-clock uncertainty measured** and set as `SLAConfig.timestamp_uncertainty_us`.
- [ ] **Capture is allocation-free**: timestamps written into a pre-allocated ring buffer, no logging, no formatting, no dictionary construction on the hot path.
- [ ] **Aggregation runs off the hot path**, on a drained buffer, never inside the trading thread.

## Sample hygiene
- [ ] **Timestamps are integer nanoseconds.** No floats (binary64 spacing at epoch-scale ns is 256 ns), no booleans.
- [ ] **Non-monotonic samples are quarantined per sample and COUNTED**, never clamped and never silently dropped — instrumentation defects correlate with the slow path, so dropping them biases the tail downwards.
- [ ] **Quarantine rate alerted on**, with a threshold agreed in advance.
- [ ] **Zero-length stages investigated, not celebrated**: a 0 ns stage means the timer could not resolve it.
- [ ] **Feed-handler tick drops counted separately.** This measurement only sees ticks that produced an order; a pipeline that drops ticks under load hides its own worst samples.

## Measurement window
- [ ] **Window sized for the percentile being audited**: ≥ 100 samples for P99, ≥ 1,000 for P99.9. Verify with `min_samples_for_percentile`.
- [ ] **Window captured under load**, at or above realistic peak message rates — RTS 6 Art. 10's "highest number of messages ... during the previous six months, multiplied by two" is a defensible target regardless of jurisdiction.
- [ ] **`max_samples` set** if the drain loop could outrun evaluation. Confirm the handler treats the raise as a signal to evaluate and reset, never to discard.

## Reporting and interpretation
- [ ] **Percentile estimator stated in the report.** Nearest rank (default) returns observed latencies; linear interpolation can return a value the system never produced.
- [ ] **`sla_status` read, not `sla_breaches == []`.** `T2T_INSUFFICIENT_SAMPLES_FOR_SLA` is not an approval.
- [ ] **`resolution_warnings` empty** before any figure goes on a dashboard or into a sign-off.
- [ ] **Per-stage percentiles are NOT summed.** The sum of stage P99s is not the T2T P99, and the error runs in both directions.
- [ ] **Tail diagnosed from `tail_attribution`**, not from `percentage_of_total` — the latter decomposes the mean.
- [ ] **`dominant_stage` cross-checked against `below_noise_floor`** before any optimisation work is scheduled, especially when the dominant stage is `NIC_INGRESS` or `NIC_EGRESS`.
- [ ] **SLA budgets calibrated, not inherited.** No regulator, exchange or standards body publishes a tick-to-trade latency SLA; the shipped 5 / 15 / 50 / 100 µs figures are engineering starting points.
- [ ] **Report states the units**: this module is microseconds; `colocation-latency-budget-accounting` is nanoseconds.

## Sign-off
- [ ] `python -m unittest discover -s skills/tick-to-trade-latency-measurement/scripts` passes.
- [ ] The measurement window, message rate, clock configuration and estimator are recorded alongside the numbers, so the figures can be reproduced and compared against a later run.
