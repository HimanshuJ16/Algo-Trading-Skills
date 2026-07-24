# Pre-Flight / Sign-off Checklist — regime-detection-for-strategy-switching

Use this before considering the skill's implementation complete.

- [ ] **Indicator Calculations:** Confirm ADX and ATR Z-score are computed accurately over rolling bar windows.
- [ ] **Regime Classification:** Confirm raw candidate regimes match threshold definitions.
- [ ] **Hysteresis Filtering:** Confirm regime switches require $N=3$ consecutive bar candidate confirmations.
- [ ] **Strategy Routing:** Confirm `route_strategy_variant()` returns valid active strategy module names.
- [ ] **Automated Testing:** Run `python scripts/test_regime_detector.py` and confirm 100% test pass rate.

## Sign-off

- Reviewed by: ___________________________
- Date: ___________________________
- Environment tested (paper/sandbox/live): ___________________________
