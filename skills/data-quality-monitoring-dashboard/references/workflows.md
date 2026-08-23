# Workflows for Data Quality Monitoring Dashboard

1. **Engine Configuration**:
   - Set `min_healthy_score`, `critical_failover_score`, penalty factors, `latency_zero_score_ms`
     and pillar weights. The constructor rejects weights that do not sum to 1.0 and a
     `critical_failover_score` that is not strictly below `min_healthy_score`.
2. **Batch Telemetry Collection**:
   - Collect record counts, nulls, duplicates, outliers, average latency, and TPS over a
     fixed window per `(vendor_id, symbol)`.
   - Defect counts may overlap (a duplicate can also be an outlier), so they need not sum
     to `total_records`, but each must be `<= total_records`.
3. **Telemetry Validation**:
   - `audit_feed_quality` raises `ValueError` on structurally impossible telemetry
     (negative counts, a defect count above `total_records`, non-finite or negative
     latency/tick rate). Treat that exception as a collector defect and alert on it —
     do not swallow it, or a broken collector will look like a silent healthy feed.
4. **Pillar Scoring** (each floored at 0.0, rounded to 2dp before weighting):
   - $S_{\text{comp}} = 100 - \text{NullPct} \times f_{\text{null}}$ (default $f = 2.0$).
   - $S_{\text{time}} = 100 \times \left(1 - \frac{\text{LatencyMs}}{L_0}\right)$ (default $L_0 = 500\text{ ms}$).
   - $S_{\text{acc}} = 100 - \text{OutlierPct} \times f_{\text{out}}$ (default $f = 5.0$).
   - $S_{\text{uniq}} = 100 - \text{DupPct} \times f_{\text{dup}}$ (default $f = 2.0$).
   - $S_{\text{live}} = 100$ if $\text{TPS} > 0$ else $0$.
5. **Composite Index Calculation**:
   - $\text{DQ Score} = 0.25 S_{\text{comp}} + 0.25 S_{\text{time}} + 0.25 S_{\text{acc}} + 0.15 S_{\text{uniq}} + 0.10 S_{\text{live}}$.
6. **Alerting & Failover** (first matching branch wins):
   - $\text{TPS} = 0 \implies$ `CRITICAL` + failover, regardless of composite score.
   - $\text{DQ Score} < 70.0 \implies$ `CRITICAL` + failover.
   - $\text{DQ Score} < 85.0 \implies$ `WARNING`, no failover.
   - Otherwise `HEALTHY`. Thresholds are strict `<`, so a score exactly equal to a
     threshold sits in the healthier band.
