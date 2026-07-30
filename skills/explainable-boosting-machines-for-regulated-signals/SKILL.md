---
name: explainable-boosting-machines-for-regulated-signals
description: >-
  Quantitative glass-box ML engine using Explainable Boosting Machines (EBM / GA2M) for regulated trading signal generation, exact shape-function feature attributions, and SR 11-7 model governance compliance.
domain: Financial ML & Governance
subdomain: Glass-Box ML & Regulatory Signals (EBM / GA2M)
tags: ["ebm", "ga2m", "explainable-boosting", "glass-box-ml", "sr-11-7", "mifid-ii-rts-6", "model-governance"]
brokers_frameworks: ["InterpretML EBM", "GA2M Framework", "Python Dataclasses"]
version: "1.0.0"
author: algo-trading-skills-contributors
license: Apache-2.0
---

## When to Use

Use this skill in regulated quantitative trading, credit/option pricing models, and model risk governance (Fed SR 11-7, MiFID II RTS 6). Black-box ML models (XGBoost, Deep Neural Nets) create regulatory compliance hurdles because post-hoc explainers (SHAP/LIME) rely on sampling approximations. **Explainable Boosting Machines (EBM / GA2M)** decompose predictions into exact additive 1D shape functions ($f_i(x_i)$) and 2D interaction terms ($f_{jk}(x_j, x_k)$), providing 100% transparent glass-box trading signals.

## Prerequisites

- Model baseline intercept $\beta_0$.
- Feature shape function tables $f_i(x_i)$ for single features and $f_{jk}(x_j, x_k)$ for interactions.
- Input feature vector $\{x_1, x_2, \dots, x_M\}$.

## Workflow

1. **Exact Shape Function Evaluation**:
   - Compute single-feature additive contributions $f_i(x_i)$ for each feature.
   - Compute pairwise interaction contributions $f_{jk}(x_j, x_k)$ for key feature pairs.
2. **Prediction Composition**:
   - $\hat{Y} = \beta_0 + \sum_{i=1}^M f_i(x_i) + \sum_{(j,k)} f_{jk}(x_j, x_k)$.
3. **Monotonicity & Regulatory Sanity Audit**:
   - Verify shape functions $f_i(x_i)$ adhere to mandatory financial monotonicity bounds.
4. **Audit Report Generation**: Output structured `EbmSignalAuditReport`.

> Full procedure: see `references/workflows.md`.
> Standards reference: see `references/standards.md`.
> Printable pre-flight checklist: see `assets/checklist.md`.

## Common Pitfalls

- **Confusing EBM Exact Attributions with SHAP Approximations**: Assuming EBM requires sampling algorithms; EBM feature contributions are exact evaluations of $f_i(x_i)$.
- **Ignoring Pairwise Interactions ($f_{jk}$)**: Omitting 2D interaction terms ($GA^2M$), missing joint volatility-volume non-linear dynamics.
- **Allowing Non-Monotonic Noise in Shape Curves**: Failing to smooth shape functions, allowing noisy empirical data to create counter-intuitive signal spikes.

## Verification

- Instantiate `ExplainableBoostingPricerEngine`. Register 1D shape functions for RSI ($f_{\text{rsi}}$), Volatility ($f_{\text{vol}}$), and 2D interaction ($f_{\text{rsi,vol}}$). Evaluate input vector (RSI = 75, Vol = 0.25). Verify engine computes exact sum ($\beta_0 + f_{\text{rsi}} + f_{\text{vol}} + f_{\text{rsi,vol}} \equiv \hat{Y}$), validates monotonicity, and outputs `PASS_GOVERNANCE_AUDIT`.
- Run `python scripts/test_explainable_boosting_pricer.py`.

## Related Skills

- `explainability-for-live-trading-signals`
- `ensemble-signal-combination-without-overfitting`
---
