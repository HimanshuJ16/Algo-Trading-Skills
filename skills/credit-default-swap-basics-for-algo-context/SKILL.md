---
name: credit-default-swap-basics-for-algo-context
description: >-
  Quantitative credit trading module for calculating Credit Default Swap (CDS) hazard rates, implied default probabilities, ISDA standard upfront payments, RPV01, and equity-CDS cross-asset signals.
domain: Derivatives & Fixed Income
subdomain: Credit Derivatives
tags: ["cds", "credit-default-swap", "hazard-rate", "default-probability", "isda-upfront", "rpv01", "cross-asset"]
brokers_frameworks: ["ISDA Standard Model", "NumPy"]
version: "1.0.0"
author: algo-trading-skills-contributors
license: Apache-2.0
---

## When to Use

Use this skill when integrating Credit Default Swap (CDS) metrics into quantitative trading algorithms, credit risk models, or cross-asset capital structure arbitrage strategies (CDS vs. Equity). CDS contracts trade on standardized fixed coupons (100 bps for Investment Grade, 500 bps for High Yield) with an upfront cash settlement. This module calculates the hazard rate ($\lambda$), cumulative default probability ($PD$), Risky PV01 ($RPV01$), and ISDA standard upfront payments.

## Prerequisites

- CDS Par Spread $s_{par}$ (in bps or decimal) and Standard Coupon $s_{coupon}$ (100 or 500 bps).
- Maturity $T$ (years), Risk-Free Rate $r$, and Recovery Rate $R$ (default 40% = 0.40).

## Workflow

1. **Hazard Rate Estimation (Credit Triangle)**:
   - Hazard rate $\lambda = \frac{s_{par}}{1 - R}$.
2. **Default & Survival Probability**:
   - Survival Probability $S(T) = e^{-\lambda T}$.
   - Cumulative Default Probability $PD(T) = 1 - S(T) = 1 - e^{-\lambda T}$.
3. **Risky PV01 (RPV01) Calculation**:
   - Continuous annuity factor: $RPV01 = \frac{1 - e^{-(r + \lambda) T}}{r + \lambda}$.
4. **ISDA Upfront Payment**:
   - $\text{Upfront} = \text{Notional} \times RPV01 \times (s_{par} - s_{coupon})$.
5. **Cross-Asset Capital Structure Signal**:
   - Spiking CDS spreads ($s_{par} > s_{historical\_mean} + 2\sigma$) signal credit distress, generating short equity / long CDS signals.

> Full procedure: see `references/workflows.md`.
> Standards reference: see `references/standards.md`.
> Printable pre-flight checklist: see `assets/checklist.md`.

## Common Pitfalls

- **Ignoring the Recovery Rate ($R$)**: Assuming $R = 0$ instead of standard 40% for senior unsecured debt, miscalculating implied hazard rate by 67%.
- **Confusing Par Spread with Quoted Upfront**: Quoting par spread without converting to ISDA upfront payment, leading to incorrect cash settlement calculations.
- **Ignoring Continuous Compounding in RPV01**: Using simple linear multiplication instead of risky survival-discounted integrals for RPV01.

## Verification

- Instantiate `CreditDefaultSwapEngine`. Input Par Spread = 200 bps (0.02), Coupon = 100 bps (0.01), $R = 0.40$, $r = 0.04$, $T = 5.0$. Verify hazard rate $\lambda = \frac{0.02}{0.60} \approx 0.0333$ (3.33%). Verify $PD(5) \approx 1 - e^{-0.1667} \approx 15.35\%$. Compute upfront payment for $10M Notional and verify matching ISDA cash settlement.
- Run `python scripts/test_credit_default_swap_basics_for_algo_context.py`.

## Related Skills

- `counterparty-credit-risk-for-otc-derivatives`
- `convertible-bond-arbitrage-data-requirements`
---
