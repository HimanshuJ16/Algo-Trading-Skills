---
name: explainability-for-live-trading-signals
description: Use when deploying ML trading models to decompose raw live signal predictions
  into local feature attributions (SHAP/contributions) and generate human-readable
  audit explanations
domain: algorithmic-trading
subdomain: financial-ml
tags:
- financial-ml
- explainable-ai
- shap-values
- signal-attribution
- compliance-audit
brokers_frameworks:
- SHAP
- Captum
- scikit-learn
- XGBoost
- Custom Explainers
version: "1.0.0"
author: algo-trading-skills-contributors
license: Apache-2.0
---

## When to Use

Invoke this whenever deploying Machine Learning (ML) or complex quantitative models for live signal generation. Black-box ML models (XGBoost, Random Forests, Neural Networks) that output raw probability or score signals (e.g. $+0.82$ Buy) without feature attribution create compliance vulnerabilities and lack trader trust. If an unexpected order fires during market stress, risk officers must instantly identify which input features drove the decision. Generating local feature attribution contributions ($\phi_i$), top driver rankings, and structured human-readable natural language audit reports for every live signal is mandatory.

## Prerequisites

- Model feature input dictionary or vector $\{f_1: v_1, f_2: v_2, \dots, f_M: v_M\}$.
- Model baseline value (expected prediction value $E[Y]$).
- Feature attribution weights or SHAP values.

## Workflow

1. **Capture Live Model Prediction & Input Features**:
   - Ingest raw model output prediction score $\hat{Y}$ and feature values $X_t$.

2. **Compute Local Feature Attributions ($\phi_i$)**:
   - Decompose prediction score relative to model base value:
     $$\hat{Y} = \text{BaseValue} + \sum_{i=1}^M \phi_i$$

3. **Extract Top Positive & Negative Drivers**:
   - Rank features by contribution magnitude $|\phi_i|$.
   - Identify top bullish factors ($\phi_i > 0$) and top bearish factors ($\phi_i < 0$).

4. **Generate Human-Readable Audit Explanation**:
   - Format natural language audit string:
     `"BUY signal (+0.82) triggered primarily by High RSI Momentum (+0.35) and Low Volatility (+0.25), offset by Negative Sentiment (-0.08)."`

5. **Write Compliance Audit Log (`log_explainable_signal`)**:
   - Store signal timestamp, prediction score, base value, feature contribution dictionary, and natural language summary into an immutable JSON compliance log.

> Full step-by-step procedure with broker-specific detail: see `references/workflows.md`.
> Broker/framework coverage table for this skill: see `references/standards.md`.
> Printable pre-flight checklist: see `assets/checklist.md`.

## Common Pitfalls

- **Black-Box Production Signals**: Transmitting raw model prediction scores to execution algorithms without feature attribution logging.
- **Global vs Local Attribution Confusion**: Relying on global feature importance (e.g. Gini importance) rather than local instance-level contributions for specific live signals.
- **Unvalidated Feature Contribution Sums**: Failing to verify that base value plus sum of feature attributions equals the total model output score ($\text{BaseValue} + \sum \phi_i = \hat{Y}$).

## Verification

- Submit synthetic feature vector and verify `LiveSignalExplainer` computes exact feature contributions summing to model output score.
- Verify natural language audit report correctly identifies top positive and top negative feature drivers.
- Verify `log_explainable_signal()` writes structured JSON audit entry.
- Run unit test suite `python scripts/test_signal_explainer.py` and confirm 100% pass rate.

## Related Skills

- `feature-store-for-live-and-backtest-parity`
- `ensemble-signal-combination-without-overfitting`
- `regime-detection-for-strategy-switching`
---
