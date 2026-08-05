# Workflows for Smart Order Router Failover on Venue Outage

1. **Venue Health Auditing**:
   - Track consecutive error counts per exchange connection.
2. **Circuit Breaker Trip**:
   - Transition venue state to `CIRCUIT_BROKEN_OUTAGE` when error limit is exceeded.
3. **Route Selection & Failover**:
   - Filter active venues to healthy candidates only; select best execution price.
4. **Fallback Audit Logging**:
   - Record failover event and fallback venue sequence in execution report.
