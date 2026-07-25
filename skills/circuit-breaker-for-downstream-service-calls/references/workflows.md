# Deep Workflow Reference — circuit-breaker-for-downstream-service-calls

This file holds the full technical procedure referenced by `SKILL.md`.

## Full Procedure

1. **State Machine Management**:
   - Track state: `CLOSED` (normal) $\to$ `OPEN` (tripped) $\to$ `HALF_OPEN` (testing recovery).

2. **Failure Threshold Audit**:
   - Increment `consecutive_failures` on error or timeout.
   - If `consecutive_failures >= 3`, transition state to `OPEN`.

3. **Fail-Fast & Fallback Execution**:
   - While `OPEN`, fail fast immediately or return `fallback_fn()` without executing network calls.

4. **Cooldown & Recovery**:
   - After `cooldown_seconds=5.0`, transition to `HALF_OPEN` and allow trial requests to verify recovery.

## Production Implementation Reference

- Reference code: `scripts/circuit_breaker.py` (`ServiceCircuitBreaker`, `CircuitState`, `CircuitBreakerOpenException`).
- Automated unit tests: `scripts/test_circuit_breaker.py`.
