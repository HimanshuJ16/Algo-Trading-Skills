---
name: risk-parity-allocation-across-strategies
description: >-
  Production-grade risk parity portfolio allocation engine computing Equal Risk Contribution (ERC) and inverse-volatility weights across trading strategies to ensure equal risk contribution across strategy components.
domain: Portfolio & Risk Management
subdomain: Risk Parity & Capital Allocation
tags: ["risk-parity", "equal-risk-contribution", "erc", "inverse-volatility", "portfolio-allocation", "capital-scaling"]
brokers_frameworks: ["Risk Parity / Equal Risk Contribution (ERC)", "Covariance Matrix Risk Decomposition", "Python Dataclasses"]
version: "1.0.0"
author: algo-trading-skills-contributors
license: Apache-2.0
---

## When to Use

Use this skill when allocating capital across a multi-strategy quantitative portfolio (e.g., trend following, statistical arbitrage, mean reversion, market making). Traditional capital-weighted allocation (e.g., equal capital weighting) results in high-volatility strategies dominating total portfolio risk, causing unhedged drawdowns. Risk Parity allocates capital inversely proportional to strategy volatility ($w_i \propto 1/\sigma_i$), ensuring each strategy contributes an equal percentage to total portfolio risk.

## Prerequisites

- Strategy risk specifications (`StrategyRiskData`: `strategy_id`, `annualized_volatility`, `daily_returns`).
- Total portfolio capital ($; default $1,000,000).
- Optional strategy covariance matrix $\Sigma$.

## Workflow

1. **Inverse Volatility Weighting**:
   - Compute raw inverse-volatility weights: $w_i = \frac{1/\sigma_i}{\sum_j (1/\sigma_j)}$.
2. **Portfolio Volatility & Risk Decomposition**:
   - Compute portfolio volatility $\sigma_p = \sqrt{w^T \Sigma w}$.
   - Compute Marginal Contribution to Risk (MCR): $\text{MCR}_i = \frac{(\Sigma w)_i}{\sigma_p}$.
   - Compute Risk Contribution %: $\text{RC}_i = \frac{w_i \times \text{MCR}_i}{\sigma_p} \times 100\%$.
3. **Target Share & Risk Balance Audit**:
   - Compare actual risk contribution % against equal share target ($\frac{100\%}{N}$). Assert risk parity error $\le 5.0\%$.
4. **Capital Allocation Output**: Output structured `RiskParityReport`.

> Full procedure: see `references/workflows.md`.
> Standards reference: see `references/standards.md`.
> Printable pre-flight checklist: see `assets/checklist.md`.

## Common Pitfalls

- **Confusing Capital Parity with Risk Parity**: Allocating 50% capital to a 30% vol strategy and 50% capital to a 10% vol strategy gives 90% of total risk to the first strategy.
- **Ignoring Cross-Strategy Covariance**: Assuming zero correlation between strategies when correlations spike during market stress events.
- **Over-Leveraging Low-Vol Strategies**: Unchecked leverage scaling on ultra-low-vol strategies can trigger severe tail loss.

## Verification

- Instantiate `RiskParityAllocationEngine`. Allocate across 3 strategies with vols 10%, 20%, 30% $\implies$ verify low-risk strategy receives ~54.5% capital weight, med-risk ~27.3%, high-risk ~18.2%, yielding equal 33.3% risk contribution. Pass full covariance matrix $\implies$ verify MCR decomposition.
- Run `python scripts/test_risk_parity_allocation_across_strategies.py`.

## Related Skills

- `risk-budget-allocation-across-time-horizons`
- `risk-adjusted-performance-attribution-per-strategy`
---
