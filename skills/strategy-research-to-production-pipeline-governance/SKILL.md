---
name: strategy-research-to-production-pipeline-governance
description: >-
  Production-grade Research-to-Production Pipeline Governance Engine enforcing multi-stage promotion gatekeeping, reproducibility (git hash & dataset checksum), shadow paper-trading tracking error audits, Risk Committee sign-offs, and immutable audit ledgers.
domain: Investment Governance & MLOps
subdomain: Model Lifecycle & Pipeline Governance
tags: ["pipeline-governance", "research-to-production", "model-validation", "reproducibility", "shadow-trading", "risk-signoff"]
brokers_frameworks: ["Policy-as-Code Governance", "Quantitative MLOps Framework", "Python Dataclasses"]
version: "1.0.0"
author: algo-trading-skills-contributors
license: Apache-2.0
---

## When to Use

Use this skill when governing the promotion of quantitative trading strategies from research to live production capital. Un-vetted strategies deployed directly from Jupyter notebooks into production often suffer from backtest overfitting, silent data leakage, untracked code changes, or execution slippage. This engine enforces sequential promotion gatekeeping (`RESEARCH_BACKTEST` $\to$ `INDEPENDENT_VALIDATION` $\to$ `PAPER_TRADING_SHADOW` $\to$ `STAGING_CANARY` $\to$ `LIVE_PRODUCTION`), auditing reproducibility, out-of-sample performance, shadow tracking error ($\le 5.0\%$), and Risk Committee sign-off.

## Prerequisites

- Strategy promotion artifacts (`StagePromotionArtifacts`: `git_commit_hash`, `dataset_checksum`, `backtest_sharpe`, `backtest_max_drawdown_pct`, `shadow_tracking_error_pct`, `paper_trading_days`, `has_risk_committee_signoff`, `author_id`, `validator_id`).

## Workflow

1. **Reproducibility & Code Audit**:
   - Verify valid Git commit hash and dataset checksum.
2. **Backtest Out-of-Sample Quantitative Audit**:
   - Check if Out-of-Sample Sharpe $\ge 1.50$ and Max Drawdown $\le 15.0\%$.
3. **Paper Trading Shadow Execution Audit**:
   - Check if shadow paper trading duration $\ge 14$ days and shadow tracking error $\le 5.0\%$.
4. **Risk Committee Sign-Off Gate**:
   - Require formal validator ID and Risk Committee sign-off for promotion to `LIVE_PRODUCTION`.
5. **Cryptographic Audit Log Generation**:
   - Generate SHA-256 audit hash and output structured `StagePromotionDecision`.

> Full procedure: see `references/workflows.md`.
> Standards reference: see `references/standards.md`.
> Printable pre-flight checklist: see `assets/checklist.md`.

## Common Pitfalls

- **Bypassing Shadow Paper Trading**: Deploying research models directly to live markets without validating real-time market data feed alignment or execution latency.
- **Untracked Code or Data Versioning**: Deploying strategies without pinned Git commit hashes or dataset checksums, creating non-reproducible models.
- **Unvalidated Data Leakage**: Optimizing parameters on full historical dataset without out-of-sample validation or independent risk team verification.

## Verification

- Instantiate `StrategyResearchToProductionGovernanceEngine`. Submit valid artifacts for promotion to `LIVE_PRODUCTION` (Sharpe $= 2.1$, tracking error $= 2.5\%$, 21 days paper trading, Risk sign-off present) $\implies$ verify `is_approved = True` and SHA-256 audit hash created. Submit failing artifacts (missing sign-off, $8.5\%$ tracking error) $\implies$ verify `is_approved = False` with 3 failed gate descriptions.
- Run `python scripts/test_strategy_research_to_production_pipeline_governance.py`.

## Related Skills

- `strategy-committee-governance-for-capital-allocation-decisions`
- `new-strategy-onboarding-checklist`
---
