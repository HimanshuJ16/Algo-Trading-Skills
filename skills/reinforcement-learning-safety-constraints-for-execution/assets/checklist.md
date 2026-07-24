# Pre-Flight / Sign-off Checklist — reinforcement-learning-safety-constraints-for-execution

Use this before considering the skill's implementation complete.

- [ ] **Action Clipping:** Confirm orders exceeding max size are clipped deterministically.
- [ ] **Position Cap Enforcement:** Confirm orders that would breach position caps are reduced to fit remaining capacity.
- [ ] **Spread Veto Guard:** Confirm market orders during wide spreads are vetoed.
- [ ] **Terminal Inventory Clearance:** Confirm remaining inventory is forced to liquidate near session end.
- [ ] **Automated Testing:** Run `python scripts/test_rl_safety_guard.py` and confirm 100% test pass rate.

## Sign-off

- Reviewed by: ___________________________
- Date: ___________________________
- Environment tested (paper/sandbox/live): ___________________________
