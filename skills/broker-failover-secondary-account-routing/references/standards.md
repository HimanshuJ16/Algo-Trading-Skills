# Institutional Quant Standards for Broker Failover

In professional high-frequency and mid-frequency quant trading environments, broker failover is not a "nice-to-have" but a critical compliance and risk management requirement. 

## Key Standards
1. **The Circuit Breaker Pattern**
   - **CLOSED State**: Standard operating mode. All flow routes to primary.
   - **OPEN State**: Primary is deemed unhealthy (X consecutive failures, or latency > Y ms). Flow is instantly rerouted to the secondary/backup broker.
   - **HALF-OPEN State**: After a timeout, a "probe" order (usually a tiny lot size or a simple ping) is routed to the primary broker to verify recovery before resuming full flow.

2. **Idempotency & Duplicate Prevention**
   - Orders must be uniquely tagged across both primary and secondary routing layers. If a primary broker times out, the router must ensure it didn't silently execute the order before routing to the secondary (e.g., via FIX protocol sequence numbers or REST UUIDs).

3. **Symbol Mapping**
   - Different brokers have different canonical namespaces for assets. A failover router MUST maintain a robust translation dictionary.

4. **Concurrency**
   - Routing layers must be thread-safe or utilize async locks to handle high-throughput order bursts from multiple strategies simultaneously.
