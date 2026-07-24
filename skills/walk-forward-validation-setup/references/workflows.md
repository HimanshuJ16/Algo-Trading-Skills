# Deep Workflow Reference — walk-forward-validation-setup

This file holds the full technical procedure referenced by `SKILL.md`. Load this when
actually implementing the skill, not just when deciding whether it applies.

## Full Procedure

1. **Chronological Time-Series Window Splitting:**
   - Partition dataset chronologically into non-overlapping training and testing windows (`generate_splits()`).
   - Strictly prohibit random-shuffling or `k-fold` cross-validation on time-series data.

2. **Expanding vs Rolling Window Selection:**
   - Choose `EXPANDING` mode when historical data remains relevant across regimes.
   - Choose `ROLLING` mode when market regime shifts render old data noisy or obsolete.

3. **Purge & Embargo Gap Enforcement:**
   - Insert an explicit purge/embargo gap ($E \ge \text{max\_feature\_lookback}$) between `train_end` and `test_start`.
   - Prevent rolling feature lookback overlap from leaking training signal into out-of-sample evaluation.

4. **Multi-Fold Performance Aggregation:**
   - Aggregate out-of-sample performance across all walk-forward folds via `evaluate_walk_forward()`.
   - Report per-fold breakdown (Accuracy, Sharpe, Drawdown, Win Rate) to evaluate regime consistency.

## Failure Modes Observed in Production

- **Random K-Fold Cross-Validation:** Shuffling time-series data, training on future price data and creating severe lookahead bias.
- **Missing Purge / Embargo Gap:** Omitting the embargo gap between train and test windows, leaking rolling feature calculations across boundaries.
- **Reporting Best Single Fold:** Masking strategy regime-dependence by reporting only the highest-performing fold rather than full distribution metrics.
- **Post-Test Parameter Tuning:** Re-tuning hyperparameters after seeing out-of-sample test results without validating on unseen future folds.

## Production Implementation Reference

- Reference code: `scripts/walk_forward.py` (`WalkForwardSplitter`, `SplitMode`, `FoldMetrics`).
- Automated unit tests: `scripts/test_walk_forward.py`.
