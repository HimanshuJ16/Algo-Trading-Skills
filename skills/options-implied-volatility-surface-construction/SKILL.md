---
name: options-implied-volatility-surface-construction
description: >-
  Options implied volatility surface construction engine interpolating parametric volatility smiles (SVI / quadratic smile), inverting Black-Scholes market prices, and auditing calendar and butterfly arbitrage violations.
domain: Quantitative Finance & Derivatives Modeling
subdomain: Implied Volatility Surface Calibration & Arbitrage Verification
tags: ["iv-surface", "volatility-smile", "svi-model", "black-scholes", "arbitrage-free", "options-pricing", "derivatives"]
brokers_frameworks: ["Black-Scholes Model", "SciPy / Math", "Python Dataclasses"]
version: "1.0.0"
author: algo-trading-skills-contributors
license: Apache-2.0
---

## When to Use

Use this skill when calibrating an Implied Volatility (IV) surface $\sigma(K, T)$ from discrete exchange-traded option quotes for pricing, backtesting, or delta hedging. Naive linear interpolation or unconstrained surface fitting creates static arbitrage vulnerabilities (calendar spread arbitrage where total variance decreases over time, or butterfly arbitrage where strike pricing creates negative probability density). This engine fits parametric volatility smiles ($\sigma(m) = \sigma_{\text{ATM}} + \alpha(m-1.0) + \beta(m-1.0)^2$), inverts Black-Scholes prices, and audits no-arbitrage constraints.

## Prerequisites

- Option market quote array (`strike`, `tte_years`, `market_price`, `option_type`).
- Surface parameters (`spot_price`, `risk_free_rate`, `atm_vol`, `skew_alpha`, `smile_beta`).

## Workflow

1. **Black-Scholes IV Inversion**:
   - Invert Black-Scholes pricing formula $C_{\text{BS}}(\sigma) = P_{\text{market}}$ via Newton-Raphson or root-finding.
2. **Parametric Volatility Smile Fitting**:
   - Fit strike moneyness $m = K/S$ to quadratic smile / SVI model:
     $$\sigma(m, \tau) = \sigma_{\text{ATM}} + \alpha (m - 1.0) + \beta (m - 1.0)^2$$
3. **No-Arbitrage Verification**:
   - **Calendar Spread Arbitrage**: Verify total implied variance $w(k, \tau) = \sigma^2 \tau$ is non-decreasing over expiration ($\frac{\partial w}{\partial \tau} \ge 0$).
   - **Butterfly Arbitrage**: Verify second derivative / strike convexity is positive ($\frac{\partial^2 C}{\partial K^2} \ge 0$).
4. **Audit Report Generation**: Output structured `IVSurfaceConstructionReport`.

> Full procedure: see `references/workflows.md`.
> Standards reference: see `references/standards.md`.
> Printable pre-flight checklist: see `assets/checklist.md`.

## Common Pitfalls

- **Ignoring Calendar Arbitrage**: Allowing total implied variance for longer term-to-expiration to fall below short term-to-expiration ($w(T_2) < w(T_1)$).
- **Extrapolating Deep OTM Wings Without Bounds**: Permitting negative IVs or unconstrained exploding IVs for extreme strikes ($K/S < 0.5$ or $K/S > 1.5$).
- **Failing to Audit Butterfly Convexity**: Creating non-convex option pricing curves that generate negative risk-neutral probability density.

## Verification

- Instantiate `OptionsIVSurfaceConstructionEngine`. Evaluate strike IV for ATM ($m=1.0$), OTM Put ($m=0.90$), and OTM Call ($m=1.10$). Audit calendar variance monotonicity across $\tau_1=0.25$ and $\tau_2=0.50$ $\implies$ verify `ARBITRAGE_FREE_SURFACE` status.
- Run `python scripts/test_options_implied_volatility_surface.py`.

## Related Skills

- `options-backtesting-with-realistic-iv-surface`
- `options-chain-data-normalization-across-vendors`
---
