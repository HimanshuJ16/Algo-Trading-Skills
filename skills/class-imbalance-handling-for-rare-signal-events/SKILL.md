---
name: class-imbalance-handling-for-rare-signal-events
description: >-
  Use when predicting rare events such as halts or flash crashes where one class is
  under a few percent; cost-sensitive weighting and undersampling with the probability
  recalibration that undersampling makes necessary.
license: Apache-2.0
metadata:
  domain: algorithmic-trading
  subdomain: financial-ml
  tags: machine-learning, class-imbalance, rare-events, undersampling, class-weights
  brokers_frameworks: "Scikit-Learn; Pandas; NumPy"
  version: "1.1.0"
  author: algo-trading-skills-contributors
---

## When to Use

Use this skill when building quantitative models to predict highly asymmetric, rare events (e.g., flash crashes, limit-up/limit-down halts, or rare alpha signals). Financial datasets for rare events are often 99% noise and 1% signal. Standard models trained on this data will optimize for "Accuracy" by predicting 0 (noise) every time, completely ignoring the signal. This utility enforces class balancing to force the model to learn the minority class.

## When NOT to Use

- **The classes are only mildly imbalanced** (say, better than 1:4). Reweighting and undersampling both distort the class prior; below a real imbalance problem they cost calibration and buy nothing.
- **The model's probability output feeds expected-value sizing and you will not recalibrate.** Both weighting and undersampling shift predicted probabilities away from the true event rate. If you cannot apply `correct_undersampling_bias` (or an equivalent calibration step), leave the prior alone and move the decision threshold instead.
- **The minority class is small in absolute terms, not just in proportion.** With a few dozen positive examples, rebalancing amplifies noise; the constraint is sample count, not class ratio.
- **You are balancing a validation or test split.** Out-of-sample data must retain the market's real event rate — see Common Pitfalls.

## Prerequisites

- A binary or multi-class integer target array (`y`) representing the rare event. `compute_class_weights` handles multi-class; `random_undersample` and `compute_scale_pos_weight` are binary-only.
- A feature matrix (`X`) whose rows align with `y` along the first axis.
- NumPy >= 1.21 (repository `requirements.txt`); no scikit-learn or imbalanced-learn dependency is required by the helper itself.

## Workflow

1. **Evaluation Setup**: Before attempting to balance the data, set validation metrics to Precision-Recall AUC (PR-AUC) or F1-Score. ROC-AUC and Accuracy are misleading under heavy imbalance because the large true-negative count keeps the false-positive rate small even when precision is poor.
2. **Split First, Balance Second**: Perform the time-aware train/validation split before touching the class distribution. Balancing is a training-set-only transformation.
3. **Cost-Sensitive Learning (Recommended)**: This approach discards no data. Match the helper to the estimator's parameter shape:
   - scikit-learn estimators taking a `class_weight` mapping (e.g. `RandomForestClassifier`, `LogisticRegression`): pass `ImbalanceHandler.compute_class_weights(y_train)`.
   - Gradient-boosting libraries taking the scalar `scale_pos_weight` (XGBoost, LightGBM): pass `ImbalanceHandler.compute_scale_pos_weight(y_train)`. This is `negatives / positives`, a different quantity from the weight dict — the two are not interchangeable.
4. **Undersampling (Alternative)**: If the dataset is too large to train on, use `ImbalanceHandler.random_undersample(X_train, y_train, majority_ratio=...)` to reduce the majority class to a chosen multiple of the minority count (`1.0` = parity). Record `beta = kept_majority / original_majority` from the log line — step 5 needs it.
5. **Recalibrate Probabilities Before Trading On Them**: A model trained on undersampled data reports probabilities inflated towards the minority class. Pass `predict_proba` output through `ImbalanceHandler.correct_undersampling_bias(p, beta)` before it drives position sizing, expected value, or any absolute probability threshold. Ranking metrics (PR-AUC, ROC-AUC) are unaffected by the correction; monetary decisions are not.
6. **Score on the Untouched Split**: Predict on the unmodified validation set, which still carries the market's real event rate, and evaluate with Precision, Recall, F1, and the confusion matrix.

> Full procedure: see `references/workflows.md`.
> Standards reference: see `references/standards.md`.
> Printable pre-flight checklist: see `assets/checklist.md`.

## Common Pitfalls

- **Data Leakage via Resampling Validation**: Applying SMOTE or Undersampling to the *entire* dataset before doing a train-test split. This severely biases validation results because the validation set is no longer representative of the true market distribution.
- **Using Accuracy as a Metric**: A model predicting "No Crash" every day achieves 99.9% accuracy but is completely useless for trading.
- **Overusing Oversampling (SMOTE) in Finance**: Financial data is incredibly noisy. Generating synthetic financial samples via interpolation (SMOTE) often creates unrealistic market states that confuse the model.
- **Confusing the Weight Dict With `scale_pos_weight`**: `compute_class_weights` returns `{label: weight}` for scikit-learn's `class_weight`; XGBoost's `scale_pos_weight` is a single scalar (`negatives / positives`). Passing the dict where a scalar is expected fails loudly; passing the minority weight `n / (2 * minority_count)` as a `scale_pos_weight` fails silently with a different, incorrect amount of upweighting.
- **Trading On Uncalibrated Post-Balancing Probabilities**: After 1:1 undersampling of a 1% event, a model that has learned nothing beyond the base rate outputs ~0.5. Treating that as "50% chance of a crash" oversizes positions by two orders of magnitude. Correct for the retention rate before any probability is spent as money.
- **Reseeding the Global RNG**: A resampling helper that calls `np.random.seed()` silently reseeds the caller's process-wide generator, making later splits, model initialisation, and Monte Carlo runs deterministic functions of the resampler's seed. `random_undersample` uses a local `np.random.default_rng` for this reason.
- **Balancing Across the Purge/Embargo Boundary**: Undersampling picks majority rows at random, so it does not remove overlapping-label leakage. Purge and embargo first, then balance what survives.

## Verification

- Generate an imbalanced dataset (99% class 0, 1% class 1). `compute_class_weights` must return `{0: n/(2*990...), 1: n/(2*10...)}` with the class-1 weight ~99x the class-0 weight, and the per-sample weights must sum back to `n_samples`. `compute_scale_pos_weight` must return 99.0 for the same array.
- Run `random_undersample` and verify a 50/50 distribution, that returned rows stay in the original chronological order, and that `np.random.get_state()` is unchanged afterwards.
- Feed an already-balanced array (equal class counts) to `random_undersample` and verify both classes survive intact.
- `correct_undersampling_bias(0.5, beta)` with `beta = minority/majority` must return the original event rate.
- Run `python -m unittest discover -s skills/class-imbalance-handling-for-rare-signal-events/scripts` (33 tests), or `python tools/run_all_tests.py` for the full repository suite.

## Related Skills

- `walk-forward-optimization-window-management`
- `cross-sectional-vs-time-series-model-design`
- `sample-weighting-for-overlapping-labels`
- `label-noise-estimation-in-financial-targets`
