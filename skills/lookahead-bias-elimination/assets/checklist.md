# Pre-Flight / Sign-off Checklist — lookahead-bias-elimination

Use this before considering the skill's implementation complete.

**A clean run of every box below means no screen fired. It does not mean the backtest is
causal — read `references/standards.md` § Audit Limitations before signing.**

- [ ] **Decision Timestamp Recorded:** The exact decision instant and the execution lag are written down, not inferred from code.
- [ ] **Detector Calibrated First:** `run_timing_calibration()` returns exactly `1.0` on this frame. Any lower value is recorded here, with the reason: ___________________
- [ ] **Same-Bar Fill Verification:** `LookaheadBiasAuditor.audit_backtest_timing()` reports zero `SAME_BAR_FILL_CONTAMINATION` findings against Close, High **and** Low — or each finding is traced to a gapless bar and confirmed a false positive.
- [ ] **Unauditable Rows Reviewed:** Every `UNDETERMINED` finding (absent price column, NaN signal, NaN fill, NaN reference price at a signal bar) has been read and resolved. None was treated as a clean result.
- [ ] **Execution Bar Alignment:** Signals generated at bar $T$ execute at bar $T+\text{execution\_lag}$'s Open via `align_signal_execution()`, and the aligned frame re-audits with zero same-bar findings.
- [ ] **Panel Safety:** For any stacked multi-symbol frame, `symbol_col` and `timestamp_col` were passed so the shift cannot cross an instrument boundary.
- [ ] **Indicator Warmup Check:** `indicator_cols` was passed and no active signal sits on a NaN indicator. No indicator NaNs were filled, and no `min_periods` was lowered to suppress them.
- [ ] **Indicator Causality Fingerprint:** `audit_indicator_causality()` reports no centred window and no negative shift for each derived column — noting it recognises only those two constructions.
- [ ] **Point-In-Time Data Joins:** External data joins use strict as-of **publication** timestamps, and `check_feature_timestamps()` reports no breach at the decision instant (exact matches counted as breaches unless dissemination latency is already included).
- [ ] **Strategy Leak Calibration:** `run_leak_calibration()` injected a one-bar-forward leak and backtest performance inflated dramatically. If it did not, the backtest is not sensitive enough for its clean results to mean anything.
- [ ] **Walk-Forward Gap Reviewed:** In-sample vs strict out-of-sample performance compared; any large unexplained gap investigated as residual lookahead.
- [ ] **Automated Testing:** `python -m unittest discover -s skills/lookahead-bias-elimination/scripts` passes 100%.

## Sign-off

- Reviewed by: ___________________________
- Date: ___________________________
- Environment tested (paper/sandbox/live): ___________________________
