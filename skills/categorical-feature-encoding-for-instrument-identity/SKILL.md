---
name: categorical-feature-encoding-for-instrument-identity
description: Quantitative feature engineering pipeline for safely encoding high-cardinality
  instrument identities (tickers) using smoothed target encoding without lookahead
  bias.
domain: Machine Learning
subdomain: Feature Engineering
tags:
- categorical-encoding
- target-encoding
- instrument-identity
- feature-engineering
- machine-learning
brokers_frameworks:
- Scikit-Learn
- Pandas
version: 1.0.0
author: algo-trading-skills-contributors
license: Apache-2.0
---

## When to Use

Use this skill when building cross-sectional machine learning models where the "instrument identity" (e.g., AAPL, TSLA, BTC) is a feature. High cardinality makes One-Hot Encoding (OHE) explode memory and cause sparsity. Label encoding implies a false mathematical hierarchy. Instead, we use Smoothed Target Encoding to replace the ticker with its historical mean target value, ensuring we do not leak future information.

## Prerequisites

- A panel dataset containing `symbol`, `timestamp`, `features`, and a `target` variable.
- A strong understanding of time-series cross-validation to prevent target leakage.

## Workflow

1. **Initialization**: Instantiate `InstrumentTargetEncoder` with a smoothing factor (weight).
2. **Time-Aware Fitting**: Iterate through the dataset chronologically. For day $T$, calculate the encoding statistics using only data from day $T-1$ and earlier.
3. **Smoothing**: Combine the instrument-specific mean with the global mean. If a symbol is newly listed (e.g., IPO), it defaults heavily to the global mean, avoiding NaN values or extreme outliers.
4. **Transformation**: Replace the categorical `symbol` column with `symbol_encoded`.

> Full procedure: see `references/workflows.md`.
> Standards reference: see `references/standards.md`.
> Printable pre-flight checklist: see `assets/checklist.md`.

## Common Pitfalls

- **Global Target Encoding**: Calculating the mean target over the *entire* dataset and applying it to all rows. This causes catastrophic lookahead bias (target leakage) because the day 1 encoding contains information from day 100.
- **Ignoring the Cold-Start Problem**: IPOs or newly added universe symbols have 0 historical rows. Without smoothing to a global or sector mean, the model will fail on these rows.
- **One-Hot Encoding 3000 Symbols**: Blowing up memory by adding 3000 sparse columns to a dataset, causing tree-based models to overfit deeply.

## Verification

- Feed a dataset with 3 symbols and a target variable. Encode the dataset point-in-time and verify that the first day's encoding relies entirely on the global prior, and subsequent days smoothly adapt to the instrument's historical mean.
- Run `python scripts/test_categorical_feature_encoding_for_instrument_identity.py`.

## Related Skills

- `feature-engineering-without-leakage`
- `cross-sectional-vs-time-series-model-design`
