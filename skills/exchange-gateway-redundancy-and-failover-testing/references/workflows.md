# Workflows for Exchange Gateway Redundancy and Failover Testing

1. **Liveness Monitoring**:
   - Continuously monitor FIX heartbeat interval and socket latency.
2. **Failure Detection**:
   - Detect primary gateway disconnection or timeout.
3. **Failover Execution**:
   - Promote secondary gateway to active state and sync sequence numbers.
4. **In-Flight Order Reconciliation**:
   - Re-send pending orders with PossDupFlag enabled.
