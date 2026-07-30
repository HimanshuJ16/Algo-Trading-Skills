---
name: execution-algorithm-regression-testing-suite
description: >-
  Quantitative CI/CD testing engine for executing automated regression benchmarks (MiFID II RTS 6, SEC 15c3-5) on execution algorithm code updates, comparing Implementation Shortfall, fill rates, and risk bounds.
domain: Execution Algorithms
subdomain: CI/CD Quality Gates & Regulatory Testing
tags: ["regression-testing", "mifid-ii-rts-6", "ci-cd-quality-gate", "execution-algo", "implementation-shortfall", "backtesting-suite", "order-determinism"]
brokers_frameworks: ["MiFID II RTS 6 Standard", "SEC Rule 15c3-5", "Python Dataclasses", "CI/CD Pipeline Gates"]
version: "1.0.0"
author: algo-trading-skills-contributors
license: Apache-2.0
---

## When to Use

Use this skill in algorithmic trading CI/CD pipelines, release engineering, and regulatory compliance. Regulatory technical standards (**MiFID II RTS 6** and **SEC Rule 15c3-5**) require that all code updates to automated execution algorithms (TWAP, VWAP, POV, Implementation Shortfall) undergo rigorous automated regression testing before production deployment. This module runs historical market scenario suites, compares candidate code metrics against baseline production baselines ($\Delta \text{IS} \le +2.0\text{ bps}$, fill rate $\ge 98\%$), and enforces deployment quality gates.

## Prerequisites

- Baseline production algorithm metrics ($\text{IS}_{\text{base}}$, $\text{FillRate}_{\text{base}}$, $\alpha_{\text{max}}$).
- Candidate algorithm code build output.
- Suite of historical market test scenarios (Normal Volatility, Shock Spikes, Liquidity Crunch).

## Workflow

1. **Scenario Backtest Replay**:
   - Replay baseline and candidate algo versions over standard historical market scenarios.
2. **Metric Variance Audit**:
   - Compute $\Delta \text{IS} = \text{IS}_{\text{candidate}} - \text{IS}_{\text{baseline}}$ (in basis points).
   - Compute Fill Rate Ratio = $\text{FillRate}_{\text{candidate}} / \text{FillRate}_{\text{baseline}}$.
   - Audit maximum participation ceiling compliance ($\alpha_{\text{candidate}} \le \alpha_{\text{max\_limit}}$).
3. **CI/CD Quality Gate Evaluation**:
   - If $\Delta \text{IS} > +2.0\text{ bps}$ or Fill Ratio $< 0.98$ or Risk Breach $\implies$ Flag `FAIL_REGRESSION_REJECTED`.
   - Else $\implies$ Flag `PASS_REGRESSION_APPROVED`.
4. **Audit Report Generation**: Output structured `RegressionTestSuiteAuditReport`.

> Full procedure: see `references/workflows.md`.
> Standards reference: see `references/standards.md`.
> Printable pre-flight checklist: see `assets/checklist.md`.

## Common Pitfalls

- **Bypassing RTS 6 Regression Testing for "Hotfixes"**: Deploying emergency patches without running automated regression suites, causing undetected slippage regressions or runaway order loops.
- **Testing Only Under Quiet Market Conditions**: Evaluating candidates only on low-volatility days, missing order queue latency bugs during high-volatility market shocks.
- **Ignoring Fill Completion Regressions**: Focusing solely on low average slippage while failing to detect that the candidate algo drops fill completion rates from 100% to 80%.

## Verification

- Instantiate `ExecutionAlgoRegressionTestSuite`. Define 3 test scenarios (Normal, Volatility Shock, Liquidity Crunch). Run regression test on candidate code version. Test passing candidate ($\Delta \text{IS} = +0.5\text{ bps}$, fill = 99.5%) $\implies$ verify engine outputs `PASS_REGRESSION_APPROVED`. Test regressed candidate ($\Delta \text{IS} = +4.5\text{ bps}$) $\implies$ verify engine flags `FAIL_REGRESSION_REJECTED` and blocks release.
- Run `python scripts/test_execution_algorithm_regression_testing_suite.py`.

## Related Skills

- `execution-algo-parameter-optimization-via-backtest`
- `execution-slippage-attribution-timing-vs-sizing`
---
