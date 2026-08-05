---
name: sample-weighting-for-overlapping-labels
description: >-
  Production-grade sample weighting engine for overlapping financial labels (Triple Barrier Method) based on Marcos López de Prado's concurrent label uniqueness, return-attribution, and time-decay weighting.
domain: Machine Learning & Quantitative Research
subdomain: Sample Uniqueness & Overlapping Labels
tags: ["sample-weighting", "overlapping-labels", "lopez-de-prado", "sample-uniqueness", "triple-barrier-method", "mlops"]
brokers_frameworks: ["Advances in Financial Machine Learning (López de Prado)", "Python Dataclasses"]
version: "1.0.0"
author: algo-trading-skills-contributors
license: Apache-2.0
---

## When to Use

Use this skill when training supervised machine learning models on financial time series with multi-period event labels (e.g., Triple Barrier Method holding periods). Standard ML models assume training samples are Independent and Identically Distributed (IID). When target labels overlap in time (e.g. 5-day horizon labels generated on consecutive daily bars), information is shared across adjacent samples, causing severe data leakage, artificial in-sample accuracy, and severe out-of-sample overfitting. This engine computes concurrent label uniqueness ($u_{i,t} = 1/c_t$) and reweights samples accordingly.

## Prerequisites

- Label event spans (`LabelSpan`: `sample_id`, `start_time_idx`, `end_time_idx`, `realized_return`).
- Weighting method (`UNIQUENESS_ONLY`, `RETURN_ATTRIBUTED`, `TIME_DECAY`).

## Workflow

1. **Concurrency Matrix Calculation**:
   - Count active concurrent labels $c_t$ at every time index $t$.
2. **Average Uniqueness Computation**:
   - Compute average uniqueness $u_i = \text{mean}(1 / c_t)$ over the lifetime of label span $i$.
3. **Sample Weight Calculation & Normalization**:
   - Compute raw weights (uniqueness, return-attributed $u_i \times |r_i|$, or time-decayed).
   - Normalize weights so $\sum w_i = N$.
4. **Report Output**: Output structured `SampleWeightingReport`.

> Full procedure: see `references/workflows.md`.
> Standards reference: see `references/standards.md`.
> Printable pre-flight checklist: see `assets/checklist.md`.

## Common Pitfalls

- **Assuming IID Financial Samples**: Training models on raw overlapping labels without sample weighting, leading to catastrophic backtest overfitting.
- **Ignoring Return Attribution**: Treating a 0.01% return label with the same weight as a 5.0% return label during high-volatility events.
- **Failing to Purge/Embargo Cross-Validation**: Combining sample weighting with standard k-fold CV instead of Purged Group K-Fold.

## Verification

- Instantiate `SampleWeightingForOverlappingLabelsEngine`. Feed non-overlapping spans $\implies$ verify uniqueness = 1.0. Feed 100% overlapping spans $\implies$ verify uniqueness = 0.5. Feed return-attributed weighting $\implies$ verify sample with higher return gets higher weight.
- Run `python scripts/test_overlapping_sample_weighter.py`.

## Related Skills

- `reproducible-ml-training-pipelines`
- `factor-research-multiple-testing-correction`
---
