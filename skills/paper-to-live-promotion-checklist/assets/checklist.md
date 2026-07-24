# Pre-Flight / Sign-off Checklist — paper-to-live-promotion-checklist

Use this before considering the skill's implementation complete.

- [ ] **Multi-Criteria Gate Validation:** Confirm `evaluate_gate()` evaluates duration ($\ge 20$ days), trades count, slippage alignment, and accuracy alignment.
- [ ] **Risk Controls Verification:** Confirm at least 1 risk control trigger event is recorded in paper trading logs.
- [ ] **Reduced Live Sizing:** Confirm initial live deployment starts at a reduced position sizing fraction (e.g. 25% target size).
- [ ] **Rollback Trigger Rules:** Confirm `check_rollback_trigger()` automatically reverts to paper trading on live performance divergence.
- [ ] **Automated Testing:** Run `python scripts/test_promotion_gate.py` and confirm 100% test pass rate.

## Sign-off

- Reviewed by: ___________________________
- Date: ___________________________
- Environment tested (paper/sandbox/live): ___________________________
