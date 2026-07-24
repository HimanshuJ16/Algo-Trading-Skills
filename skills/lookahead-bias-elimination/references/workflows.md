# Deep Workflow Reference — lookahead-bias-elimination

This file holds the full technical procedure referenced by `SKILL.md`. Load this when
actually implementing the skill, not just when deciding whether it applies.

## Full Procedure

1. **Same-Bar Fill Contamination Audit:**
   - Audit backtest code using `LookaheadBiasAuditor.audit_backtest_timing()`.
   - Ensure signals generated at bar $T$ are never filled at bar $T$'s Close, High, or Low.

2. **Signal-to-Execution Bar Alignment:**
   - Use `LookaheadBiasAuditor.align_signal_execution()` to shift signals from bar $T$ to execute at bar $T+1$'s Open price.

3. **Indicator Warmup Validation:**
   - Ensure backtest execution ignores signals triggered before the indicator warmup period ($T < \text{warmup\_periods}$).

4. **Point-In-Time Data Join Verification:**
   - Verify that non-price features (fundamentals, corporate actions, IV surfaces) are joined using strict as-of publication timestamps.

5. **Forward-Leakage Calibration Benchmark:**
   - Inject a known +1 bar forward leak (`run_leak_calibration()`) to calibrate backtest sensitivity to lookahead bias.

## Failure Modes Observed in Production

- **Same-Bar Close Fills:** Generating a signal from bar $T$'s Close and filling the order at bar $T$'s Close, assuming impossible zero-latency same-bar execution.
- **Unwarmed Indicator Trading:** Executing trades during early backtest bars before rolling window indicators (SMA, ATR, RSI) have fully formed.
- **Centered Vectorized Rolling Windows:** Using pandas rolling windows with `center=True`, leaking future bar data into current indicator values.
- **Post-Restated Fundamental Data:** Joining fundamental financial statements by fiscal quarter date rather than actual SEC/regulatory filing date.

## Production Implementation Reference

- Reference code: `scripts/leak_audit.py` (`LookaheadBiasAuditor`, `LookaheadViolationType`, `align_signal_execution`).
- Automated unit tests: `scripts/test_leak_audit.py`.
