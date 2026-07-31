---
name: model-card-documentation-for-trading-models
description: >-
  Quantitative model governance and Model Card generation engine producing SR 26-2 / MRM compliant documentation for machine learning alphas, execution algorithms, and risk models.
domain: Quant Research Alt Data
subdomain: Model Governance & Model Risk Management (MRM)
tags: ["model-card", "model-governance", "sr-26-2", "mrm", "model-risk", "documentation-generator", "quant-compliance"]
brokers_frameworks: ["SR 26-2 Guidance", "Mitchell et al. Model Cards", "Python Dataclasses"]
version: "1.0.0"
author: algo-trading-skills-contributors
license: Apache-2.0
---

## When to Use

Use this skill when documenting quantitative trading models, machine learning alphas, or execution algorithms for production deployment under institutional Model Risk Management (MRM) and regulatory frameworks (SR 26-2 / SR 11-7 guidance). Institutional trading firms require standardized **Model Cards** detailing model identity, intended use, out-of-scope operational regimes, training feature lineage, backtest performance metrics (Sharpe ratio, Max Drawdown, Capacity), and emergency kill-switch conditions.

## Prerequisites

- Model identity metadata (`model_id`, `name`, `version`, `author`, `model_type`: `'ML_ALPHA'`, `'EXECUTION_ALGO'`, `'RISK_MODEL'`, `asset_class`, `intended_use`, `out_of_scope_uses`).
- Model performance metrics (`sharpe_ratio`, `sortino_ratio`, `max_drawdown_pct`, `annual_return_pct`, `win_rate_pct`, `capacity_usd`).
- Model governance configuration (`sr_compliant`: bool, `is_validated_by_mrm`: bool, `validation_date`, `kill_switch_triggers`).

## Workflow

1. **Model Governance & MRM Audit**:
   - Audit `is_validated_by_mrm == True`, `intended_use`, and `out_of_scope_uses`.
   - If un-validated or missing out-of-scope definition $\implies$ Flag `MODEL_CARD_NON_COMPLIANT_DEFICIT`.
2. **Performance Metrics Verification**:
   - Audit Sharpe ratio ($\ge 1.0$), Max Drawdown ($\le 25\%$), and Capital Capacity ($C_{\text{max}}$).
3. **Markdown & JSON Model Card Synthesis**:
   - Generate structured Markdown documentation and JSON audit schema.
4. **Audit Report Generation**: Output structured `ModelCardReport`.

> Full procedure: see `references/workflows.md`.
> Standards reference: see `references/standards.md`.
> Printable pre-flight checklist: see `assets/checklist.md`.

## Common Pitfalls

- **Missing Out-of-Scope Use Definitions**: Failing to specify prohibited market regimes (e.g. trading during earnings releases or illiquid post-market sessions).
- **Unvalidated Production Models**: Deploying machine learning alphas without independent Model Risk Management (MRM) validation sign-off.
- **Incomplete Feature Lineage Documentation**: Omitting feature engineering transformation formulas, leading to un-reproducible model cards.

## Verification

- Instantiate `ModelCardGeneratorEngine`. Audit ML Alpha model (`Sharpe=2.1`, `Drawdown=12%`, `MRM_Validated=True`) $\implies$ verify generated Markdown Model Card contains all 6 required MRM sections, exports valid JSON schema, and approves `MODEL_CARD_GENERATED_COMPLIANT`.
- Run `python scripts/test_model_card_generator.py`.

## Related Skills

- `research-idea-pipeline-tracking-and-prioritization`
- `factor-research-multiple-testing-correction`
---
