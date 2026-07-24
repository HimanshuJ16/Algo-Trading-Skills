# Pre-Flight / Sign-off Checklist — survivorship-bias-free-universe-construction

Use this before considering the skill's implementation complete.

- [ ] **Point-in-Time Query:** Confirm `get_active_universe()` returns symbols active on target date $T$.
- [ ] **Delisting Settlement:** Confirm `process_delisting_settlement()` computes terminal cash value for bankruptcy and mergers.
- [ ] **Bankruptcy Zero Mark:** Confirm bankruptcy delisting event marks open long position to $0.00$.
- [ ] **Bias Audit:** Confirm `audit_survivorship_bias()` reports non-zero delisted ratio across historical backtest window.
- [ ] **Automated Testing:** Run `python scripts/test_universe_builder.py` and confirm 100% test pass rate.

## Sign-off

- Reviewed by: ___________________________
- Date: ___________________________
- Environment tested (paper/sandbox/live): ___________________________
