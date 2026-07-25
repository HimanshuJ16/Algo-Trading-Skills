---
name: feature-engineering-cost-benefit-tracking
description: >-
  Use when engineering ML feature sets to evaluate each feature's marginal performance contribution (Shapley / Permutation Importance) against its compute, storage, and API licensing cost, pruning expensive low-value features.
domain: algorithmic-trading
subdomain: financial-ml
tags: ["financial-ml", "feature-cost-benefit", "shapley-value", "cost-benefit-pruning", "feature-selection", "compute-efficiency"]
brokers_frameworks: ["Feature Cost-Benefit Tracker Engine", "Python NumPy"]
version: "1.0"
author: algo-trading-skills-contributors
license: Apache-2.0
---

## When to Use

Invoke this skill when optimizing ML feature pipelines for production deployment. Production feature pipelines frequently suffer from feature bloat: dozens of complex features (e.g. high-latency order book depth metrics, expensive alternative data APIs, multi-resolution convolutions) that add negligible marginal predictive accuracy while ballooning infrastructure costs and inference latency. This skill evaluates each feature's ROI (marginal performance gain per dollar of compute/data cost) to prune inefficient features.

## Prerequisites

- Feature list with computed feature importance scores (e.g. Permutation Importance or Shapley values).
- Data acquisition and compute infrastructure cost per feature ($/month or latency-ms).

## Workflow

1. **Quantify Marginal Feature Value**: Compute feature importance scores $I_i$ (e.g. % accuracy drop when feature $i$ is shuffled).
2. **Assign Feature Cost**: Record monthly licensing and compute cost $C_i$ ($/month).
3. **Compute Feature ROI Ratio**:
   $$\text{ROI}_i = \frac{I_i}{\max(0.01, C_i)}$$
4. **Enforce Pruning Decision Rules**:
   - `KEEP`: High importance, low/moderate cost ($I_i \ge 0.02$).
   - `PRUNE`: Low importance, high cost ($I_i < 0.01$ and $C_i > \$100/\text{mo}$).

> Full procedure: see `references/workflows.md`.
> Standards reference: see `references/standards.md`.
> Printable pre-flight checklist: see `assets/checklist.md`.

## Common Pitfalls

- **Evaluating Features in Isolation**: Dropping a low-individual-importance feature that interacts synergistically with another feature.
- **Ignoring Inference Latency as a Cost**: Treating data cost as the only metric while ignoring feature computation latency impact on HFT strategies.

## Verification

- Submit 5 features with varying importance and cost, verify low-ROI expensive features are flagged for pruning.
- Run `python scripts/test_feature_cost_benefit.py` and confirm 100% pass rate.

## Related Skills

- `feature-importance-drift-monitoring`
- `feature-selection-stability-across-folds`
- `model-inference-latency-budget-for-live-trading`
---
