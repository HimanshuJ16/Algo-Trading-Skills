---
name: capital-efficiency-across-cross-margined-strategies
description: >-
  Quantitative margin optimization engine to calculate capital efficiency gains using Portfolio/Cross-Margining based on asset correlation and directional delta.
domain: Risk Management
subdomain: Margin & Capital
tags: ["cross-margin", "portfolio-margin", "capital-efficiency", "correlation", "risk"]
brokers_frameworks: ["Generic Risk Engineering"]
version: "1.0.0"
author: algo-trading-skills-contributors
license: Apache-2.0
---

## When to Use

Use this skill when managing a multi-strategy quantitative portfolio where capital is constrained. By transitioning from Isolated Margin (where each position requires full standalone collateral) to Cross/Portfolio Margin, the broker recognizes offsetting risks (e.g., long BTC and short ETH). This engine calculates the mathematical "Margin Offset" based on the correlation matrix of the underlying assets.

## Prerequisites

- Portfolio position data including Delta (directional exposure).
- A robust, historical correlation matrix for the traded universe.
- A broker that explicitly supports Portfolio Margining (e.g., Interactive Brokers, Bybit Unified Trading Account, Delta Exchange).

## Workflow

1. **Portfolio Definition**: Load current portfolio positions (Asset, Delta, Base Margin Requirement).
2. **Correlation Matrix**: Load the pairwise correlation matrix for the assets.
3. **Isolated Calculation**: Calculate the sum of absolute base margins (Isolated Margin).
4. **Cross-Margin Calculation**: Use the `CrossMarginOptimizer` to calculate the portfolio margin by reducing the base margin requirements proportionally to the correlation between opposing directional positions.
5. **Efficiency Reporting**: Output the Capital Efficiency Ratio (CER). A ratio of > 1.0 indicates capital is freed up for deployment elsewhere.

> Full procedure: see `references/workflows.md`.
> Standards reference: see `references/standards.md`.
> Printable pre-flight checklist: see `assets/checklist.md`.

## Common Pitfalls

- **Assuming 1.0 Correlation**: Believing that two highly correlated assets (e.g., BTC and ETH) will *always* maintain a 0.95 correlation during flash crashes. Margin engines apply haircuts to correlations.
- **Over-leveraging**: Using the freed-up capital from cross-margining to double down on risk, leading to cascading liquidations if the correlation breaks.
- **Cross-Exchange Illusion**: You cannot cross-margin a long position on Binance with a short position on Bybit; cross-margining only works within the same clearinghouse/broker.

## Verification

- Simulate a portfolio with a $100k Long in Asset A and a $100k Short in Asset B, where A and B have a 0.90 correlation. The isolated margin is $20k, but the cross-margin should be significantly lower (e.g., ~$2k-$5k).
- Run `python scripts/test_capital_efficiency_across_cross_margined_strategies.py`.

## Related Skills

- `broker-margin-interest-accrual-tracking`
- `broker-account-margin-call-handling`
