---
name: portfolio-stress-test-including-liquidity-crunch-scenarios
description: >-
  Portfolio stress testing engine simulating macro price shocks combined with liquidity crunch scenarios, calculating Days-to-Liquidate (DTL) and liquidity slippage haircuts.
domain: Risk Management & Stress Testing
subdomain: Liquidity Risk & Crisis Stress Simulation
tags: ["stress-testing", "liquidity-crunch", "days-to-liquidate", "market-shock", "risk-management", "var", "slippage-haircut"]
brokers_frameworks: ["ESMA / SEC Liquidity Risk Framework", "Python Dataclasses"]
version: "1.0.0"
author: algo-trading-skills-contributors
license: Apache-2.0
---

## When to Use

Use this skill when evaluating portfolio resilience during crisis regimes (e.g., 2008 GFC, March 2020 COVID crash, 2022 crypto liquidity crunch). Standard Value-at-Risk (VaR) models assume continuous market liquidity. In a severe crisis, market volume drops ($50-80\%$), spreads widen ($5-10\times$), and Days-to-Liquidate ($DTL$) expand dramatically. This engine stress-tests portfolios by coupling price shocks with volume haircuts to compute liquidity-adjusted stress losses ($L\text{-StressedPnL}$).

## Prerequisites

- Portfolio position specs (`symbol`, `quantity`, `current_price`, `adv_shares`, `spread_bps`).
- Stress scenario definition (`scenario_name`, `price_shock_pct`, `liquidity_drop_pct`: default 0.50, `spread_expansion_factor`: default 5.0).
- Liquidity risk config (`max_allowed_dtl_days`: default 5.0).

## Workflow

1. **Stressed Liquidity & DTL Calculation**:
   - Compute Stressed ADV $= \text{ADV} \cdot (1 - \text{LiquidityDropPct})$.
   - Compute Daily Execution Cap $= 0.10 \cdot \text{StressedADV}$.
   - Compute Days-to-Liquidate:
     $$DTL_i = \frac{|Q_i|}{\text{DailyExecutionCap}_i}$$
2. **Price Shock & Liquidity Haircut Evaluation**:
   - Compute Price Shock Loss $= \sum (Q_i \cdot P_i \cdot \Delta P_i)$.
   - Compute Liquidity Slippage Haircut $= \sum \left( |Q_i| \cdot P_i \cdot \frac{\text{SpreadBps} \cdot \text{SpreadExpansion}}{10000} \cdot DTL_i \right)$.
   - Total Stressed Loss $= \text{PriceShockLoss} + \text{LiquiditySlippageHaircut}$.
3. **Liquidity Bottleneck Audit**:
   - Flag assets where $DTL_i > \text{MaxAllowedDTLDays}$ (illiquid bottleneck).
4. **Audit Report Generation**: Output structured `StressTestReport`.

> Full procedure: see `references/workflows.md`.
> Standards reference: see `references/standards.md`.
> Printable pre-flight checklist: see `assets/checklist.md`.

## Common Pitfalls

- **Ignoring Liquidity Haircuts**: Evaluating price shocks alone without modeling volume drying up, underestimating true liquidation loss.
- **Assuming Constant Participation**: Assuming $100\%$ of ADV can be liquidated in a single day during a panic fire sale.
- **Uncoordinated Stress Scenarios**: Applying price shocks to equities while assuming fixed income/FX liquidity remains pristine.

## Verification

- Instantiate `PortfolioStressTestEngine`. Input portfolio holding $100,000$ shares of illiquid stock ($\text{ADV} = 50,000$). Apply scenario with $20\%$ price crash, $50\%$ liquidity drop, $5\times$ spread expansion $\implies$ verify $DTL = 40.0$ days, flagging `LIQUIDITY_CRUNCH_ILLIQUID_WARNING` and computing liquidity slippage haircut.
- Run `python scripts/test_portfolio_stress_test_including_liquidity_crunch_scenarios.py`.

## Related Skills

- `liquidity-adjusted-position-sizing`
- `portfolio-level-stop-loss-independent-of-strategy-stops`
---
