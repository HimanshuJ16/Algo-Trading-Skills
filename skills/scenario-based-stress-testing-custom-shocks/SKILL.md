---
name: scenario-based-stress-testing-custom-shocks
description: >-
  Production-grade scenario-based stress testing engine running historical crisis replays (2008 Lehman, 2020 Covid, 2022 Rate Hikes), custom multi-factor shocks, and drawdown limit breach analysis across multi-asset quantitative portfolios.
domain: Risk Management & Portfolio Governance
subdomain: Stress Testing & Scenario Analysis
tags: ["stress-testing", "custom-shocks", "historical-crises", "lehman-2008", "covid-2020", "drawdown-breach"]
brokers_frameworks: ["Scenario Stress Testing Engine", "Python Dataclasses", "Risk Management Framework"]
version: "1.0.0"
author: algo-trading-skills-contributors
license: Apache-2.0
---

## When to Use

Use this skill when evaluating portfolio resilience under extreme, deterministic "what-if" market conditions. Standard statistical risk measures (VaR, Expected Shortfall) assume normal or fat-tailed market distributions calibrated to recent historical windows, failing to capture regime shifts or black swan crashes. Scenario-based stress testing applies deterministic factor shocks (e.g. 2008 Financial Crisis: Equity -35%, Vol +150%; 2020 Covid Crash: Equity -30%, Oil -60%) to quantify drawdown depth and detect limit breaches.

## Prerequisites

- Portfolio position inventory (`AssetPosition`: `asset_id`, `factor_name`, `current_value_usd`, `beta_to_factor`).
- Max allowed portfolio drawdown limit % (default 20.0%).
- Predefined historical crisis definitions or custom factor shocks (`FactorShock`).

## Workflow

1. **Portfolio Position & Factor Sensitivity Mapping**:
   - Map asset positions to risk factors (`EQUITY_SPOT`, `IMPLIED_VOL`, `INTEREST_RATE_BPS`, `CRUDE_OIL`) and factor betas.
2. **Scenario Execution**:
   - Apply percentage shocks ($S_{\text{sim}} = V_{\text{usd}} \times \Delta_{\text{factor}} \times \beta$) or interest rate basis point shifts.
3. **Loss & Drawdown Limit Audit**:
   - Calculate simulated P&L ($) and percentage loss (%); flag scenario if loss exceeds max allowed drawdown.
4. **Stress Report Generation**: Output structured `StressTestReport`.

> Full procedure: see `references/workflows.md`.
> Standards reference: see `references/standards.md`.
> Printable pre-flight checklist: see `assets/checklist.md`.

## Common Pitfalls

- **Relying Solely on VaR Without Stress Testing**: Value-at-Risk fails to quantify loss severity beyond the 99% quantile; stress testing models the full loss tail.
- **Uncorrelated Shock Assumptions in Crises**: Assuming asset diversification holds during severe liquidity crunches when asset correlations spike to 1.0.
- **Ignoring Second-Order Option Sensitivity**: Applying spot shocks to options positions without recalculating Gamma and Vega gains/losses.

## Verification

- Instantiate `CustomScenarioStressTester`. Run predefined historical stress tests on $1,000,000 equity portfolio $\implies$ verify 2008 Lehman scenario generates -$350,000 P&L (-35%) and flags drawdown limit breach. Run custom 15% tech crash scenario $\implies$ verify -$150,000 P&L (-15%) with no breach flag.
- Run `python scripts/test_custom_scenario_stress_tester.py`.

## Related Skills

- `risk-model-backtesting-against-realized-outcomes`
- `risk-limit-calibration-against-historical-drawdowns`
---
