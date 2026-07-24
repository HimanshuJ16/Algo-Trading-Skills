# Pre-Flight / Sign-off Checklist — lookahead-bias-elimination

Use this before considering the skill's implementation complete.

- [ ] **Same-Bar Fill Verification:** Run `LookaheadBiasAuditor.audit_backtest_timing()` and confirm zero signals execute at same-bar Close.
- [ ] **Execution Bar Alignment:** Confirm signals generated at bar $T$ are shifted to execute at bar $T+1$'s Open via `align_signal_execution()`.
- [ ] **Indicator Warmup Check:** Confirm trades before indicator warmup completion are excluded.
- [ ] **Point-In-Time Data Joins:** Confirm external data joins use strict as-of publication timestamps.
- [ ] **Forward-Leak Calibration:** Run `run_leak_calibration()` to confirm backtest sensitivity.
- [ ] **Automated Testing:** Run `python scripts/test_leak_audit.py` and confirm 100% test pass rate.

## Sign-off

- Reviewed by: ___________________________
- Date: ___________________________
- Environment tested (paper/sandbox/live): ___________________________
