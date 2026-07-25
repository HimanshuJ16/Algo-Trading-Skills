---
name: quantile-regression-for-uncertainty-aware-signals
description: >-
  Use when training ML signal generation models to predict conditional return quantiles (e.g. 10th, 50th, 90th percentiles) rather than single point forecasts, enabling uncertainty-aware confidence-scaled position sizing.
domain: algorithmic-trading
subdomain: financial-ml
tags: ["financial-ml", "quantile-regression", "uncertainty-estimation", "pinball-loss", "confidence-scaling", "position-sizing"]
brokers_frameworks: ["Quantile Regression Signal Engine", "Python NumPy"]
version: "1.0"
author: algo-trading-skills-contributors
license: Apache-2.0
---

## When to Use

Invoke this skill when building signal generation models for position sizing. Traditional point forecast models ($E[Y|X]$) predict expected return but provide no measure of prediction uncertainty (variance or tail risk). Quantile regression fits lower ($\tau=0.10$), median ($\tau=0.50$), and upper ($\tau=0.90$) conditional quantiles using Pinball Loss ($L_{\tau}(y, \hat{y}) = \max(\tau(y - \hat{y}), (\tau - 1)(y - \hat{y}))$). The interquartile range ($q_{0.90} - q_{0.10}$) measures prediction uncertainty: wide bands reduce position size, narrow bands increase allocation confidence.

## Prerequisites

- Feature vector $X$ and target return $y$.
- Target quantiles $\tau \in \{0.10, 0.50, 0.90\}$.

## Workflow

1. **Evaluate Pinball (Quantile) Loss**:
   $$L_{\tau}(y, \hat{y}) = \begin{cases} \tau (y - \hat{y}) & \text{if } y \ge \hat{y} \\ (1 - \tau) (\hat{y} - y) & \text{if } y < \hat{y} \end{cases}$$
2. **Train Multi-Quantile Model**: Fit parameter vectors $W_{\tau}$ for each quantile $\tau$.
3. **Compute Uncertainty Band**:
   $$\text{UncertaintyWidth} = \hat{q}_{0.90} - \hat{q}_{0.10}$$
4. **Scale Position Size by Confidence**:
   $$\text{SizeMultiplier} = \frac{\hat{q}_{0.50}}{\text{max}(\epsilon, \text{UncertaintyWidth})}$$

> Full procedure: see `references/workflows.md`.
> Standards reference: see `references/standards.md`.
> Printable pre-flight checklist: see `assets/checklist.md`.

## Common Pitfalls

- **Quantile Crossing**: Predicting $\hat{q}_{0.10} > \hat{q}_{0.90}$ due to unconstrained separate models. Enforce non-crossing sorting.
- **Using MSE Loss for Extreme Quantiles**: MSE fits conditional mean, completely failing to estimate tail quantiles.

## Verification

- Train quantile model on synthetic return distribution, verify $\hat{q}_{0.90} > \hat{q}_{0.50} > \hat{q}_{0.10}$.
- Run `python scripts/test_quantile_regression_model.py` and confirm 100% pass rate.

## Related Skills

- `position-sizing-and-portfolio-optimization`
- `explainable-boosting-machines-for-regulated-signals`
---
