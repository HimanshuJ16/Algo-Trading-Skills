# Workflows for Cross-Region Data Replication Lag Monitoring

1. **Heartbeat Ingestion**:
   - Collect heartbeat write timestamp $t_{\text{primary}}$ and read timestamp $t_{\text{replica}}$.
2. **Lag Calculation**:
   - $\Delta t = t_{\text{replica}} - t_{\text{primary}}$.
3. **Percentile Computation**:
   - Compute P95 and P99 over rolling window.
4. **Health Classification**:
   - $\text{P99} \le 100\text{ms} \implies$ `HEALTHY`.
   - $100\text{ms} < \text{P99} \le 500\text{ms} \implies$ `DEGRADED_WARNING`.
   - $\text{P99} > 500\text{ms} \implies$ `UNSAFE_STALE` (Trigger read-failover to primary).
