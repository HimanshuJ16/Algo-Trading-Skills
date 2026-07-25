# Pre-Flight / Sign-off Checklist — circuit-breaker-for-downstream-service-calls

Use this before considering the skill's implementation complete.

- [ ] **State Machine Configuration:** Confirm `CLOSED`, `OPEN`, and `HALF_OPEN` states transition as expected.
- [ ] **Failure Threshold Trip:** Confirm 3 consecutive failures trip state to `OPEN`.
- [ ] **Fail-Fast Enforcement:** Confirm `CircuitBreakerOpenException` or `fallback_fn` is returned when `OPEN`.
- [ ] **Cooldown Reset:** Confirm `HALF_OPEN` trial successes return state to `CLOSED`.
- [ ] **Automated Testing:** Run `python scripts/test_circuit_breaker.py` — 100% pass rate.

## Sign-off

- Reviewed by: ___________________________
- Date: ___________________________
