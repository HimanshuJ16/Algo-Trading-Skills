---
name: strategy-performance-decay-detection-vs-market-wide-decay
description: >-
  Production-grade performance decay diagnostic engine classifying strategy underperformance into Idiosyncratic Alpha Decay vs Systematic Market-Wide Regime Shift using peer benchmark attribution and relative Sharpe Z-score monitoring.
domain: Investment Governance & Portfolio Analytics
subdomain: Performance Decay Diagnostics
tags: ["performance-decay", "alpha-decay", "regime-shift", "peer-benchmark", "z-score-attribution", "strategy-diagnostics"]
brokers_frameworks: ["Peer Benchmark Framework", "Rolling Z-Score Analytics", "Python Dataclasses", "pandas", "numpy"]
version: "1.0.0"
author: algo-trading-skills-contributors
license: Apache-2.0
---

## When to Use

Use this skill when diagnosing underperformance in a quantitative trading strategy. A drop in realized Sharpe ratio can stem from two completely different causes: (1) **Idiosyncratic Alpha Decay** (the strategy's edge has been arbitraged away or crowded), requiring strategy decommissioning; or (2) **Market-Wide Regime Shift** (the entire asset class or strategy peer group is experiencing a low-volatility chop), requiring temporary capital reduction or risk pausing. This engine compares target strategy returns against a peer benchmark index, computes relative Sharpe Z-scores, and outputs actionable diagnostics (`IDIOSYNCRATIC_ALPHA_DECAY` vs `MARKET_WIDE_REGIME_SHIFT`).

## Prerequisites

- Strategy daily return series (`strategy_returns`).
- Peer benchmark index return series (`peer_returns`, e.g. Trend Following Peer Index, Stat Arb Peer Index).
- Rolling evaluation window (default 60 days) and Z-score threshold (default $-1.96$).

## Workflow

1. **Rolling Sharpe Ratio Calculation**:
   - Compute rolling annualized Sharpe ratio for target strategy ($\text{Sharpe}_{\text{target}}$) and peer benchmark ($\text{Sharpe}_{\text{peer}}$).
2. **Relative Excess Sharpe & Z-Score Computation**:
   - Calculate relative Sharpe difference: $\Delta \text{Sharpe} = \text{Sharpe}_{\text{target}} - \text{Sharpe}_{\text{peer}}$.
   - Compute rolling Z-score: $Z = \frac{\Delta \text{Sharpe} - \mu_{\Delta}}{\sigma_{\Delta}}$.
3. **Decay Cause Classification**:
   - $Z \le -1.96$ and $\text{Sharpe}_{\text{peer}} \ge 0.50 \implies$ `IDIOSYNCRATIC_ALPHA_DECAY`.
   - $\text{Sharpe}_{\text{target}} < 0.50$ and $\text{Sharpe}_{\text{peer}} < 0.50 \implies$ `MARKET_WIDE_REGIME_SHIFT`.
   - $\text{Sharpe}_{\text{target}} \ge 0.50 \implies$ `HEALTHY`.
4. **Execution Output**: Output structured `StrategyDecayDiagnosticsReport`.

> Full procedure: see `references/workflows.md`.
> Standards reference: see `references/standards.md`.
> Printable pre-flight checklist: see `assets/checklist.md`.

## Common Pitfalls

- **Confusing Idiosyncratic Decay with Regime Shift**: Decommissioning a robust strategy during a temporary market-wide regime shift when peer strategies are also underperforming.
- **Evaluating Performance in Isolation**: Assessing strategy drawdown without comparing against a peer benchmark index.
- **Ignoring Statistical Significance**: Retaining a decaying strategy whose relative performance Z-score has breached $-1.96$ ($95\%$ confidence underperformance vs peers).

## Verification

- Instantiate `StrategyPerformanceDecayDiagnosticEngine`. Pass strategy experiencing alpha decay while peer index remains healthy $\implies$ verify `classification = IDIOSYNCRATIC_ALPHA_DECAY`, $Z < -1.96$, and recommended action `DECOMMISSION_OR_RECODE`. Pass scenario where strategy and peers decay simultaneously $\implies$ verify `classification = MARKET_WIDE_REGIME_SHIFT` and action `PAUSE_OR_REDUCE_RISK`.
- Run `python scripts/test_strategy_performance_decay_detection_vs_market_wide_decay.py`.

## Related Skills

- `strategy-lifecycle-retirement-criteria`
- `benchmark-portfolio-for-multi-strategy-performance-context`
---
