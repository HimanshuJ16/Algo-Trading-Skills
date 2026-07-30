---
name: feature-selection-stability-across-folds
description: >-
  Quantitative ML engine for measuring feature selection stability across cross-validation folds using Nogueira's Index, computing inclusion frequencies, and filtering unstable overfitted features.
domain: Financial ML & Validation
subdomain: Cross-Validation & Feature Selection Robustness
tags: ["feature-selection", "nogueira-index", "kuncheva-index", "cross-validation", "fold-stability", "p-hacking", "overfitting-control"]
brokers_frameworks: ["Nogueira 2017 Measure", "Kuncheva Index", "Python Dataclasses"]
version: "1.0.0"
author: algo-trading-skills-contributors
license: Apache-2.0
---

## When to Use

Use this skill in quantitative feature selection pipelines, walk-forward cross-validation setups, and model robustness audits. When feature selection algorithms (Lasso, Boruta, RFE) select different subsets of features across cross-validation folds, the resulting model is fragile and overfitted. This module calculates **Nogueira's Stability Index ($\Phi$)** and feature inclusion frequencies ($p_i$), retaining only consensus features selected in $\ge 80\%$ of folds.

## Prerequisites

- Total number of candidate features $M$.
- List of selected feature sets across $K$ cross-validation folds: $[S_1, S_2, \dots, S_K]$.
- Minimum inclusion threshold $p_{\text{min}} = 0.80$ and minimum Nogueira stability threshold $\Phi_{\text{min}} = 0.70$.

## Workflow

1. **Feature Inclusion Frequency Calculation**:
   - For each candidate feature $i \in \{1 \dots M\}$:
     - Compute selection probability: $p_i = \frac{1}{K} \sum_{k=1}^K \mathbf{1}_{i \in S_k}$.
2. **Nogueira Stability Index ($\Phi$) Evaluation**:
   - Compute average selected subset size $\bar{k} = \frac{1}{K} \sum_{k=1}^K |S_k|$.
   - Calculate sample variance $s_i^2 = \frac{K}{K-1} p_i (1 - p_i)$.
   - Calculate Nogueira Index: $\Phi = 1 - \frac{\frac{1}{M} \sum s_i^2}{\bar{k} \left(1 - \frac{\bar{k}}{M}\right)}$.
3. **Consensus Feature Set Extraction**:
   - Retain Consensus Features: $S_{\text{consensus}} = \{f_i \mid p_i \ge 0.80\}$.
   - Prune Unstable Features: $S_{\text{pruned}} = \{f_i \mid p_i < 0.80\}$.
4. **Stability Audit & Status Determination**:
   - If $\Phi \ge 0.70 \implies$ Flag `STABLE_FEATURE_SET`.
   - Else $\implies$ Flag `UNSTABLE_OVERFITTED_FEATURE_SET`.
5. **Audit Report Generation**: Output structured `FeatureStabilityAuditReport`.

> Full procedure: see `references/workflows.md`.
> Standards reference: see `references/standards.md`.
> Printable pre-flight checklist: see `assets/checklist.md`.

## Common Pitfalls

- **Ignoring Cross-Fold Feature Variance**: Training models on union feature sets containing unstable features selected in only 1 of 10 folds.
- **Using Jaccard Index Without Chance Correction**: Evaluating raw subset overlap without adjusting for random chance agreements when $\bar{k} \approx M / 2$.
- **Failing to Re-Train on Consensus Features**: Retaining all features despite low Nogueira stability scores.

## Verification

- Instantiate `FeatureStabilityAnalyzerEngine`. Input 10 candidate features across 5 walk-forward CV folds. Scenario 1: Identical feature selection across all 5 folds $\implies$ verify engine computes Nogueira Index $\Phi = 1.0$, flags `STABLE_FEATURE_SET`, and retains 100% consensus. Scenario 2: Random erratic feature selections across folds $\implies$ verify engine computes $\Phi < 0.30$, prunes unstable features, and flags `UNSTABLE_OVERFITTED_FEATURE_SET`.
- Run `python scripts/test_feature_stability_analyzer.py`.

## Related Skills

- `walk-forward-validation-setup`
- `feature-importance-drift-monitoring`
---
