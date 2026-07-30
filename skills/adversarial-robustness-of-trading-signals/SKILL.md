---
name: adversarial-robustness-of-trading-signals
description: Tests financial machine learning models for adversarial vulnerability
  by injecting epsilon-bounded perturbations (simulating flash-crash noise or FGSM
  attacks) and measuring signal flip rates.
domain: algorithmic-trading
subdomain: financial-ml-robustness
tags:
- ml
- trading
- adversarial-robustness
- fgsm
- signal-processing
brokers_frameworks:
- scikit-learn
- numpy
version: 1.1.0
author: System
license: MIT
---

## When to Use

Invoke this skill before deploying any machine learning model (e.g., Random Forests, Gradient Boosted Trees, or Deep Neural Networks) to production trading. Financial markets are highly noisy, and an attacker (or a market flash crash) can introduce imperceptible noise to order book features that forces a model to flip from a "BUY" to a "SELL" signal. This skill measures the model's Adversarial Vulnerability Score.

## Prerequisites

- Python 3.9+
- `numpy`
- A trained model with a `predict(X)` method.

## Workflow

1. **Baseline Evaluation**: Compute trading signals on clean historical feature data $X$.
2. **Adversarial Perturbation**: Inject bounded adversarial noise $\epsilon$ (epsilon) to the features. This simulates FGSM (Fast Gradient Sign Method) style evasion attacks or extreme market microstructure noise.
3. **Signal Flip Measurement**: Count the percentage of predictions that changed ($y_{\text{clean}} \neq y_{\text{adversarial}}$).
4. **Vulnerability Scoring**: If the flip rate exceeds the tolerance threshold (e.g., $> 5\%$), flag the model as non-robust and reject deployment.

## Common Pitfalls

- **Testing with only Gaussian Noise**: Simple Gaussian noise doesn't reflect true adversarial perturbations. Worst-case directional noise (bounding to epsilon limits) is required.
- **Ignoring Feature Scaling**: Injecting raw noise without respecting the scale of the features (e.g., adding 0.01 noise to a feature bounded between 0 and 1, vs a feature bounded between 1000 and 2000).

## Verification

Run `python scripts/test_signal_adversarial_tester.py` to confirm the injection logic and vulnerability scoring triggers correctly when models flip predictions under noise.

## Related Skills

- `feature-engineering`
- `backtest-outlier-and-bad-tick-filtering`
