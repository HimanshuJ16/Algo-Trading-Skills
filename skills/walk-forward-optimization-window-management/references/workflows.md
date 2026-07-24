# Deep Workflow Reference — walk-forward-optimization-window-management

This file holds the full technical procedure referenced by `SKILL.md`. Load this when
actually implementing the skill, not just when deciding whether it applies.

## Full Procedure

1. **Select Window Geometry & Mode:**
   - Choose `ROLLING` (fixed IS length) or `ANCHORED` (expanding IS length).
   - Set parameters: `in_sample_days`, `out_of_sample_days`, `step_days`, `warmup_days`.

2. **Generate Window Slices:**
   - Invoke `WalkForwardWindowManager.generate_windows(start_date, end_date)`.
   - Each slice includes `warmup_start`, `is_start`, `is_end`, `oos_start`, and `oos_end`.

3. **Verify Temporal Isolation:**
   - Call `validate_window_isolation()` to confirm $\max(T_{\text{IS}}) < \min(T_{\text{OOS}})$.

4. **Execute Optimization Loop:**
   - Optimize parameters $P^*$ over In-Sample window.
   - Evaluate top parameter set $P^*$ over Out-of-Sample window.

5. **Stitch OOS Results & Compute WFE:**
   - Concatenate non-overlapping OOS equity returns.
   - Calculate Walk-Forward Efficiency $\text{WFE} = \frac{\text{Sharpe}_{\text{OOS}}}{\text{Sharpe}_{\text{IS}}}$. Require $\text{WFE} \ge 0.50$.

## Failure Modes Observed in Production

- **Temporal Window Overlap:** In-Sample training bars overlapping with Out-of-Sample evaluation bars, producing inflated backtest results.
- **Warming Contamination:** Counting indicator warm-up bars in out-of-sample performance metrics.

## Production Implementation Reference

- Reference code: `scripts/walk_forward_manager.py` (`WalkForwardWindowManager`, `WindowSlice`, `WFEEvaluation`).
- Automated unit tests: `scripts/test_walk_forward_manager.py`.
