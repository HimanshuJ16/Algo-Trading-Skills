# Deep Workflow Reference — walk-forward-optimization-window-management

This file holds the full technical procedure referenced by `SKILL.md`. Load this when
actually implementing the skill, not just when deciding whether it applies.

## Full Procedure

1. **Size the embargo before anything else:**
   - Enumerate every feature the strategy computes and record the longest lookback `L` in bars.
   - Enumerate every label/target and record the longest forward horizon `H` in bars.
   - Set `embargo_days >= max(L, H)` converted to calendar days. This is the single input that
     decides whether the split is genuinely leak-free; the window lengths do not.
   - If the strategy has no lookback and no multi-bar label, `embargo_days = 0` is correct — but
     that is rare, and the helper logs a warning when it happens so the choice is visible.

2. **Select Window Geometry & Mode:**
   - Choose `ROLLING` (fixed IS length) or `ANCHORED` (expanding IS length). Rolling suits
     fast-changing regimes; anchored suits structural series where old data stays relevant.
   - Set `in_sample_days`, `out_of_sample_days`, `step_days`, `warmup_days`.
   - Keep `step_days >= out_of_sample_days`. Equality gives a contiguous stitched OOS curve;
     a larger step leaves untested calendar gaps (legal, logged); a smaller step overlaps and is
     rejected unless `allow_overlapping_oos=True` is passed deliberately.
   - Confirm the dataset spans at least `min_required_days()` inclusive days, plus `warmup_days`
     of history before `start_date`.

3. **Generate Window Slices:**
   - Invoke `WalkForwardWindowManager.generate_windows(start_date, end_date)` with plain
     `datetime.date` bounds, both inclusive.
   - Each slice includes `warmup_start`, `is_start`, `is_end`, `embargo_start`, `embargo_end`,
     `oos_start`, and `oos_end`. The embargo bounds are `None` when `embargo_days == 0`.

4. **Verify Temporal Isolation:**
   - `validate_window_isolation(slice, min_embargo_days)` runs automatically inside
     `generate_windows`. It confirms $\max(T_{\text{IS}}) < \min(T_{\text{OOS}})$, that the
     realised gap covers the configured embargo, and that the warm-up and embargo bounds do not
     intrude into the IS or OOS intervals.
   - `validate_slice_sequence(slices)` confirms the sequence is chronological with
     non-overlapping OOS intervals. Call it again after any manual filtering of slices.

5. **Execute Optimization Loop:**
   - Load `[warmup_start, is_start - 1]` purely to prime indicator state. Never score it.
   - Optimize parameters $P^*$ over `[is_start, is_end]`.
   - Drop `[embargo_start, embargo_end]` from both training and scoring.
   - Evaluate $P^*$ over `[oos_start, oos_end]`, scoring only bars inside that interval.

6. **Stitch OOS Results & Compute WFE:**
   - Concatenate the non-overlapping OOS returns into one continuous out-of-sample track record.
     Include every slice, including the losing ones.
   - Compute both Sharpe figures on the same annualization basis before taking the ratio.
   - Calculate Walk-Forward Efficiency $\text{WFE} = \frac{\text{Sharpe}_{\text{OOS}}}{\text{Sharpe}_{\text{IS}}}$.
     Require $\text{WFE} \ge 0.50$ — the conventional bar, not a statistical guarantee.
   - Treat a result with a populated `undefined_reason` as a failed slice, not as a pass or a
     zero. A non-positive in-sample Sharpe means there was no in-sample edge to generalize.

## Failure Modes Observed in Production

- **Temporal Window Overlap:** In-Sample training bars overlapping with Out-of-Sample evaluation
  bars, producing inflated backtest results.
- **Adjacency Mistaken For Isolation:** `oos_start = is_end + 1 bar` with no embargo. A 20-day
  moving average makes the first 20 OOS bars partly a function of IS data, and a 5-day forward
  return label makes the last 5 IS labels a function of OOS outcomes. Chronological ordering
  alone does not close either channel.
- **Overlapping OOS Slices Stitched Together:** `step_days < out_of_sample_days` followed by
  concatenation, which double-counts the overlapping days in the out-of-sample track record.
- **Clamped WFE Denominator:** Flooring the in-sample Sharpe at an epsilon to avoid dividing by
  zero. A parameter set with $\text{Sharpe}_{\text{IS}} = -1.0$ and
  $\text{Sharpe}_{\text{OOS}} = 0.5$ then scores an enormous positive WFE and is reported robust,
  when it is a losing in-sample fit that happened to profit out-of-sample.
- **Mixed Annualization Bases:** An annualized OOS Sharpe divided by a per-period IS Sharpe (or
  vice versa), which rescales WFE by $\sqrt{\text{periods per year}}$ and turns a failing walk-
  forward into a passing one.
- **Warming Contamination:** Counting indicator warm-up bars in out-of-sample performance metrics.
- **Non-Advancing Step:** `step_days = 0` from a parameter sweep or a config typo, which cannot
  advance the window cursor and generates slices without terminating.

## Production Implementation Reference

- Reference code: `scripts/walk_forward_manager.py` (`WalkForwardWindowManager`, `WindowSlice`,
  `WFEEvaluation`, `WindowMode`, `WalkForwardError`).
- Automated unit tests: `scripts/test_walk_forward_manager.py`
  (`python -m unittest discover -s scripts`).
