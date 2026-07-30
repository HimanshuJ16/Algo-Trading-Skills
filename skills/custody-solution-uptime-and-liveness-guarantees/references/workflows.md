# Workflows for Custody Solution Uptime and Liveness Guarantees

1. **Telemetry Parsing**:
   - Collect API ping, active MPC node count, and signing latency.
2. **Uptime & Quorum Evaluation**:
   - $\text{Uptime} = (N_{\text{healthy}} / N_{\text{total}}) \times 100\%$.
   - Verify $\text{Active Nodes} \ge k$.
3. **P99 Latency Calculation**:
   - Compute $P99(t_{\text{signing}})$ over rolling window.
4. **Failover Execution**:
   - Switch trade routing to secondary custody provider upon SLA breach.