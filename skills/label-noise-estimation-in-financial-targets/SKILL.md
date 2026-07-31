---
name: label-noise-estimation-in-financial-targets
description: >-
  Quantitative machine learning noise estimation engine implementing Confident Learning (Cleanlab framework), computing noise transition matrices, identifying mislabeled financial targets, and generating clean sample weights.
domain: Quant Research & Alt Data
subdomain: Financial Machine Learning & Target Labeling
tags: ["label-noise", "confident-learning", "cleanlab", "financial-ml", "target-labeling", "noise-ratio", "sample-weighting"]
brokers_frameworks: ["Cleanlab Framework", "scikit-learn / XGBoost", "Python Dataclasses"]
version: "1.0.0"
author: algo-trading-skills-contributors
license: Apache-2.0
---

## When to Use

Use this skill when training financial machine learning models (XGBoost, LightGBM, Neural Networks) on classification targets (Triple-Barrier Method, Trend Scanning, Fixed-Horizon Returns). Financial targets suffer from severe **label noise** due to microstructure noise, bid-ask spread bounces, and volatile market jumps. Training ML models on uncleaned, noisy labels leads to catastrophic overfitting and negative out-of-sample Sharpe ratios. This module estimates the label noise ratio $\eta$, identifies mislabeled training samples via Confident Learning, and outputs cleaned target vectors and sample weights.

## Prerequisites

- Array of noisy observed target labels $y \in \{0, 1\}$.
- Matrix of out-of-fold predicted class probabilities $P(y=k|X)$.

## Workflow

1. **Class-Specific Threshold Calculation**:
   - Compute class probability thresholds $t_k$:
     $$t_k = \frac{1}{|S_k|} \sum_{i \in S_k} P(y=k | x_i)$$
2. **Confident Error Identification**:
   - Flag sample $i$ with observed $y_i = 0$ as mislabeled if $P(y=1|x_i) \ge t_1$.
   - Flag sample $i$ with observed $y_i = 1$ as mislabeled if $P(y=0|x_i) \ge t_0$.
3. **Noise Ratio & Transition Matrix Estimation**:
   - Compute total noise ratio $\eta = \frac{N_{\text{mislabeled}}}{N_{\text{total}}}$.
   - If $\eta \ge 0.20$ ($20\%$) $\implies$ Flag `HIGH_LABEL_NOISE_WARNING`.
4. **Noise-Cleaned Target & Sample Weight Generation**:
   - Assign sample weight $W_i = 0.0$ for confident errors.
5. **Audit Report Generation**: Output structured `LabelNoiseReport`.

> Full procedure: see `references/workflows.md`.
> Standards reference: see `references/standards.md`.
> Printable pre-flight checklist: see `assets/checklist.md`.

## Common Pitfalls

- **Training on In-Sample Probabilities**: Computing Confident Learning thresholds on in-sample predicted probabilities instead of out-of-fold cross-validated probabilities, underestimating noise ratios.
- **Ignoring Class Imbalance in Noise Thresholds**: Using a fixed $0.5$ threshold across imbalanced financial classes instead of class-specific expected probabilities $t_k$.
- **Hard Pruning All Low-Confidence Data**: Dropping too many samples when noise ratio is low, reducing training dataset size unnecessarily.

## Verification

- Instantiate `LabelNoiseEstimatorEngine`. Ingest 1,000 synthetic financial target samples with 15% injected label noise. Verify engine accurately estimates noise ratio $\approx 15.0\%$, identifies mislabeled sample indices, and generates sample weights $W$ with $W_i = 0.0$ for mislabeled points. Audit 30% Heavy Noise dataset $\implies$ verify `HIGH_LABEL_NOISE_WARNING`.
- Run `python scripts/test_label_noise_estimator.py`.

## Related Skills

- `factor-research-multiple-testing-correction`
- `backtesting-alt-data-strategies-with-realistic-availability-lag`
---
