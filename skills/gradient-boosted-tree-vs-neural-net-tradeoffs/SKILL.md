---
name: gradient-boosted-tree-vs-neural-net-tradeoffs
description: >-
  Quantitative model architecture selection engine for evaluating GBDTs (LightGBM/XGBoost) vs Deep Neural Networks (LSTM/Transformers) across accuracy, latency, interpretability, and regime-shift robustness.
domain: Financial ML & Architecture
subdomain: Model Family Selection & Trade-off Evaluation
tags: ["gradient-boosted-trees", "lightgbm", "xgboost", "neural-networks", "lstm", "transformer", "model-selection", "sr-11-7", "interpretability"]
brokers_frameworks: ["LightGBM", "XGBoost", "PyTorch / TensorFlow", "Python Dataclasses"]
version: "1.0.0"
author: algo-trading-skills-contributors
license: Apache-2.0
---

## When to Use

Use this skill when designing financial machine learning model architectures. A primary engineering decision in quantitative finance is selecting between **Gradient Boosted Decision Trees (GBDTs: LightGBM, XGBoost, CatBoost)** and **Deep Neural Networks (LSTMs, Transformers, TFTs)**. GBDTs dominate tabular financial data with fast training, robust handling of non-stationary distributions, and native SHAP interpretability (SR 11-7 compliance). Neural Networks excel at learning representations from raw high-frequency tick/order-book image sequences. This module evaluates dataset modality, latency budgets, and regulatory requirements to output deterministic model recommendations.

## Prerequisites

- Dataset specifications (modality: `TABULAR_ENGINEERED` vs `RAW_HIGH_FREQUENCY_TICKS`, sample size $N$, feature count $M$).
- Target latency budget (e.g., $< 500\mu\text{s}$ vs $> 10\text{ms}$).
- Regulatory interpretability requirement (`STRICT_SR11_7_MIFID2` vs `INTERNAL_RESEARCH`).

## Workflow

1. **Dataset Modality & Constraint Evaluation**:
   - Assess data structure (engineered tabular features vs raw tick sequence).
   - Evaluate strictness of SR 11-7 model governance and inference latency constraints.
2. **Dimension Scoring (0 - 10 Score for GBDT vs NN)**:
   - **Tabular Data Fitting**: GBDT=9.5, NN=5.5.
   - **Sequential Pattern Extraction**: GBDT=5.0, NN=9.0.
   - **Interpretability & Compliance**: GBDT=9.0, NN=4.0.
   - **Inference Speed & Microsecond Latency**: GBDT=9.0, NN=5.0.
   - **Regime Shift Robustness**: GBDT=8.5, NN=4.5.
3. **Model Family Decision**:
   - Total Score Comparison:
     - GBDT Score $>$ NN Score $+ 2.0 \implies$ `RECOMMEND_LIGHTGBM_XGBOOST`.
     - NN Score $>$ GBDT Score $+ 2.0 \implies$ `RECOMMEND_NEURAL_NETWORK_LSTM_TRANSFORMER`.
     - Close scores $\implies$ `RECOMMEND_HYBRID_ENSEMBLE`.
4. **Audit Report Generation**: Output structured `ModelFamilyTradeoffReport`.

> Full procedure: see `references/workflows.md`.
> Standards reference: see `references/standards.md`.
> Printable pre-flight checklist: see `assets/checklist.md`.

## Common Pitfalls

- **Forcing Deep Learning on Tabular Data**: Applying complex Transformers to tabular financial features, resulting in longer training times, overfitting, and poor out-of-sample Sharpe ratios.
- **Ignoring SR 11-7 Regulatory Interpretability**: Deploying black-box Deep Neural Networks for regulated signals without explainability logs, failing model risk audits.
- **Underestimating Deep Learning Inference Latency**: Expecting multi-layer LSTMs to execute within sub-100 microsecond HFT latency budgets without specialized FPGA/C++ runtime acceleration.

## Verification

- Instantiate `ModelFamilySelectorEngine`. Test Scenario 1: Tabular data ($N=100\text{k}$, $M=50$), Latency Budget $= 200\mu\text{s}$, Strict SR 11-7 Compliance $\implies$ verify engine outputs `RECOMMEND_LIGHTGBM_XGBOOST` (GBDT Score = 9.0 vs NN = 4.8). Test Scenario 2: Raw HFT tick sequence ($N=5\text{M}$ ticks), Latency Budget $= 20\text{ms}$, Internal Research $\implies$ verify engine outputs `RECOMMEND_NEURAL_NETWORK_LSTM_TRANSFORMER`.
- Run `python scripts/test_model_family_selector.py`.

## Related Skills

- `explainable-boosting-machines-for-regulated-signals`
- `model-inference-latency-budget-for-live-trading`
---
