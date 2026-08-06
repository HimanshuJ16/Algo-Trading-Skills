---
name: strategy-underperformance-remediation-decision-tree
description: >-
  Production-grade Strategy Underperformance Remediation Engine evaluating quantitative triage decision trees to separate execution failure, market-wide regime shifts, parameter drift, and structural alpha decay.
domain: Investment Governance & Remediation Management
subdomain: Quantitative Strategy Triage & Remediation
tags: ["underperformance-remediation", "triage-decision-tree", "alpha-decay", "parameter-recalibration", "execution-optimization", "strategy-governance"]
brokers_frameworks: ["Quantitative Triage Decision Tree", "Remediation Governance Matrix", "Python Dataclasses"]
version: "1.0.0"
author: algo-trading-skills-contributors
license: Apache-2.0
---

## When to Use

Use this skill when a quantitative strategy underperforms its historical backtest or risk-adjusted target limits ($\text{Sharpe} < 1.0$). Rather than arbitrarily tweaking parameters (which leads to backtest overfitting and HARKing), quantitative funds use a structured triage decision tree to diagnose the root cause of underperformance. This engine routes underperformance through 4 diagnostic nodes: Node 1 (Alpha Hypothesis Validity), Node 2 (Execution Slippage vs Expected Alpha), Node 3 (Peer Benchmark Regime Shift), and Node 4 (Parameter Drift), generating actionable remediation reports (`MANDATORY_STRATEGY_DECOMMISSION`, `OPTIMIZE_EXECUTION_AND_DATA`, `TEMPORARY_CAPITAL_DEGRADE_RETAIN_SIGNAL`, `RECALIBRATE_MODEL_PARAMETERS`).

## Prerequisites

- Strategy underperformance triage metrics (`UnderperformanceTriageMetrics`: `strategy_id`, `live_sharpe`, `backtest_sharpe`, `peer_benchmark_sharpe`, `realized_slippage_bps`, `expected_alpha_bps`, `is_data_feed_healthy`, `is_alpha_hypothesis_valid`).

## Workflow

1. **Fundamental Hypothesis Audit (Node 1)**:
   - If economic edge is no longer valid $\implies$ `MANDATORY_STRATEGY_DECOMMISSION` (Liquidate & retire).
2. **Execution & Data Quality Audit (Node 2)**:
   - If data feeds are unhealthy or $\frac{\text{Slippage}}{\text{Alpha}} > 50\% \implies$ `OPTIMIZE_EXECUTION_AND_DATA` (Fix SLAs or order slicing).
3. **Market-Wide Regime Shift Audit (Node 3)**:
   - If strategy and peer benchmark Sharpe are both $< 0.50 \implies$ `TEMPORARY_CAPITAL_DEGRADE_RETAIN_SIGNAL` (Cut capital 50%).
4. **Parameter Drift Audit (Node 4)**:
   - If strategy Sharpe $< 1.0$ while peers remain healthy $\ge 0.50 \implies$ `RECALIBRATE_MODEL_PARAMETERS` (Walk-forward parameter re-optimization).
5. **Execution Output**: Output structured `StrategyRemediationReport`.

> Full procedure: see `references/workflows.md`.
> Standards reference: see `references/standards.md`.
> Printable pre-flight checklist: see `assets/checklist.md`.

## Common Pitfalls

- **HARKing (Hypothesizing After Results are Known)**: Adding arbitrary indicators to "fix" underperformance without diagnosing if execution slippage or alpha decay was the true cause.
- **Decommissioning During Regime Shifts**: Retiring a strategy when the entire peer benchmark asset class is suffering from a temporary market-wide regime shift.
- **Ignoring Execution Slippage**: Recalibrating signal parameters when underperformance is actually caused by poor execution order slicing or broker commission drag.

## Verification

- Instantiate `StrategyUnderperformanceRemediationEngine`. Pass invalid alpha hypothesis $\implies$ verify `recommended_action = MANDATORY_STRATEGY_DECOMMISSION` and `is_decommissioned = True`. Pass $75\%$ slippage-to-alpha ratio $\implies$ verify `OPTIMIZE_EXECUTION_AND_DATA`. Pass underperforming strategy with healthy peers $\implies$ verify `RECALIBRATE_MODEL_PARAMETERS`.
- Run `python scripts/test_strategy_underperformance_remediation_decision_tree.py`.

## Related Skills

- `strategy-lifecycle-retirement-criteria`
- `strategy-performance-decay-detection-vs-market-wide-decay`
---
