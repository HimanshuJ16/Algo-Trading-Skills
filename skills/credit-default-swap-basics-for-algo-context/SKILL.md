---
name: credit-default-swap-basics-for-algo-context
description: >-
  Use when integrating credit default swap metrics into a strategy or capital-structure
  arbitrage: hazard rates, implied default probability, indicative upfront on the
  post-2009 fixed-coupon convention, and risky annuity.
license: Apache-2.0
metadata:
  domain: algorithmic-trading
  subdomain: multi-asset-derivatives
  tags: cds, credit-default-swap, hazard-rate, default-probability, isda-upfront, rpv01, cross-asset
  brokers_frameworks: "ISDA CDS Conventions; Python Math"
  version: "1.2.0"
  author: algo-trading-skills-contributors
---

## When to Use

Use this skill when integrating Credit Default Swap (CDS) metrics into quantitative trading algorithms, credit risk models, or cross-asset capital structure arbitrage strategies (CDS vs. Equity). Since the ISDA April 2009 "Big Bang" standardisation, North American corporate CDS (SNAC) trade on fixed coupons — 100 bps for Investment Grade, 500 bps for High Yield — with the spread difference settled as an upfront payment. This module calculates the hazard rate ($\lambda$), cumulative default probability ($PD$), Risky PV01 ($RPV01$), indicative upfront payments, and CDS-equity cross-asset z-score signals.

## When NOT to Use

- **Exact ISDA cash settlement.** The upfront here uses a continuous-annuity approximation, not the ISDA CDS Standard Model (quarterly premiums on IMM dates, Actual/360 accrual, ISDA curves). It is also a *clean* figure: ISDA cash settlement nets the accrued premium since the last IMM date, which the seller rebates to the buyer, and that term is not modelled. Use https://www.cdsmodel.com/ for settlement-matching figures; treat this engine's output as indicative.
- **Full curve stripping.** The hazard rate is the flat credit-triangle approximation $\lambda = s/(1-R)$ — no term structure, no calibration to multiple maturities.
- **European/sovereign CDS conventions.** Coupon grids outside SNAC differ (e.g. 25/100/500/1000 tiers); verify the region's convention before applying 100/500.
- **Distressed-name pricing.** Near default, spreads converge toward ~1000 bps and upfront quotes move to points-upfront; the credit-triangle assumptions degrade.

## Prerequisites

- CDS Par Spread $s_{par}$ (in bps) and Standard Coupon $s_{coupon}$ (100 or 500 bps for SNAC).
- Maturity $T$ (years), Risk-Free Rate $r$ (continuous), and Recovery Rate $R$ (default 40% = 0.40, the ISDA CDS Standard Model convention for senior unsecured).

## Workflow

1. **Hazard Rate Estimation (Credit Triangle)**:
   - Hazard rate $\lambda = \frac{s_{par}}{1 - R}$ (flat-hazard textbook approximation).
2. **Default & Survival Probability**:
   - Survival Probability $S(T) = e^{-\lambda T}$.
   - Cumulative Default Probability $PD(T) = 1 - S(T)$.
3. **Risky PV01 (RPV01) Calculation**:
   - Continuous annuity factor: $RPV01 = \frac{1 - e^{-(r + \lambda) T}}{r + \lambda}$, evaluated at its limit $T$ only when $r + \lambda$ is exactly zero.
   - Decision point: a *negative* $r + \lambda$ (negative policy rate against a tight IG hazard) is a valid annuity strictly greater than $T$ — it must be evaluated, not clamped to $T$, or the upfront is understated.
4. **Indicative Upfront Payment**:
   - $\text{Upfront} = \text{Notional} \times RPV01 \times (s_{par} - s_{coupon})$; the protection buyer pays when $s_{par} > s_{coupon}$.
5. **Credit Tier Classification (heuristic)**:
   - Spread buckets at 150 / 1000 bps (informal desk conventions, parameterisable): `< 150` INVESTMENT_GRADE, `[150, 1000)` CROSSOVER_HIGH_YIELD, `>= 1000` DISTRESSED. Note 500 bps is the standard HY coupon and cannot be the distressed boundary.
6. **Cross-Asset Capital Structure Signal**:
   - `generate_cross_asset_signal` computes $z = (s_{last} - \bar{s}) / \sigma_s$ over the spread history.
   - Decision point: $z > +2 \implies$ `SHORT_EQUITY_LONG_CDS` (credit distress spike); $z < -2 \implies$ `LONG_EQUITY_SHORT_CDS` (compression); a flat history (zero $\sigma$) yields NEUTRAL — require genuine dispersion before acting.

> Full procedure: see `references/workflows.md`.
> Standards reference: see `references/standards.md`.
> Printable pre-flight checklist: see `assets/checklist.md`.

## Common Pitfalls

- **Treating the approximation as settlement**: quoting this engine's upfront as an ISDA cash-settlement figure — the Standard Model's quarterly Act/360 annuity will differ.
- **Ignoring the Recovery Rate ($R$)**: Assuming $R = 0$ instead of standard 40% for senior unsecured debt, miscalculating implied hazard rate by 67%.
- **Setting $R = 1$**: loss given default becomes zero and the hazard rate is undefined; the engine rejects it (`[0, 1)` is the valid range).
- **Confusing Par Spread with Quoted Upfront**: Quoting par spread without converting to an upfront payment, leading to incorrect cash settlement calculations.
- **Labeling 500 bps "distressed"**: 500 bps is the standard high-yield coupon; distress is conventionally ~1000+ bps, where quotes shift to points upfront.
- **Clamping RPV01 to $T$ under negative rates**: treating $r + \lambda \le 0$ as the zero-limit case returns $T$, but for $r + \lambda < 0$ the annuity is $\frac{e^{|r+\lambda|T} - 1}{|r+\lambda|} > T$. At $r = -2\%$, $\lambda = 1\%$, $T = 5$ the clamp understates RPV01 by ~2.5% and the upfront with it.
- **Quoting the clean upfront as the cash settlement**: the settlement amount nets the accrued premium from the last IMM date (seller rebates it to the buyer); this engine returns the clean figure only.
- **Ignoring Continuous Compounding in RPV01**: Using simple linear multiplication instead of survival-discounted integrals for RPV01.
- **Cross-asset signals without dispersion**: a z-score against a flat history means nothing — the engine returns NEUTRAL, and so should the strategy.

## Verification

- Instantiate `CreditDefaultSwapEngine`. Input Par Spread = 200 bps, Coupon = 100 bps, $R = 0.40$, $r = 0.04$, $T = 5.0$. Verify hazard rate $\lambda = \frac{0.02}{0.60} = 0.0333$ (3.33%), $PD(5) = 1 - e^{-1/6} \approx 15.35\%$, $RPV01 \approx 4.1858$, and the indicative upfront for $10M notional $\approx \$418{,}580$ (buyer pays; par > coupon). Verify tier `CROSSOVER_HIGH_YIELD`.
- Negative-rate check: `CreditDefaultSwapEngine(risk_free_rate=-0.06).calculate_rpv01(0.01, 5.0)` $= \frac{e^{0.25} - 1}{0.05} \approx 5.6805$ — strictly above $T = 5$, never equal to it.
- `generate_cross_asset_signal([100]*9 + [200])` → mean 110, population $\sigma = 30$, $z = 3.0$ → `SHORT_EQUITY_LONG_CDS`.
- Run `python -m unittest discover -s skills/credit-default-swap-basics-for-algo-context/scripts`.

## Related Skills

- `counterparty-credit-risk-for-otc-derivatives`
- `convertible-bond-arbitrage-data-requirements`
