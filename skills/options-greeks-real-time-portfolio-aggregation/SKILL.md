---
name: options-greeks-real-time-portfolio-aggregation
description: >-
  Real-time options portfolio Greeks aggregation engine computing net portfolio Delta, Dollar Delta, Gamma, Theta, and Vega across multi-leg positions and auditing risk exposure limits.
domain: Options Risk Management
subdomain: Real-Time Portfolio Derivatives Risk
tags: ["options-greeks", "portfolio-aggregation", "dollar-delta", "gamma-risk", "theta-decay", "vega-exposure", "derivatives-risk"]
brokers_frameworks: ["Options Risk Analytics Spec", "Python Dataclasses"]
version: "1.0.0"
author: algo-trading-skills-contributors
license: Apache-2.0
---

## When to Use

Use this skill when managing options portfolios or multi-leg derivatives books across equity, index, or crypto markets. Because individual options contracts have nonlinear payoff profiles and sensitivity to spot price, volatility, and time, risk managers must continuously aggregate net portfolio-level Greeks ($\Delta, \Gamma, \Theta, \nu$) in real time. This engine calculates total position Delta, Dollar Delta ($\Delta_{\text{USD}} = \Delta \times S$), Gamma, daily Theta decay, and Vega exposure while auditing against maximum risk limits.

## Prerequisites

- List of option positions (`symbol`, `underlying_symbol`, `position_qty`, `multiplier`, `spot_price`, `delta`, `gamma`, `theta`, `vega`).
- Risk limit configuration (`max_dollar_delta_usd`, `max_negative_theta_usd`, `max_vega_usd`).

## Workflow

1. **Position-Level Greeks Scaling**:
   - For each position $i$ (long $+Q$, short $-Q$):
     $$\Delta_i = Q_i \times M_i \times \text{delta}_i$$
     $$\text{DollarDelta}_i = \Delta_i \times S_i$$
     $$\Gamma_i = Q_i \times M_i \times \text{gamma}_i, \quad \Theta_i = Q_i \times M_i \times \text{theta}_i, \quad \nu_i = Q_i \times M_i \times \text{vega}_i$$
2. **Net Portfolio Aggregation & Grouping**:
   - Compute total portfolio sums:
     $$\Delta_{\text{net}} = \sum \Delta_i, \quad \Delta_{\text{USD, net}} = \sum \text{DollarDelta}_i, \quad \Gamma_{\text{net}} = \sum \Gamma_i, \quad \Theta_{\text{net}} = \sum \Theta_i, \quad \nu_{\text{net}} = \sum \nu_i$$
   - Group net exposures by underlying asset symbol.
3. **Risk Limit Compliance Audit**:
   - Verify $|\Delta_{\text{USD, net}}| \le \text{MaxDollarDelta}$.
   - Verify $|\Theta_{\text{net}}| \le \text{MaxNegativeTheta}$ and $|\nu_{\text{net}}| \le \text{MaxVega}$.
4. **Audit Report Generation**: Output structured `PortfolioGreeksReport`.

> Full procedure: see `references/workflows.md`.
> Standards reference: see `references/standards.md`.
> Printable pre-flight checklist: see `assets/checklist.md`.

## Common Pitfalls

- **Omitting Contract Multipliers**: Adding raw per-contract Delta without multiplying by contract lot size ($M = 100$ for US equities).
- **Ignoring Sign Conventions**: Treating short option positions as positive Greeks instead of multiplying by negative position quantity ($-Q$).
- **Evaluating Raw Delta Across Different Spot Prices**: Summing raw share Delta across different underlying stocks instead of standardizing via Dollar Delta ($\Delta \times S$).

## Verification

- Instantiate `OptionsGreeksRealTimePortfolioAggregationEngine`. Input 10 long Calls ($Q=+10, M=100, S=\$100, \Delta=0.50, \Gamma=0.02, \Theta=-0.05, \nu=0.10$) $\implies$ verify Net Delta $= +500$ shares, Dollar Delta $= \$50,000$, Net Gamma $= 20$, Daily Theta $= -\$50$, Net Vega $= \$100$. Input position breaching Dollar Delta limit $\implies$ verify status `DOLLAR_DELTA_BREACH`.
- Run `python scripts/test_options_greeks_real_time_portfolio_aggregation.py`.

## Related Skills

- `options-backtesting-with-realistic-iv-surface`
- `options-chain-data-normalization-across-vendors`
---
