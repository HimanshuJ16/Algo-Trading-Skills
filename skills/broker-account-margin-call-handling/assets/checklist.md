# Pre-Flight / Sign-off Checklist — broker-account-margin-call-handling

Use this before considering the skill's implementation complete.

- [ ] **Ratio Evaluation:** Confirm `evaluate_margin_health()` calculates margin ratio accurately.
- [ ] **Multi-Tier Gates:** Confirm state transitions to `WARNING` (85%), `CRITICAL` (95%), and `BREACH` (100%).
- [ ] **Order Veto Gate:** Confirm `guard_new_order()` blocks leverage-increasing orders under margin stress.
- [ ] **De-leveraging Plan:** Confirm `plan_deleveraging()` calculates position reductions to restore margin safety buffer.
- [ ] **Automated Testing:** Run `python scripts/test_margin_call_engine.py` and confirm 100% test pass rate.

## Sign-off

- Reviewed by: ___________________________
- Date: ___________________________
- Environment tested (paper/sandbox/live): ___________________________
