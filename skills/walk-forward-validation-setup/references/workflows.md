# Deep Workflow Reference — walk-forward-validation-setup

This file holds the full technical procedure referenced by `SKILL.md`. Load this when
actually implementing the skill, not just when deciding whether it applies.

## Full Procedure

1. **Chronological Time-Series Window Splitting:**
   - Sort the dataset by timestamp first. `evaluate_walk_forward(timestamp_col=...)` verifies
     non-decreasing order and raises on a violation; row-index folds are meaningless otherwise.
   - Partition chronologically into training and testing windows (`generate_splits()`). Test
     windows are contiguous and non-overlapping in both modes, so per-fold results can be
     concatenated into one out-of-sample series without double-counting rows.
   - Strictly prohibit random-shuffling or `k-fold` cross-validation on time-series data.

2. **Expanding vs Rolling Window Selection:**
   - `EXPANDING` — training window anchored at row 0, growing by `test_size` each fold. Use when
     historical data remains relevant across regimes.
   - `ROLLING` — fixed-length training window sliding forward by `test_size` each fold. Use when
     regime shifts render old data noisy or obsolete.
   - The choice changes what the validation measures, not just its cost. Record which was used
     and why alongside the results.

3. **Purge & Embargo Gap Enforcement:**
   - Size the gap between `train_end` and `test_start` to $\max(L, H)$ where $L$ is the longest
     backward feature lookback and $H$ the longest forward label horizon, both in rows.
   - $H$ is the channel usually missed. A 20-bar forward-return target attached to the last 20
     training rows is realised inside the test window: the training set contains the answer.
     Purging removes those rows. See `references/standards.md` for the López de Prado citation.
   - Pass `max_feature_lookback` and `label_horizon` to `generate_splits()` so the gap is
     checked. Omitting both logs a warning; supplying either makes an undersized gap an error.

4. **Out-of-Sample Scoring:**
   - `fit_predict_fn(train_df, test_df)` is called once per fold. `test_df` has `target_col`
     removed (`hide_test_labels=True`) so the callback cannot read the labels it is scored on.
   - Accuracy is computed from the predictions; a length mismatch raises rather than
     broadcasting into a plausible-looking number.
   - Supply `returns_fn(test_df, predictions)` returning the fold's realised per-period strategy
     returns to get Sharpe, max drawdown and win rate. Without it those fields are `None` — the
     harness does not assume how a prediction maps to a position, and does not invent a figure.

5. **Multi-Fold Performance Aggregation:**
   - `aggregate_folds()` reports fold count, mean/sample-sd/min/max accuracy, mean and minimum
     Sharpe, and the worst drawdown across folds.
   - Report the dispersion and the worst fold, not the mean alone. A high mean with high
     dispersion is a regime-dependent strategy, and the mean hides it.
   - Fewer than three folds cannot show regime dependence; `aggregate_folds()` warns.

## Failure Modes Observed in Production

- **Random K-Fold Cross-Validation:** Shuffling time-series data, training on future price data and creating severe lookahead bias.
- **Gap Sized to the Feature Lookback Only:** Embargoing 5 rows for a 5-bar moving average while the target is a 20-bar forward return, leaving the label horizon leaking backwards into training.
- **Placeholder Risk Metrics:** A validation report whose Sharpe and drawdown are identical on every fold is printing constants, not measuring a strategy. Any metric that does not vary with the fold's returns is not a metric.
- **Accuracy Reported as Win Rate:** Directional accuracy on many small moves and errors on a few large ones is a good-looking accuracy and a losing strategy.
- **Test Labels Visible to the Model:** Passing the full test frame, target column included, into the predict callback. The result looks excellent rather than broken, which is why it survives review.
- **Reporting Best Single Fold:** Masking strategy regime-dependence by reporting only the highest-performing fold rather than full distribution metrics.
- **Post-Test Parameter Tuning:** Re-tuning hyperparameters after seeing out-of-sample test results without validating on unseen future folds.
- **Silent Mode Fallback:** A mis-typed window mode string that falls through to a different methodology than the one documented in the research notes.

## Cross-Fold Independence

Under both modes, the rows immediately following fold *k*'s test window are absorbed into fold
*k+1*'s training window. That does not corrupt fold *k*'s already-computed result, but it does
correlate consecutive folds. Per-fold results are therefore not independent draws; do not feed
them into a significance test that assumes independence.

## Production Implementation Reference

- Reference code: `scripts/walk_forward.py` (`WalkForwardSplitter`, `SplitMode`,
  `WalkForwardSplit`, `FoldMetrics`, `WalkForwardAggregate`, `WalkForwardError`).
- Automated unit tests: `scripts/test_walk_forward.py`.
