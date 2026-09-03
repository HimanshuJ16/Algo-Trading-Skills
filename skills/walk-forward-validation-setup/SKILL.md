---
name: walk-forward-validation-setup
description: Use when validating a trading strategy or ML signal model on historical
  time-series data, to generate chronological train/test folds separated by a purge/embargo
  gap and aggregate out-of-sample results across every fold, instead of the invalid splits
  k-fold cross-validation produces on non-stationary sequential data
domain: algorithmic-trading
subdomain: backtesting-methodology
tags:
- backtesting-methodology
- walk-forward-validation
- purge-embargo
- lookahead-prevention
- out-of-sample-testing
- cross-validation
- lopez-de-prado
brokers_frameworks:
- scikit-learn TimeSeriesSplit
- QuantConnect LEAN
- Backtrader
- Python standard library
version: "2.0.0"
author: algo-trading-skills-contributors
license: Apache-2.0
---

## When to Use

Invoke this whenever a strategy or ML model's performance needs to be validated against historical data before being trusted. Standard k-fold cross-validation (randomly shuffling data into folds) is invalid for time series because it trains on data from the future relative to some test samples, and because financial time series are non-stationary — a model validated on a random shuffle can look good purely by having memorized regime-specific patterns that don't generalize forward in time.

The reference helper (`scripts/walk_forward.py`) generates the fold indices, enforces the gap, and aggregates per-fold out-of-sample results. It is a **validation harness, not a performance model**: it computes Sharpe, drawdown and win rate only from realised strategy returns you supply, and reports `None` rather than a placeholder when you do not.

## When NOT to Use

- Do not use it as a fold generator for a cross-sectional model whose labels overlap in time and whose observations must be weighted by uniqueness. This skill applies a single boundary gap per fold; per-observation purging and uniqueness weighting are a different mechanism (see `sample-weighting-for-overlapping-labels` and `synthetic-labels-from-triple-barrier-method`).
- Do not use it to size calendar-dated in-sample/out-of-sample windows for a parameter sweep, or to compute Walk-Forward Efficiency. That is `walk-forward-optimization-window-management`; this skill works in row indices over an already-built feature matrix.
- Do not treat a passing walk-forward result as evidence a strategy is real when the parameters were chosen from a large search. Fold count says nothing about how many configurations were tried (see `walk-forward-hyperparameter-search-budget` and `factor-research-multiple-testing-correction`).
- Do not rely on it to catch a feature that was computed over the full dataset before slicing. Splitting rows correctly cannot undo leakage already baked into a column (see `feature-engineering-without-leakage` and `lookahead-bias-elimination`).

## Prerequisites

- Historical data spanning multiple distinct market regimes if possible (trending, ranging, high/low volatility periods) — a walk-forward test over a single regime understates real-world variance
- A clear definition of the strategy/model's minimum required training window
- Known values for the longest backward feature lookback `L` and the longest forward label horizon `H`, in rows. The gap between train and test cannot be sized without them.
- A defined mapping from a model prediction to a position, if Sharpe/drawdown/win rate are wanted — the harness will not guess it

## Workflow

1. Sort the dataset chronologically and split it into sequential windows in that order — never shuffle. Row-index folds are only chronological if the rows are; `evaluate_walk_forward(timestamp_col=...)` verifies non-decreasing timestamps and raises rather than silently producing a "training" window dated after its "test" window.
2. Use an expanding or rolling window scheme: train on window 1, test on window 2 (immediately following, out-of-sample); then either expand the training window to include window 2 and test on window 3 (expanding-window), or roll the training window forward by one period and test on the next (rolling-window). Choose expanding vs rolling based on whether older data remains relevant (expanding) or whether the market has fundamentally changed such that older data adds noise rather than signal (rolling).
3. Insert a **purge/embargo gap** between the end of each training window and the start of its corresponding test window, sized to **`max(L, H)`** — the longest backward feature lookback *and* the longest forward label horizon. Chronological ordering alone does not close either channel: a label with an `H`-bar forward horizon attached to the last `H` training rows is realised from out-of-sample bars (purging removes those rows), and a feature with an `L`-bar lookback evaluated on the first `L` test rows is computed partly from training bars. Sizing the gap to the feature lookback alone leaves a forward-looking target leaking backwards, which is usually the larger of the two. Pass `max_feature_lookback` and `label_horizon` to `generate_splits()` so the gap is checked against them instead of taken on trust — an unchecked gap only warns.
4. Aggregate performance across all walk-forward folds, not just the final fold — `aggregate_folds()` reports the mean *and* the dispersion, the worst fold, and the worst drawdown, because a strategy that performs well on the most recent fold but poorly on earlier folds may simply be overfit to recent conditions. Fewer than 3-5 folds cannot show regime dependence at all.
5. Keep the out-of-sample labels away from the model. The harness drops `target_col` from the frame handed to `fit_predict_fn` for exactly this reason (`hide_test_labels=True`); if a callback needs the labels for anything other than scoring, that is the bug.
6. Never re-tune hyperparameters after seeing walk-forward test results and then report the original walk-forward numbers as final — any parameter adjustment made after seeing test-period performance invalidates that fold; if retuning happens, it must be validated on a subsequent, still-unseen fold.
7. For ML signal classifiers specifically, apply the same walk-forward discipline to feature selection, not just model fitting — selecting which features to use based on their correlation with the target across the entire dataset (including test folds) is a leakage channel distinct from parameter tuning.

> Full step-by-step procedure with broker-specific detail: see `references/workflows.md`.
> Broker/framework coverage table for this skill: see `references/standards.md`.
> Printable pre-flight checklist: see `assets/checklist.md`.

## Common Pitfalls

- Using `sklearn`'s default k-fold cross-validation (or any random-shuffle CV) on time-series data without switching to a time-series-aware splitter — a common default-tool mistake when an agent generates ML validation code without specific instruction.
- Sizing the gap to the feature lookback only. The label horizon is the channel that leaks the *answer* into the training set, and on a 20-day forward-return target with 5-day features the correct gap is 20, not 5.
- Reading a Sharpe ratio, drawdown or win rate off a validation harness that did not receive any realised returns. A validation report whose risk metrics are constants across every fold is reporting a placeholder, not a result. This helper returns `None` in that case, on purpose.
- Treating classification accuracy as a win rate. Predicting direction correctly on many small moves and wrongly on a few large ones is a profitable-looking accuracy and a losing strategy.
- Reporting only the final or best-performing fold's metrics rather than the full distribution across folds, which masks regime-dependent fragility.
- Handing the test frame's label column to the model's predict function. It is the easiest leak in the whole pipeline to introduce and the hardest to see in the metrics, because the result looks excellent rather than broken.
- Treating walk-forward validation as a one-time gate rather than an ongoing process — a model validated well on data through a certain date needs re-validation as new data accumulates (see `model-staleness-detection`).

## Verification

- Confirm the train/test split code sorts by timestamp and never uses a random shuffle or `random_state`-based split for any time-series data; confirm the ordering is asserted in code, not assumed.
- Confirm an explicit purge/embargo gap exists and that its size was checked against `max(feature lookback, label horizon)` rather than left at a default.
- Confirm reported performance includes a per-fold breakdown across at least 3-5 distinct time windows plus the cross-fold dispersion and worst fold, not a single aggregate number, so regime-dependence is visible.
- Confirm every reported risk metric traces to a realised return series, and that no metric is constant across folds.
- Run `python -m unittest discover -s skills/walk-forward-validation-setup/scripts` and confirm all tests pass.

## Related Skills

- `walk-forward-optimization-window-management`
- `walk-forward-hyperparameter-search-budget`
- `lookahead-bias-elimination`
- `feature-engineering-without-leakage`
- `sample-weighting-for-overlapping-labels`
- `hyperparameter-tuning-without-target-leakage`
- `feature-selection-stability-across-folds`
- `backtest-reporting-standardized-tearsheet`
- `model-staleness-detection`
