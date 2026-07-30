---
name: feature-importance-drift-monitoring
description: >-
  Quantitative MLOps engine for tracking feature importance drift, computing Spearman rank correlations between training baselines and live inference windows, and triggering model retraining alerts.
domain: Financial ML & MLOps
subdomain: Model Governance & Drift Monitoring
tags: ["feature-drift", "spearman-rank", "mlops", "model-retraining", "concept-drift", "shap-drift", "feature-importance"]
brokers_frameworks: ["Spearman Rank Correlation", "SciPy Stats", "Python Dataclasses"]
version: "1.0.0"
author: algo-trading-skills-contributors
license: Apache-2.0
---

## When to Use

Use this skill in live financial ML deployment pipelines, automated model retraining systems, and model governance dashboards. In dynamic financial markets, feature importance rankings shift as market regimes evolve (e.g. macro rate sentiment taking over micro price momentum). When the Spearman rank correlation ($\rho_{\text{rank}}$) between training baseline importance and live production importance drops below 0.70, the model's learned decision boundary is no longer valid, requiring an automated retraining trigger.

## Prerequisites

- Baseline training feature importance map $\{f_i: I_{\text{base}, i}\}$.
- Production window feature importance map $\{f_i: I_{\text{live}, i}\}$.
- Alert thresholds ($\rho_{\text{min}} = 0.70$, max single feature drop threshold = 80%).

## Workflow

1. **Feature Rank Assignment**:
   - Assign integer ranks $R_{\text{base}, i}$ and $R_{\text{live}, i}$ to features ordered by importance.
2. **Spearman Rank Correlation Calculation**:
   - Compute rank differences $d_i = R_{\text{live}, i} - R_{\text{base}, i}$.
   - $\rho_{\text{rank}} = 1 - \frac{6 \sum d_i^2}{M(M^2 - 1)}$.
3. **Severe Feature Degradation Audit**:
   - Identify top baseline features experiencing $> 80\%$ drop in live importance.
4. **Drift Alert & Retrain Recommendation**:
   - If $\rho_{\text{rank}} < 0.70$ OR top feature degraded $\implies$ Flag `FEATURE_DRIFT_ALERT_TRIGGERED` (Trigger Retrain).
   - Else $\implies$ Flag `FEATURE_STABILITY_NORMAL`.
5. **Audit Report Generation**: Output structured `FeatureDriftAuditReport`.

> Full procedure: see `references/workflows.md`.
> Standards reference: see `references/standards.md`.
> Printable pre-flight checklist: see `assets/checklist.md`.

## Common Pitfalls

- **Monitoring Distribution Drift Only (PSI/KS) Without Importance Drift**: Detecting raw input distribution shifts while failing to notice that the model's top predictive feature has lost all signal power.
- **Using Pearson Correlation on Non-Linear Ranks**: Using linear Pearson correlation instead of non-parametric Spearman rank correlation to evaluate feature ranking shifts.
- **Ignoring Retraining Triggers**: Logging drift alerts to dashboards without automated pipelines to trigger model retraining.

## Verification

- Instantiate `FeatureImportanceDriftMonitorEngine`. Input baseline importance map (RSI: 0.40, Vol: 0.30, Trend: 0.20, Sentiment: 0.10). Test Scenario 1: Stable live importance ($\rho_{\text{rank}} = 0.95$) $\implies$ verify engine outputs `FEATURE_STABILITY_NORMAL`. Test Scenario 2: Regime shift live importance where Sentiment rises to 1 and RSI drops to 4 ($\rho_{\text{rank}} = 0.20$) $\implies$ verify engine triggers `FEATURE_DRIFT_ALERT_TRIGGERED`.
- Run `python scripts/test_feature_drift_monitor.py`.

## Related Skills

- `explainability-for-live-trading-signals`
- `feature-selection-stability-across-folds`
---
