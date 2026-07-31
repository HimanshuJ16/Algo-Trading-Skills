---
name: quanto-options-and-cross-currency-derivative-structures
description: >-
  Quantitative quanto option pricing engine adjusting Black-Scholes drift for cross-currency asset-FX correlation risk, quanto Delta, and FX correlation sensitivity.
domain: Derivatives & Cross-Currency Structuring
subdomain: Exotic Derivatives & FX Risk Engineering
tags: ["quanto-options", "cross-currency", "black-scholes", "correlation-drift", "quanto-delta", "derivatives-pricing"]
brokers_frameworks: ["Black-Scholes Quanto Model", "Python Dataclasses", "Math Normal Distribution"]
version: "1.0.0"
author: algo-trading-skills-contributors
license: Apache-2.0
---

## When to Use

Use this skill when pricing and risk-managing cross-currency derivative structures where an underlying asset is denominated in a foreign currency (e.g. Nikkei 225 in JPY, Bitcoin in USD) but settled in a domestic currency (e.g. USD, EUR) at a fixed exchange rate. Because foreign asset price movements are correlated with exchange rate shifts, standard Black-Scholes models misprice quanto options. This engine incorporates the Black-Scholes quanto drift adjustment ($r_{\text{quanto}} = r_f - q - \rho \sigma_S \sigma_X$), pricing European quanto calls/puts and computing quanto-specific Greeks.

## Prerequisites

- Input market data (`spot_price`, `strike_price`, `time_to_expiry_years`, `domestic_rate`, `foreign_rate`, `dividend_yield`, `asset_volatility`, `fx_volatility`, `correlation`, `fixed_fx_rate`, `option_type`).

## Workflow

1. **Quanto Drift Adjustment**:
   - Compute effective dividend yield adjustment: $q_{\text{eff}} = q + \rho \cdot \sigma_S \cdot \sigma_X$.
   - Compute quanto drift: $r_{\text{quanto}} = r_f - q_{\text{eff}}$.
2. **Black-Scholes Quanto $d_1, d_2$ Calculation**:
   - Compute $d_1 = \frac{\ln(S / K) + \left(r_{\text{quanto}} + \frac{1}{2} \sigma_S^2\right) T}{\sigma_S \sqrt{T}}$.
   - Compute $d_2 = d_1 - \sigma_S \sqrt{T}$.
3. **Quanto Option Pricing & Greeks**:
   - Call: $V_{\text{call}} = F_X \cdot e^{-r_d T} \cdot \left[ S e^{r_{\text{quanto}} T} N(d_1) - K N(d_2) \right]$.
   - Put: $V_{\text{put}} = F_X \cdot e^{-r_d T} \cdot \left[ K N(-d_2) - S e^{r_{\text{quanto}} T} N(-d_1) \right]$.
   - Compute Quanto Delta, Gamma, Vega, and FX Correlation Sensitivity ($\frac{\partial V}{\partial \rho}$).
4. **Audit Report Generation**: Output structured `QuantoOptionPricingReport`.

> Full procedure: see `references/workflows.md`.
> Standards reference: see `references/standards.md`.
> Printable pre-flight checklist: see `assets/checklist.md`.

## Common Pitfalls

- **Ignoring Asset-FX Correlation**: Omitting $\rho \cdot \sigma_S \cdot \sigma_X$ drift adjustment, severely mispricing quanto options during volatile FX regimes.
- **Wrong Interest Rate Pairing**: Confusing domestic risk-free rate $r_d$ (used for discounting option payoff) with foreign risk-free rate $r_f$ (used for foreign asset drift).
- **Correlation Instability**: Assuming constant correlation $\rho$, ignoring correlation regime shifts during market stress.

## Verification

- Instantiate `QuantoOptionPricer`. Price European Call ($S=\$100, K=\$100, T=1.0\text{ yr}, r_d=5\%, r_f=2\%, \sigma_S=20\%, \sigma_X=15\%, \rho=0.30, F_X=1.0$) $\implies$ verify $r_{\text{quanto}} = 0.011$ ($1.1\%$), $d_1 \approx 0.155$, call price $> 0$, and valid FX correlation sensitivity.
- Run `python scripts/test_quanto_options_and_cross_currency_derivative_structures.py`.

## Related Skills

- `options-implied-volatility-surface-construction`
- `currency-pair-quoting-convention-normalization`
---
