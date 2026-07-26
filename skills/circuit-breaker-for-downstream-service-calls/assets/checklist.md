# Pre-Flight Checklist

- [ ] Does the circuit breaker transition to the OPEN state after the configured failure threshold is hit?
- [ ] Are business-logic exceptions properly excluded from the failure counter?
- [ ] Is there a fallback mechanism or safe exception handling surrounding the circuit breaker call?
- [ ] Does the Half-Open state successfully allow the system to recover when the downstream service comes back online?
