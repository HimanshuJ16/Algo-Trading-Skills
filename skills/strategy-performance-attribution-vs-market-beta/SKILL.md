---
name: strategy-performance-attribution-vs-market-beta
description: >-
  Production-grade performance attribution engine performing CAPM and Fama-French multi-factor regressions to decompose strategy returns into systematic Market Beta, Style Risk Premia (SMB, HML), and true idiosyncratic Jensen's Alpha.
domain: Investment Governance & Portfolio Analytics
subdomain: Performance Attribution & Risk Decomposition
tags: ["performance-attribution", "market-beta", "jensens-alpha", "capm", "fama-french", "factor-regression"]
brokers_frameworks: ["CAPM Single-Factor Model", "Fama-French 3-Factor Model", "Python Dataclasses", "pandas", "numpy"]
version: "1.0.0"
author: algo-trading-skills-contributors
license: Apache-2.0
---

## When to Use

Use this skill when evaluating whether quantitative strategy outperformance is driven by genuine active skill (True Idiosyncratic Alpha $\alpha$) or simply unacknowledged factor exposures (Market Beta $\beta_M$, Size SMB, Value HML). Claiming alpha on a strategy that is merely levered to small-cap stocks or market beta misleads investors and misprices risk. This engine runs linear OLS regressions against factor return series, calculates annualized Jensen's Alpha, $t$-statistics, $R^2$, and factor return contributions.

## Prerequisites

- Strategy daily return series (`strategy_returns`).
- Benchmark market return series (`market_returns`).
- Optional Fama-French factor return series (`smb_returns`, `hml_returns`).
- Annual risk-free rate percentage (`risk_free_rate_annual_pct`, default 2.0%).

## Workflow

1. **Excess Return Alignment**:
   - Subtract daily risk-free rate from strategy and factor return series: $y = R_{\text{strat}} - R_f$, $X_{\text{mkt}} = R_{\text{mkt}} - R_f$.
2. **OLS Factor Regression Execution**:
   - Fit regression model: $y = \alpha + \beta_M MKT + \beta_S SMB + \beta_H HML + \epsilon$.
3. **Alpha & Factor Contribution Decomposition**:
   - Compute Annualized Jensen's Alpha ($\alpha \times 252 \times 100\%$) and $t$-statistic.
   - Calculate return contributions per factor: $\text{Contribution}_i = \beta_i \times \text{Return}_{\text{factor}, i}$.
4. **Statistical Significance Audit**:
   - Audit if $|t_{\alpha}| \ge 1.96$ ($95\%$ confidence).
5. **Execution Output**: Output structured `PerformanceAttributionReport`.

> Full procedure: see `references/workflows.md`.
> Standards reference: see `references/standards.md`.
> Printable pre-flight checklist: see `assets/checklist.md`.

## Common Pitfalls

- **Confusing Market Beta with Alpha**: Attributing high returns in a bull market to manager skill when the strategy simply has a high market beta ($\beta_M > 1.2$).
- **Ignoring Style Tilts (SMB/HML)**: Failing to include Fama-French factors, mistaking passive small-cap or value factor exposure for true alpha.
- **Ignoring Statistical Significance ($t$-stat)**: Accepting a high point estimate of alpha without checking if its $t$-statistic is statistically significant ($|t| \ge 1.96$).

## Verification

- Instantiate `StrategyPerformanceAttributionEngine`. Pass synthetic returns ($1.2\times$ MKT $+ 5\%$ annual Alpha) $\implies$ verify $\beta_M \in [1.1, 1.3]$, Jensen's Alpha $> 4.0\%$, and $R^2 > 0.90$. Pass Fama-French SMB tilt $\implies$ verify SMB factor contribution decomposed.
- Run `python scripts/test_strategy_performance_attribution_vs_market_beta.py`.

## Related Skills

- `strategy-performance-decay-detection-vs-market-wide-decay`
- `benchmark-portfolio-for-multi-strategy-performance-context`
---
