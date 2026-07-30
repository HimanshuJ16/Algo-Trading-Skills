---
name: concept-drift-vs-staleness-differentiation
description: Quantitative ML diagnostic module for differentiating between Concept
  Drift (P(Y|X) structural shift / alpha decay), Covariate Shift (P(X) feature distribution
  shift), and Data Staleness (timestamp lag).
domain: Machine Learning
subdomain: Model Monitoring & Diagnostics
tags:
- concept-drift
- covariate-shift
- data-staleness
- psi
- wasserstein
- alpha-decay
- monitoring
brokers_frameworks:
- NumPy
- Pandas
- Scikit-Learn
version: 1.0.0
author: algo-trading-skills-contributors
license: Apache-2.0
---

## When to Use

Use this skill when monitoring live machine learning strategies whose predictive performance ($R^2$, directional accuracy, Sharpe ratio) begins to degrade. Simply triggering a full model retrain upon performance drop is inefficient and can be harmful if the degradation is caused by **stale data feeds** (pipeline timestamp lag) or simple **covariate shift** ($P(X)$ shift). This module isolates whether the root cause is **Data Staleness**, **Covariate Shift**, or true **Concept Drift** (structural alpha decay where $P(Y|X)$ changes).

## Prerequisites

- Reference feature matrix $X_{ref}$ (training set) and current production feature matrix $X_{curr}$.
- Reference target residuals $e_{ref} = \hat{Y}_{ref} - Y_{ref}$ and current residuals $e_{curr}$.
- Current feature timestamp $T_{feat}$ and system evaluation timestamp $T_{sys}$.

## Workflow

1. **Feature Shift Scoring (PSI / Wasserstein)**:
   - Compute Population Stability Index (PSI) or 1D Wasserstein distance per feature between $X_{ref}$ and $X_{curr}$.
   - Aggregate into an overall `Feature Shift Score`.
2. **Residual Error Ratio Scoring**:
   - Compute Mean Squared Error (MSE) ratio: $\text{Error Ratio} = \frac{\text{MSE}(e_{curr})}{\text{MSE}(e_{ref})}$.
3. **Data Staleness Audit**:
   - Compute age delta $\Delta T = T_{sys} - T_{feat}$.
   - Flag `Stale` if $\Delta T > \text{Staleness\_Threshold\_Sec}$.
4. **Diagnostic Classification**:
   - $\Delta T > \text{Threshold} \implies$ `DATA_STALENESS` (Fix: Refresh data ingestion pipeline).
   - High Feature Shift + Low Error Ratio $\implies$ `COVARIATE_SHIFT` (Fix: Retrain model on updated $P(X)$).
   - Low/Medium Feature Shift + High Error Ratio $\implies$ `CONCEPT_DRIFT` (Fix: Strategy alpha decay / Structural shift; adjust lookback window or refactor model).
   - Low Feature Shift + Low Error Ratio $\implies$ `STABLE`.

> Full procedure: see `references/workflows.md`.
> Standards reference: see `references/standards.md`.
> Printable pre-flight checklist: see `assets/checklist.md`.

## Common Pitfalls

- **Retraining on Stale Data**: Triggering an expensive automated retraining pipeline when the real issue is a frozen feature pipeline returning yesterday's prices.
- **Confusing Covariate Shift with Concept Drift**: Assuming a model is broken because input features moved into an unobserved market regime (e.g. VIX spike), even though the underlying relationship $P(Y|X)$ remains completely intact.
- **Ignoring Prediction Residuals**: Monitoring input features only. True concept drift (alpha decay) can happen with zero shift in $P(X)$ if market participants adapt to your strategy.

## Verification

- Instantiate `DriftVsStalenessClassifier`. Test 4 synthetic scenarios:
  1. Stale timestamp ($\Delta T = 3600\text{s}$) $\implies$ returns `DATA_STALENESS`.
  2. High feature shift (PSI = 0.45), low error ratio (1.05) $\implies$ returns `COVARIATE_SHIFT`.
  3. Low feature shift (PSI = 0.02), high error ratio (2.80) $\implies$ returns `CONCEPT_DRIFT`.
  4. Low shift (PSI = 0.03), low error ratio (0.98) $\implies$ returns `STABLE`.
- Run `python scripts/test_drift_vs_staleness_classifier.py`.

## Related Skills

- `walk-forward-optimization-for-model-selection`
- `class-imbalance-handling-for-rare-signal-events`
