# Pre-Flight / Sign-off Checklist — kill-switch-and-drawdown-circuit-breakers

Use this before considering the skill's implementation complete.

- [ ] **Independent Risk Veto:** Confirm `KillSwitchCircuitBreaker` operates outside strategy signal logic and vetoes order execution.
- [ ] **Daily Loss & Drawdown Halting:** Confirm daily loss breaches and peak-equity drawdown breaches halt trading and trigger `flatten_fn()`.
- [ ] **Broker Position Reconciliation:** Confirm position desync between internal logs and broker account triggers `HALTED_DESYNC`.
- [ ] **Human Re-Enable Gate:** Confirm system remains halted until `human_re_enable()` is invoked by an authorized operator.
- [ ] **Automated Testing:** Run `python scripts/test_circuit_breaker.py` and confirm 100% test pass rate.

## Sign-off

- Reviewed by: ___________________________
- Date: ___________________________
- Environment tested (paper/sandbox/live): ___________________________
