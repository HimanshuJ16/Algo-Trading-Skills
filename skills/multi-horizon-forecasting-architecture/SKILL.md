---
name: multi-horizon-forecasting-architecture
description: >-
  Multi-horizon ML return forecasting engine, combining predictions across 5-min, 15-min, 60-min, and 1-day horizons with IC-weighted decay and short-vs-long conflict arbitration.
domain: Machine Learning & Alpha Generation
subdomain: Multi-Horizon Forecast Combination & Signal Arbitration
tags: ["multi-horizon", "forecasting", "alpha-combination", "information-coefficient", "signal-decay", "conflict-arbitration"]
brokers_frameworks: ["Information Correlation (IC)", "Inverse-Square-Root Decay", "Python Dataclasses"]
version: "1.0.0"
author: algo-trading-skills-contributors
license: Apache-2.0
---

## When to Use

Use this skill when building quantitative Machine Learning (ML) models that forecast asset returns simultaneously across multiple forward time horizons (e.g., $\tau_1 = 5\text{ min}$, $\tau_2 = 15\text{ min}$, $\tau_3 = 60\text{ min}$, $\tau_4 = 1\text{ day}$). Short-term forecasts offer high Information Coefficients (IC) but decay rapidly and incur higher turnover/slippage. Long-term forecasts offer persistent directional trends. Combining multi-horizon predictions using IC-weighted decay or inverse-horizon weighting constructs a stable, turnover-aware alpha signal while arbitrating short-vs-long directional conflicts.

## Prerequisites

- Multi-horizon prediction payload (`horizon_steps`, `predicted_return`, `ic_score`, `confidence`).
- Horizon weighting config (`horizons`: list of int, `weighting_scheme`: `'INVERSE_HORIZON_SQRT'`, `'IC_WEIGHTED'`, `'EQUAL'`).

## Workflow

1. **Horizon Prediction Ingestion**:
   - Ingest return forecasts $\hat{y}_{\tau_k}$ across time horizons $\tau_k \in \{5, 15, 60, 240\}$.
2. **Horizon Weighting & Decay Calculation**:
   - Compute unnormalized weights $w_k$:
     - `INVERSE_HORIZON_SQRT`: $w_k = 1 / \sqrt{\tau_k}$.
     - `IC_WEIGHTED`: $w_k = \max(0, IC_k) \cdot \text{confidence}_k$.
     - `EQUAL`: $w_k = 1.0$.
   - Normalize weights: $\bar{w}_k = w_k / \sum w_j$.
3. **Alpha Signal Synthesis & Conflict Arbitration**:
   - Synthesize composite alpha: $\alpha = \sum \bar{w}_k \cdot \hat{y}_{\tau_k}$.
   - Compute directional consensus ratio $C \in [0, 1]$.
   - Audit short-vs-long directional conflicts ($\text{sign}(\hat{y}_{\tau_1}) \neq \text{sign}(\hat{y}_{\tau_K})$).
4. **Audit Report Generation**: Output structured `MultiHorizonForecastReport`.

> Full procedure: see `references/workflows.md`.
> Standards reference: see `references/standards.md`.
> Printable pre-flight checklist: see `assets/checklist.md`.

## Common Pitfalls

- **Naive Horizon Averaging**: Equal-weighting short and long horizons without adjusting for return scaling (e.g. 5-min 0.1% vs 1-day 2.0%).
- **Ignoring Short vs Long Conflicts**: Executing a 5-min Buy signal when 1-day prediction indicates a severe Sell trend, causing trades against the macro trend.
- **Overestimating Short-Term Edge**: Failing to factor higher execution turnover and slippage into 5-min horizon predictions.

## Verification

- Instantiate `MultiHorizonForecasterEngine`. Audit predictions across [5, 15, 60, 240] min horizons $\implies$ verify IC-weighted normalization, composite alpha synthesis, consensus score, and conflict detection.
- Run `python scripts/test_multi_horizon_forecaster.py`.

## Related Skills

- `model-inference-latency-budget-for-live-trading`
- `label-noise-estimation-in-financial-targets`
---
