---
name: new-strategy-onboarding-checklist
description: >-
  New strategy onboarding gatekeeper engine evaluating 4-gate governance standards (Backtest Robustness, Operational Runtime, Model Risk, Compliance Approval).
domain: Portfolio Multi Strategy
subdomain: Strategy Lifecycle Governance & Production Onboarding
tags: ["onboarding", "strategy-governance", "gatekeeper", "model-risk", "paper-trading", "compliance-approval", "production-readiness"]
brokers_frameworks: ["Hedge Fund Governance Framework", "Model Risk Management (SR 11-7)", "Python Dataclasses"]
version: "1.0.0"
author: algo-trading-skills-contributors
license: Apache-2.0
---

## When to Use

Use this skill when promoting new quantitative trading strategies from R&D backtesting into live capital deployment. Deploying unverified strategies risks severe capital loss due to over-fitted backtests, execution bugs, missing kill switches, or regulatory non-compliance. This engine enforces a **Four-Gate Governance Check**:
1. **Backtest & Robustness Gate**: Walk-Forward score $\ge 0.70$, $\ge 3$ market regimes covered, backtest Sharpe $\ge 1.5$.
2. **Operational Runtime Gate**: $\ge 14$ days clean paper trading with 0 critical execution errors and kill switch integration.
3. **Model Risk Gate**: Completed model card and documented parameter defaults.
4. **Compliance & Legal Gate**: Regulatory compliance sign-off.

## Prerequisites

- Strategy onboarding payload (`strategy_id`, `walk_forward_score`, `regimes_covered`, `backtest_sharpe`, `paper_trading_days`, `paper_trading_errors`, `kill_switch_integrated`, `model_card_completed`, `compliance_approved`).
- Onboarding policy config (`min_walk_forward_score`: e.g. 0.70, `min_regimes_covered`: 3, `min_paper_trading_days`: 14).

## Workflow

1. **Gate 1 - Backtest Robustness Audit**:
   - Verify walk-forward score $\ge 0.70$, regimes covered $\ge 3$, and Sharpe ratio $\ge 1.5$.
2. **Gate 2 - Operational & Paper Trading Audit**:
   - Verify paper trading duration $\ge 14$ days, 0 execution errors, and kill switch tested.
3. **Gate 3 - Model Risk Documentation Audit**:
   - Verify completed model card and documented parameter sensitivity limits.
4. **Gate 4 - Compliance & Governance Sign-Off**:
   - Confirm explicit compliance officer approval.
5. **Audit Report Generation**: Output structured `OnboardingAuditReport`.

> Full procedure: see `references/workflows.md`.
> Standards reference: see `references/standards.md`.
> Printable pre-flight checklist: see `assets/checklist.md`.

## Common Pitfalls

- **Bypassing Paper Trading**: Deploying a backtested strategy directly to live capital without paper trading validation.
- **Ignoring Kill Switch Integration**: Launching strategies that cannot be instantly halted via central risk API.
- **Unverified Model Documentation**: Deploying black-box strategies without documented parameter limits or decay conditions.

## Verification

- Instantiate `NewStrategyOnboardingEngine`. Audit fully compliant strategy (Walk-Forward 0.85, 4 regimes, 14 days paper trading, 0 errors, kill switch ok) $\implies$ verify status `ONBOARDING_PASSED`. Audit strategy with 3 days paper trading and unapproved compliance $\implies$ verify status `ONBOARDING_REJECTED` with specific gate failures listed.
- Run `python scripts/test_new_strategy_onboarding_checklist.py`.

## Related Skills

- `paper-to-live-promotion-checklist`
- `model-card-documentation-for-trading-models`
- `strategy-research-to-production-pipeline-governance`
---
