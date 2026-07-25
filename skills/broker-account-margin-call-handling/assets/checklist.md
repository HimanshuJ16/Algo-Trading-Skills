# Pre-Flight / Sign-off Checklist — broker-account-margin-call-handling

Use this before considering the skill's implementation complete.

- [ ] **Ratio Evaluation:** Confirm `evaluate_margin_health()` calculates both initial and maintenance margin ratios accurately.
- [ ] **Multi-Tier Gates:** Confirm state transitions to `WARNING` (85%), `CRITICAL` (95%), and `BREACH` (100%).
- [ ] **Predictive Order Veto Gate:** Confirm `guard_new_order()` calculates projected margin impact and blocks leverage-increasing orders under margin stress.
- [ ] **Tail-Risk Prioritization:** Confirm `plan_deleveraging()` sorts unhedged short options first for liquidation.
- [ ] **Liquidity Capping:** Confirm `plan_deleveraging()` applies an ADV (Average Daily Volume) maximum participation rate to prevent crashing illiquid assets.
- [ ] **Automated Testing:** Run `python -m unittest test_margin_call_engine.py` and confirm 100% test pass rate.

## Sign-off

- Reviewed by: ___________________________
- Date: ___________________________
- Environment tested (paper/sandbox/live): ___________________________
