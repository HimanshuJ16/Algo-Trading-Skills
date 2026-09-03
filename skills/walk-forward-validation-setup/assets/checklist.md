# Pre-Flight / Sign-off Checklist — walk-forward-validation-setup

Use this before considering the skill's implementation complete.

- [ ] **Chronological Ordering:** Confirm split generation never uses random shuffling, and that ordering is asserted in code (`evaluate_walk_forward(timestamp_col=...)`) rather than assumed.
- [ ] **Gap Sizing Recorded:** Confirm the longest backward feature lookback $L$ and the longest forward label horizon $H$ are both written down, in rows.
- [ ] **Gap Enforcement:** Confirm `embargo_size` $\ge \max(L, H)$ and that `max_feature_lookback` / `label_horizon` were passed to `generate_splits()`, so the gap is checked rather than left at the `DEFAULT_EMBARGO` placeholder.
- [ ] **Window Mode Selection:** Confirm the choice of `EXPANDING` or `ROLLING` is justified in writing against the market-regime assumption, and that no mis-typed mode string silently selected the other one.
- [ ] **Test Labels Hidden:** Confirm `hide_test_labels` is left at `True`, or that an explicit, reviewed reason exists for disabling it.
- [ ] **Risk Metrics Are Real:** Confirm every reported Sharpe / drawdown / win rate traces to a `returns_fn` return series, and that none is constant across folds. `None` is the correct output when no returns were supplied.
- [ ] **Win Rate Is Not Accuracy:** Confirm the reported win rate is a per-period hit rate on realised returns, not classification accuracy.
- [ ] **Multi-Fold Aggregation:** Confirm `aggregate_folds()` output is reported — fold count, mean *and* dispersion of accuracy, minimum Sharpe, worst drawdown — not the final or best-performing fold alone.
- [ ] **Fold Count:** Confirm at least 3-5 folds spanning distinct time windows, and that the folds are not treated as independent draws in any significance test.
- [ ] **No Post-Test Retuning:** Confirm no hyperparameter or feature-selection decision was made after seeing a fold's out-of-sample result and then reported against that same fold.
- [ ] **Automated Testing:** Run `python -m unittest discover -s scripts` from the skill directory and confirm a 100% pass rate.

## Sign-off

- Reviewed by: ___________________________
- Date: ___________________________
- Environment tested (paper/sandbox/live): ___________________________
