---
name: execution-cost-model-recalibration-cadence
description: >-
  Quantitative TCA engine for auditing execution transaction cost model (TCM) tracking error (RMSE), detecting systematic prediction bias, and executing recalibration cadences.
domain: Execution Algorithms
subdomain: Transaction Cost Analysis (TCA) & Model Governance
tags: ["execution-cost-model", "recalibration-cadence", "tca", "tracking-error", "rmse", "prediction-bias", "almgren-chriss"]
brokers_frameworks: ["Square-Root Impact Model", "TCA Governance", "Python Dataclasses", "Least-Squares Regression"]
version: "1.0.0"
author: algo-trading-skills-contributors
license: Apache-2.0
---

## When to Use

Use this skill in quantitative execution research, Transaction Cost Analysis (TCA), and portfolio management systems. Execution cost models (Almgren-Chriss, Square-Root Impact Rule) predict expected slippage ($\text{IS}_{\text{predicted}}$). As market regimes shift (volatility spikes, tick size changes, fee schedule updates), models experience tracking error drift ($\text{RMSE} > 3.5\text{ bps}$) and systematic prediction bias ($\bar{\epsilon} \ne 0$). This module enforces scheduled (weekly/monthly) and trigger-based model parameter recalibration cadences.

## Prerequisites

- Trade execution history ($N$ trades with `realized_is_bps`, `predicted_is_bps`, `order_qty`, `adv`, `spread_bps`, `volatility_pct`).
- Active model parameters ($\eta_{\text{active}}$, $\gamma_{\text{active}}$).
- Recalibration thresholds ($\text{RMSE}_{\text{max}} = 3.5\text{ bps}$, $|\bar{\epsilon}_{\text{max}}| = 1.5\text{ bps}$).

## Workflow

1. **Model Performance Audit**:
   - Compute Tracking Error: $\text{RMSE} = \sqrt{\frac{1}{N} \sum (\text{IS}_{\text{realized}} - \text{IS}_{\text{predicted}})^2}$.
   - Compute Systematic Bias: $\bar{\epsilon} = \frac{1}{N} \sum (\text{IS}_{\text{realized}} - \text{IS}_{\text{predicted}})$.
2. **Recalibration Trigger Audit**:
   - If $\text{RMSE} > \text{RMSE}_{\text{max}}$ OR $|\bar{\epsilon}| > |\bar{\epsilon}_{\text{max}}| \implies$ Trigger Recalibration.
3. **Parameter Least-Squares Refitting**:
   - Refit impact coefficients ($\eta^*, \gamma^*$) using recent trade sample data:
     $$\text{IS}_{\text{realized}} = \eta \cdot \text{Spread} + \gamma \cdot \sigma \cdot \sqrt{\frac{\text{Qty}}{\text{ADV}}}$$
4. **Audit Report Generation**: Output structured `CostModelRecalibrationReport`.

> Full procedure: see `references/workflows.md`.
> Standards reference: see `references/standards.md`.
> Printable pre-flight checklist: see `assets/checklist.md`.

## Common Pitfalls

- **Over-Recalibrating on Transient Noise**: Refitting cost models daily on tiny trade sample sizes, introducing high parameter variance and unstable portfolio optimization.
- **Ignoring Systematic Prediction Bias**: Focusing solely on correlation while ignoring constant under-prediction ($\bar{\epsilon} = +4.0\text{ bps}$), causing portfolio managers to underestimate trading costs.
- **Using Outdated Historical Trade Windows**: Training models on calm, low-volatility historical data during a major market crash regime.

## Verification

- Instantiate `ExecutionCostModelRecalibrationEngine`. Input active parameters ($\eta = 0.5$, $\gamma = 1.0$). Ingest 100 recent trade execution records. Scenario 1: Clean prediction alignment ($\text{RMSE} = 1.2\text{ bps}$, bias = $+0.2\text{ bps}$) $\implies$ verify engine outputs `MODEL_PARAMETER_STABLE`. Scenario 2: Volatility regime shift ($\text{RMSE} = 4.8\text{ bps}$, bias = $+3.1\text{ bps}$) $\implies$ verify engine triggers `RECALIBRATION_RECOMMENDED` and provides refitted parameters ($\eta^*, \gamma^*$).
- Run `python scripts/test_execution_cost_model_recalibration_cadence.py`.

## Related Skills

- `execution-algo-parameter-optimization-via-backtest`
- `execution-slippage-attribution-timing-vs-sizing`
---
