---
name: multi-model-ensemble-weight-decay
description: >-
  Multi-model ensemble weight decay engine, applying exponential recency memory decay, softmax dynamic reweighting, and model demotion circuit breakers.
domain: Machine Learning & Alpha Generation
subdomain: Dynamic Model Ensembling & Weight Decay Management
tags: ["ensemble", "weight-decay", "softmax-weighting", "exponential-decay", "alpha-decay", "model-demotion", "machine-learning"]
brokers_frameworks: ["Exponential Recency Smoothing", "Softmax Ensemble Reweighting", "Python Dataclasses"]
version: "1.0.0"
author: algo-trading-skills-contributors
license: Apache-2.0
---

## When to Use

Use this skill when managing machine learning trading ensembles composed of multiple underlying models (e.g. XGBoost, LightGBM, LSTM, Ridge Regression). In non-stationary financial markets, static ensemble weights ($\mathbf{w} = [1/M, \dots, 1/M]$) fail as market regimes change and individual models experience alpha decay. Applying **Exponential Recency Memory Decay ($\lambda$)** to historical losses or Information Coefficients (IC) combined with **Softmax Dynamic Reweighting** allows high-performing models to gain weight while demoting underperforming models below a minimum weight floor ($w_{\text{min}}$).

## Prerequisites

- Model telemetry payload (`model_id`, `recent_loss`, `recent_ic`, `days_active`).
- Ensemble config (`decay_factor_lambda`: e.g. 0.95, `temperature_beta`: e.g. 2.0, `min_weight_floor`: e.g. 0.05, `weighting_method`: `'EXPONENTIAL_LOSS'`, `'IC_SOFTMAX'`).

## Workflow

1. **Recency Memory Decay Update**:
   - Update rolling decayed loss or IC for each model $m$:
     $$\bar{L}_{m, t} = \lambda \cdot \bar{L}_{m, t-1} + (1 - \lambda) \cdot L_{m, t}$$
2. **Softmax Dynamic Weight Calculation**:
   - Compute raw softmax weights:
     $$w_{m, t} = \frac{\exp(-\beta \cdot \bar{L}_{m, t})}{\sum \exp(-\beta \cdot \bar{L}_{j, t})}$$
3. **Weight Floor & Model Demotion Circuit Breaker**:
   - If raw weight $w_{m, t} < w_{\text{min}}$ or model IC $\le 0$, flag status as `MODEL_DEMOTED`.
   - Redistribute weight to remaining active models and normalize ($\sum w_{\text{active}} = 1.0$).
4. **Audit Report Generation**: Output structured `EnsembleWeightReport`.

> Full procedure: see `references/workflows.md`.
> Standards reference: see `references/standards.md`.
> Printable pre-flight checklist: see `assets/checklist.md`.

## Common Pitfalls

- **Static Ensemble Weighting**: Keeping equal weights $1/M$ indefinitely, letting broken/decayed models dilute profitable signals.
- **Over-sensitive Temperature ($\beta$)**: Setting $\beta$ too high, causing extreme weight churn and single-model dominance after short-term loss spikes.
- **Ignoring Model Demotion**: Allowing models with negative IC to retain weight in live execution.

## Verification

- Instantiate `EnsembleWeightDecayEngine`. Audit 3-model ensemble (Model A: loss 0.1, Model B: loss 0.5, Model C: loss 1.2) $\implies$ verify Model A receives highest weight ($\sim 80\%$), Model C falls below floor and gets demoted, and active weights sum to 1.0.
- Run `python scripts/test_ensemble_weight_decay.py`.

## Related Skills

- `model-staleness-detection`
- `model-serving-infrastructure-ab-testing`
---
