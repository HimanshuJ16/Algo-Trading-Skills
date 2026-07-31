---
name: multi-currency-var-aggregation
description: >-
  Multi-currency Value at Risk (VaR) and Expected Shortfall (CVaR) aggregation engine, accounting for joint asset-FX return covariance and per-currency risk decomposition.
domain: Risk Management Global
subdomain: Multi-Currency Portfolio Risk & VaR Aggregation
tags: ["multi-currency", "value-at-risk", "var", "expected-shortfall", "cvar", "covariance-matrix", "fx-risk", "parametric-var"]
brokers_frameworks: ["Variance-Covariance VaR", "Historical Simulation VaR", "Python Dataclasses"]
version: "1.0.0"
author: algo-trading-skills-contributors
license: Apache-2.0
---

## When to Use

Use this skill when measuring and aggregating portfolio risk across international assets denominated in different fiat currencies (e.g., US Equities in `USD`, European Equities in `EUR`, Japanese Equities in `JPY`). Total portfolio risk cannot be measured by simply converting position values to a base currency — Value at Risk (VaR) must account for **Asset Return Volatility**, **FX Spot Volatility**, and the **Joint Asset-FX Return Covariance Matrix ($\boldsymbol{\Sigma}$)**. This module implements Parametric (Variance-Covariance) VaR, Historical Simulation VaR, Expected Shortfall (CVaR), and per-currency Component VaR.

## Prerequisites

- Multi-currency position payload (`symbol`, `native_currency`, `quantity`, `current_price_native`, `fx_rate_to_base`).
- Historical returns dataset (`symbol_returns`: dict of lists, `fx_returns`: dict of lists).
- Risk configuration (`confidence_level`: e.g. 0.95 or 0.99, `holding_period_days`: 1, `base_currency`: `'USD'`).

## Workflow

1. **Base Currency Position Valuation**:
   - Compute position market value in base reporting currency:
     $$V_{\text{base}, i} = Q_i \cdot P_{\text{native}, i} \cdot E(c_i \rightarrow \text{base})$$
2. **Joint Base Return Series Synthesis**:
   - For each historical period $t$, synthesize base-currency asset return:
     $$R_{\text{base}, i, t} = (1 + R_{\text{native}, i, t})(1 + R_{\text{FX}, c_i, t}) - 1$$
3. **Parametric & Historical VaR Calculation**:
   - **Historical VaR**: Compute empirical $\alpha$-quantile of portfolio PnL losses.
   - **Parametric VaR**: Compute base return covariance matrix $\boldsymbol{\Sigma}$, portfolio volatility $\sigma_p = \sqrt{\mathbf{w}^T \boldsymbol{\Sigma} \mathbf{w}}$, and $\text{VaR}_{\alpha} = Z_{\alpha} \cdot \sigma_p \cdot V_{\text{total}}$.
   - **Expected Shortfall (CVaR)**: Calculate tail conditional expectation $E[\text{Loss} \mid \text{Loss} \ge \text{VaR}_{\alpha}]$.
4. **Audit Report Generation**: Output structured `MultiCurrencyVarReport`.

> Full procedure: see `references/workflows.md`.
> Standards reference: see `references/standards.md`.
> Printable pre-flight checklist: see `assets/checklist.md`.

## Common Pitfalls

- **Ignoring FX Volatility**: Assuming FX rates are constant when computing asset VaR, ignoring currency fluctuations that can amplify or hedge equity drawdowns.
- **Ignoring Asset-FX Cross-Correlation**: Omitting $\rho(\text{Asset}_i, \text{FX}_c)$ correlations, leading to mis-estimated risk during market crises.
- **Assuming Normal Distributions for FX**: FX returns often exhibit heavy-tail kurtosis ($\kappa > 3.0$); relying solely on Parametric VaR can underestimate 99% VaR.

## Verification

- Instantiate `MultiCurrencyVarAggregatorEngine`. Audit portfolio with $100k USD equity and €100k EUR equity (EUR/USD = 1.10) $\implies$ verify base returns synthesis, 95% Parametric & Historical VaR calculation, and Expected Shortfall.
- Run `python scripts/test_multi_currency_var_aggregator.py`.

## Related Skills

- `multi-asset-backtest-currency-normalization`
- `multi-currency-pnl-and-fx-conversion`
---
