---
name: class-imbalance-handling-for-rare-signal-events
description: Machine learning utility pipeline for mitigating class imbalance when
  predicting rare financial events, supporting cost-sensitive weighting and random
  undersampling without lookahead bias.
domain: Machine Learning
subdomain: Model Training
tags:
- machine-learning
- class-imbalance
- rare-events
- undersampling
- class-weights
brokers_frameworks:
- Scikit-Learn
- Pandas
- NumPy
version: 1.0.0
author: algo-trading-skills-contributors
license: Apache-2.0
---

## When to Use

Use this skill when building quantitative models to predict highly asymmetric, rare events (e.g., flash crashes, limit-up/limit-down halts, or rare alpha signals). Financial datasets for rare events are often 99% noise and 1% signal. Standard models trained on this data will optimize for "Accuracy" by predicting 0 (noise) every time, completely ignoring the signal. This utility enforces class balancing to force the model to learn the minority class.

## Prerequisites

- A binary or multi-class target array (`y`) representing the rare event.
- A feature matrix (`X`) containing predictive indicators.

## Workflow

1. **Evaluation Setup**: Before attempting to balance the data, ensure your validation metrics are set to Precision-Recall AUC (PR-AUC) or F1-Score. Standard ROC-AUC and Accuracy are highly misleading for rare events.
2. **Cost-Sensitive Learning (Recommended)**: Use the `ImbalanceHandler.compute_class_weights(y)` method. Inject these weights directly into your tree-based models (e.g., `XGBoost(scale_pos_weight=...)` or `RandomForest(class_weight=...)`). This approach doesn't discard data.
3. **Undersampling (Alternative)**: If the dataset is too massive for memory, use `ImbalanceHandler.random_undersample(X, y)` to drastically reduce the majority class down to parity with the minority class.

> Full procedure: see `references/workflows.md`.
> Standards reference: see `references/standards.md`.
> Printable pre-flight checklist: see `assets/checklist.md`.

## Common Pitfalls

- **Data Leakage via Resampling Validation**: Applying SMOTE or Undersampling to the *entire* dataset before doing a train-test split. This severely biases validation results because the validation set is no longer representative of the true market distribution.
- **Using Accuracy as a Metric**: A model predicting "No Crash" every day achieves 99.9% accuracy but is completely useless for trading.
- **Overusing Oversampling (SMOTE) in Finance**: Financial data is incredibly noisy. Generating synthetic financial samples via interpolation (SMOTE) often creates unrealistic market states that confuse the model.

## Verification

- Generate an imbalanced dataset (99% class 0, 1% class 1). Compute class weights and verify that class 1 receives a weight ~99x higher than class 0. Run undersampling and verify the resulting arrays have exactly a 50/50 class distribution.
- Run `python scripts/test_imbalance_handler.py`.

## Related Skills

- `walk-forward-optimization-for-model-selection`
- `cross-sectional-vs-time-series-model-design`
