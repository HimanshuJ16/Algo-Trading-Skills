---
name: risk-limit-calibration-against-historical-drawdowns
description: >-
  Production-grade risk limit calibration engine estimating max drawdown, VaR/CVaR (Expected Shortfall), Ulcer Index, and extreme tail risk from historical return series to calibrate prudent pre-trade risk limits, daily loss thresholds, and position scaling multipliers.
domain: Risk Management & Quantitative Calibration
subdomain: Limit Calibration & Tail Risk Modeling
tags: ["drawdown-calibration", "var-cvar", "ulcer-index", "tail-risk", "risk-limits", "position-scaling"]
brokers_frameworks: ["Extreme Value Theory (EVT)", "Historical MaxDD", "Parametric VaR/CVaR", "Python Dataclasses"]
version: "1.0.0"
author: algo-trading-skills-contributors
license: Apache-2.0
---

## When to Use

Use this skill when establishing or recalibrating trading risk limits (max drawdown %, daily loss limit $, position size caps) for quantitative strategies. Setting arbitrary or uncalibrated risk limits leads to two major failures: setting limits too tight triggers false-positive strategy halts during normal market noise; setting limits too loose fails to prevent catastrophic capital loss during regime shifts. This engine computes historical max drawdown depth, duration, VaR/CVaR 99%, and Ulcer Index to calibrate prudent, stress-buffered risk limits.

## Prerequisites

- Daily return series (`daily_returns` with minimum 10 trading days).
- Portfolio capital ($).
- Stress buffer multiplier (default 1.5x) and calibration method (`HISTORICAL_MAX_DD`, `PARAMETRIC_VAR`, `EXTREME_VALUE_THEORY`).

## Workflow

1. **Drawdown Metrics Calculation**:
   - Compute equity curve, peak-to-trough max drawdown %, max drawdown duration in days, and Ulcer Index.
   - Compute 99% VaR and 99% CVaR (Expected Shortfall).
2. **Stress-Buffered Limit Calibration**:
   - Apply stress multiplier ($1.5 \times \text{Historical MaxDD}$ or EVT tail factor) to calculate calibrated max drawdown limit.
3. **Daily Loss Limit & Position Scaling**:
   - Calibrate daily loss limit ($3 \times \text{VaR}_{99}$ in USD).
   - Compute position scaling factor ($< 1.0$ if historical max drawdown exceeded 20%).
4. **Calibration Output & Audit Logging**: Output structured `CalibratedRiskLimits`.

> Full procedure: see `references/workflows.md`.
> Standards reference: see `references/standards.md`.
> Printable pre-flight checklist: see `assets/checklist.md`.

## Common Pitfalls

- **Calibrating on Short Data Windows**: Using less than 6 months of return history misses market regime shifts and tail events.
- **Ignoring Drawdown Duration**: Focusing only on peak-to-trough percentage depth while ignoring prolonged recovery durations.
- **No Stress Buffer**: Setting max drawdown limit equal to historical max drawdown, guaranteeing strategy halt on the next minor loss.

## Verification

- Instantiate `DrawdownLimitCalibratorEngine`. Calibrate limits for return series with 4.9% historical drawdown $\implies$ verify calibrated max drawdown limit $\approx 7.35\%$ ($1.5 \times 4.9\%$). Calibrate high drawdown series (23% max DD) $\implies$ verify position size scalar reduced to $< 1.0$.
- Run `python scripts/test_drawdown_limit_calibrator.py`.

## Related Skills

- `risk-limit-breach-escalation-matrix`
- `risk-metric-recalculation-frequency-tuning`
---
