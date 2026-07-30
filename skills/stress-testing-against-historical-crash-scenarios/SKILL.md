---
name: stress-testing-against-historical-crash-scenarios
description: Use when replaying a live portfolio's current positions against historical
  crash scenarios (2020 COVID crash, 2015 flash crash, 2008 GFC, etc.) to quantify
  tail-risk P&L impact and validate that drawdown circuit breakers would trigger before
  catastrophic loss.
domain: algorithmic-trading
subdomain: risk-management
tags:
- risk-management
- stress-testing
- crash-scenarios
- tail-risk
- drawdown-analysis
brokers_frameworks:
- NumPy
- Pandas
- Custom Risk Engine
version: '1.0'
author: algo-trading-skills-contributors
license: Apache-2.0
---

## When to Use

Invoke this skill whenever a live portfolio carries open positions that could suffer extreme losses
during a tail-risk event. Static VaR calculations underestimate losses in regime-break scenarios;
stress testing replays the portfolio's **current** position vector through actual historical crash
return distributions to estimate worst-case P&L impact. This is essential for:
- Pre-trade risk approval gates (blocking new positions if stressed loss exceeds threshold).
- Periodic (daily / weekly) portfolio risk reports to risk committees.
- Regulatory stress-testing requirements (Basel III, SEC, ESMA).

## Prerequisites

- A library of historical crash scenario return vectors (multi-day cumulative or peak-to-trough).
- Current portfolio position vector and live prices.
- Maximum acceptable stressed-loss threshold (e.g. 15% of NAV).

## Workflow

1. **Define Crash Scenario Library**:
   - Each scenario contains a name, date range, and per-asset return shocks (e.g. SPY: -34%, QQQ: -28%).

2. **Replay Current Positions Through Each Scenario**:
   - For each scenario $s$, compute stressed P&L:
     $$\Delta \text{NAV}_s = \sum_{i} w_i \cdot R_{i,s} \cdot \text{NAV}$$
   - Where $w_i$ is the current portfolio weight and $R_{i,s}$ is the scenario return for asset $i$.

3. **Identify Worst-Case Scenario**:
   - Find $\arg\min_s \Delta \text{NAV}_s$ to identify the scenario producing the largest drawdown.

4. **Enforce Stress-Test Gate**:
   - If worst-case stressed loss exceeds the threshold, block new position entries and raise alert.

> Full step-by-step procedure: see `references/workflows.md`.
> Standards reference: see `references/standards.md`.
> Printable checklist: see `assets/checklist.md`.

## Common Pitfalls

- **Survivorship Bias in Scenarios**: Using only assets that survived the crash — delisted/bankrupted assets had -100% returns.
- **Ignoring Correlation Spikes**: During crashes, cross-asset correlations spike toward 1.0 — diversification benefits evaporate.
- **Static Scenario Assumption**: Real crashes unfold over days/weeks with margin calls and forced liquidations amplifying losses.

## Verification

- Replay a 2-asset portfolio through a synthetic crash scenario and verify stressed P&L matches expected calculation.
- Confirm that a portfolio exceeding the stressed-loss threshold is correctly flagged and blocked.
- Run `python scripts/test_stress_tester.py` and confirm 100% pass rate.

## Related Skills

- `value-at-risk-var-live-monitoring`
- `kill-switch-and-drawdown-circuit-breakers`
- `correlation-aware-exposure-limits`
---
