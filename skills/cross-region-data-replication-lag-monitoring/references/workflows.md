# Workflows for Cross-Region Data Replication Lag Monitoring

1. **Heartbeat Ingestion**:
   - Collect heartbeat write timestamp $t_{\text{primary}}$ (primary's clock) and read timestamp $t_{\text{replica}}$ (replica's clock), epoch milliseconds.
   - Reject non-finite timestamps loudly. A NaN silently defeats every downstream threshold comparison.
   - The caller supplies exactly the heartbeats belonging in the rolling window; `evaluate_replica_health` filters by region pair only.
2. **Lag Calculation**:
   - $\Delta t = t_{\text{replica}} - t_{\text{primary}}$, kept **signed**. Do not clamp negatives to zero.
3. **Percentile Computation**:
   - Compute P95 and P99 over the window (linear interpolation between order statistics).
4. **Trust Gates** (each fails safe — read failover recommended, no health claim):
   - No heartbeats for the pair $\implies$ `UNKNOWN_NO_DATA`.
   - Any lag $< -\texttt{clock\_skew\_tolerance\_ms}$ $\implies$ `CLOCK_SKEW_SUSPECT`.
   - Sample count $<$ `min_sample_count` (default 100) $\implies$ `UNKNOWN_INSUFFICIENT_SAMPLES`.
5. **Health Classification** (inclusive thresholds; unsafe lag is evaluated before the trust gates so a real observed spike always escalates):
   - $\text{P99} \ge 500\text{ms} \implies$ `UNSAFE_STALE` (trigger read-failover to primary).
   - $100\text{ms} \le \text{P99} < 500\text{ms} \implies$ `DEGRADED_WARNING` (alert only).
   - $\text{P99} < 100\text{ms}$, window large enough and skew-free $\implies$ `HEALTHY`.
6. **Sub-Percentile Spike Accounting**:
   - `samples_over_unsafe_threshold` counts individual heartbeats at or above the unsafe threshold and `max_lag_ms` reports the worst; both are populated regardless of status. A P99 discards the worst 1% of samples, so rare multi-second stalls never move it — review these two fields before declaring a window clean.
7. **Act on the Verdict**:
   - `is_read_failover_recommended` is advisory output. Route it into the component that owns read routing — this module cannot block a read.
   - Investigate `CLOCK_SKEW_SUSPECT` as a clock incident (NTP/PTP), not as a replication incident; the lag numbers in that window are unusable until it is fixed.
