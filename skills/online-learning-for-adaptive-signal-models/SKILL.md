---
name: online-learning-for-adaptive-signal-models
description: >-
  Use when deploying machine learning signal models to adapt continuously to shifting market microstructures using online/incremental learning algorithms (Stochastic Gradient Descent / Recursive Least Squares) without full batch retraining.
domain: algorithmic-trading
subdomain: financial-ml
tags: ["financial-ml", "online-learning", "adaptive-model", "incremental-learning", "rls", "concept-drift", "signal-processing"]
brokers_frameworks: ["Online Adaptive Model Engine", "Python NumPy"]
version: "1.0"
author: algo-trading-skills-contributors
license: Apache-2.0
---

## When to Use

Invoke this skill when deploying ML signal generation models in fast-moving market regimes. Batch-trained models degrade as market dynamics change (alpha decay), requiring computationally heavy full retraining pipelines. Online learning continuously updates model weight vectors $W_{t+1}$ sample-by-sample upon observing new true targets $y_t$, adapting to regime shifts with zero offline retraining latency.

## Prerequisites

- Streaming feature vector $X_t$ and realized target $y_t$ (e.g. 5-minute forward return).
- Decay factor $\lambda \in (0.95, 1.0]$ or learning rate $\eta > 0$.

## Workflow

1. **Perform Online Inference**: Compute prediction $\hat{y}_t = X_t^T W_t$.
2. **Observe Realized Target**: Receive true outcome $y_t$ at horizon completion.
3. **Compute Online Error & Update Weights**:
   - Update weight vector using Recursive Least Squares (RLS) or SGD:
     $$W_{t+1} = W_t + \eta \cdot (y_t - \hat{y}_t) \cdot X_t$$
4. **Monitor Concept Drift**: Track rolling Mean Absolute Error (MAE). Adapt learning rate if drift exceeds threshold.

> Full procedure: see `references/workflows.md`.
> Standards reference: see `references/standards.md`.
> Printable pre-flight checklist: see `assets/checklist.md`.

## Common Pitfalls

- **Exploding Weights During Outliers**: Updating weights on bad ticks without gradient clipping ($|W_i| \le W_{\text{max}}$).
- **Over-Adapting to Noise**: Setting learning rate $\eta$ too high, causing model weights to oscillate randomly.
- **Updating with Un-Realized Future Targets**: Using $y_{t+h}$ to update $W_t$ before horizon $h$ has actually elapsed.

## Verification

- Stream 200 non-stationary samples with shifting linear relationship, verify weight vector converges dynamically.
- Run `python scripts/test_online_adaptive_model.py` and confirm 100% pass rate.

## Related Skills

- `model-staleness-detection`
- `offline-train-online-infer-deployment`
- `regime-detection-for-strategy-switching`
---
