# Pre-Flight / Sign-off Checklist — order-placement-idempotency

Use this before considering the skill's implementation complete.

- [ ] **Write-Ahead Intent Logging:** Confirm `record_intent()` writes `PENDING` intent to SQLite database BEFORE network HTTP calls.
- [ ] **State Transition Machine:** Confirm 4-state transitions (`PENDING`, `PLACED`, `REJECTED`, `UNKNOWN`) are strictly enforced.
- [ ] **Timeout Reconciliation:** Confirm HTTP timeouts transition order to `UNKNOWN` and invoke `_reconcile_unknown()`.
- [ ] **Startup Crash Recovery:** Confirm unresolved `PENDING`/`UNKNOWN` entries are reconciled against broker order book at startup.
- [ ] **Automated Testing:** Run `python scripts/test_order_ledger.py` and confirm 100% test pass rate.

## Sign-off

- Reviewed by: ___________________________
- Date: ___________________________
- Environment tested (paper/sandbox/live): ___________________________
