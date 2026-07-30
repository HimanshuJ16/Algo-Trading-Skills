# Workflows for Graceful Degradation Priority

1. **System Health Audit**:
   - Audit CPU utilization, network packet loss, and API response latencies.
2. **Priority Load Shedding**:
   - Drop P4 (analytics) during partial degradation; drop P2-P4 during critical outages.
3. **Queue Execution & Dispatch**:
   - Process P1 risk/cancel operations first without blocking.
4. **Degradation Audit Logging**:
   - Log shed task counts and system mode transitions.