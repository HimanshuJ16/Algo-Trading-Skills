# Workflows for Circuit Breaker Pattern

1. **Service Identification**:
   - Identify non-critical synchronous calls in your trading loop (e.g., fetching a supplementary risk limit from a REST API).
2. **Configuration**:
   - `failure_threshold`: How many consecutive errors trigger the breaker (e.g., 3).
   - `recovery_timeout`: How long to wait before trying again (e.g., 15 seconds).
   - `expected_exceptions`: Tuple of exceptions that count as failures (e.g., `requests.Timeout`, `ConnectionError`).
3. **Implementation**:
   - Use a decorator or context manager around the downstream call.
   - Example fallback logic: If the historical data API circuit is open, fallback to the last cached value and log a warning, allowing the trading loop to continue seamlessly.
4. **Monitoring Integration**:
   - Expose the circuit state (Closed, Open, Half-Open) as a Prometheus gauge or StatsD metric.
   - Alert the on-call engineer immediately when state transitions to `OPEN`.
