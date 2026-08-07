---
name: hyperparameter-tuning-without-target-leakage
description: >-
  Quantitative ML engine for tuning hyperparameters without target leakage using Purged & Embargoed Nested Cross-Validation (De Prado 2018) and isolated feature scaler fitting.
domain: Quant Research & Alt Data
subdomain: Model Governance & Leakage-Free Cross-Validation
tags: ["hyperparameter-tuning", "nested-cv", "purged-cv", "embargoing", "target-leakage", "financial-ml", "cross-validation"]
brokers_frameworks: ["De Prado (2018) PurgedKFold", "Scikit-Learn", "Python Dataclasses"]
version: "1.0.0"
author: algo-trading-skills-contributors
license: Apache-2.0
---

## When to Use

Use this skill when optimizing model hyperparameters (e.g. LightGBM `max_depth`, `learning_rate`, `n_estimators`, neural network learning rates) for financial time series strategies. Standard K-Fold Grid Search fits feature scalers on full datasets and evaluates hyperparameter combinations across overlapping temporal folds, leaking future target information into hyperparameter selection. This module executes **Purged & Embargoed Nested Cross-Validation (López de Prado 2018)**, isolating hyperparameter tuning strictly within inner training folds.

## Prerequisites

- Time series dataset with timestamps, features $X$, target $y$, and label availability horizons.
- Hyperparameter grid definition (e.g. `{"max_depth": [3, 5, 7], "learning_rate": [0.01, 0.05]}`).
- Purge duration $\Delta t_{\text{purge}}$ and Embargo buffer percentage $E = 1.0\%$.

## Workflow

1. **Outer Time-Series Split**:
   - Split dataset chronologically into $K_{\text{outer}}$ Outer Train, Validation, and Test folds.
2. **Inner Purged & Embargoed Tuning (Inner Loop)**:
   - For each Outer Train fold, create Inner Train and Inner Validation splits.
   - **Purge**: Exclude inner training samples whose label horizons overlap in time with inner validation samples.
   - **Embargo**: Exclude $E = 1.0\%$ of samples immediately following validation folds to break serial correlation.
3. **Isolated Preprocessing & Grid Search**:
   - Fit feature scalers strictly on Inner Train folds; transform Validation and Test folds without re-fitting.
   - Evaluate performance (Sharpe Ratio / IC) for each hyperparameter combination.
4. **Leakage Haircut Calculation**:
   - Quantify leakage over-estimation haircut: $\Delta \text{Sharpe} = \text{Sharpe}_{\text{leaky\_CV}} - \text{Sharpe}_{\text{purged\_nested\_CV}}$.
5. **Audit Report Generation**: Output structured `LeakageFreeTuningReport`.

> Full procedure: see `references/workflows.md`.
> Standards reference: see `references/standards.md`.
> Printable pre-flight checklist: see `assets/checklist.md`.

## Common Pitfalls

- **Fitting Scalers / Encoders on Full Dataset**: Invoking `StandardScaler.fit(X)` on full data prior to CV splits, leaking global mean/std into validation folds.
- **Tuning Hyperparameters on Test Folds**: Selecting hyperparameters that maximize test set performance, introducing backtest overfitting.
- **Ignoring Serial Correlation via Standard Random K-Fold**: Using random K-Fold CV without purging and embargoing, allowing future target returns to leak into adjacent past folds.

## Verification

- Instantiate `LeakageFreeHyperparameterTunerEngine`. Input 1,000 time series samples with overlapping 5-day return labels. Run Purged Nested CV grid search vs Standard Random CV $\implies$ verify engine isolates inner training fold scalers, applies 5-day purging + 1% embargo buffers, selects optimal hyperparameters, and calculates realistic out-of-sample Sharpe ratio without lookahead bias.
- Run `python scripts/test_leakage_free_tuner.py`.

## Related Skills

- `feature-engineering-without-leakage`
- `walk-forward-validation-setup`
---
