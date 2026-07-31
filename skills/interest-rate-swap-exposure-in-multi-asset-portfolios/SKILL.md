---
name: interest-rate-swap-exposure-in-multi-asset-portfolios
description: >-
  Fixed income risk analytics engine for Interest Rate Swaps (IRS), calculating Swap DV01/PV01 sensitivities, Pay-Fixed vs Receive-Fixed exposures, and multi-asset portfolio yield curve hedging.
domain: Portfolio Multi-Strategy
subdomain: Fixed Income Risk & IRS Exposure Management
tags: ["interest-rate-swap", "irs", "dv01", "pv01", "yield-curve", "sofr", "multi-asset-risk", "duration-hedging"]
brokers_frameworks: ["SOFR / ISDA Swap Conventions", "Fixed Income DV01 Framework", "Python Dataclasses"]
version: "1.0.0"
author: algo-trading-skills-contributors
license: Apache-2.0
---

## When to Use

Use this skill when managing multi-asset portfolios containing equities, fixed income bonds, and Interest Rate Swaps (IRS). An Interest Rate Swap exchanges fixed rate cash flows for floating index payments (SOFR/Euribor). Because DV01 (Dollar Value of a Basis Point) is additive across assets, this module quantifies IRS DV01 sensitivities, evaluates Pay-Fixed (short duration) vs Receive-Fixed (long duration) positions, and calculates the exact IRS swap notional required to achieve a **DV01-Neutral** portfolio hedge against parallel yield curve shifts (+10 bps).

## Prerequisites

- IRS Position payload (`swap_id`, `notional_usd`, `pay_receive_type`: `PAY_FIXED` / `RECEIVE_FIXED`, `fixed_rate_pct`, `tenor_years`, `floating_rate_index`: `SOFR`).
- Existing portfolio bond DV01 and equity holdings.

## Workflow

1. **IRS Position Ingestion**:
   - Ingest swap parameters (`notional_usd = $10,000,000`, `tenor = 5Y`, `PAY_FIXED`).
2. **Swap DV01 (PV01) Calculation**:
   - Estimate Swap Modified Duration: $D_{\text{mod}} \approx \frac{\text{Tenor}}{2}$.
   - Calculate Base Swap DV01: $\text{DV01}_{\text{base}} = \text{Notional} \times D_{\text{mod}} \times 0.0001$.
   - Directional Signed DV01:
     - `PAY_FIXED` $\implies +\text{DV01}_{\text{base}}$ (Gains $\$1,000$ per +1 bps rate rise).
     - `RECEIVE_FIXED` $\implies -\text{DV01}_{\text{base}}$ (Loses $\$1,000$ per +1 bps rate rise).
3. **Multi-Asset Portfolio Aggregation & Hedging**:
   - Aggregate Bond DV01 + IRS DV01.
   - Calculate required IRS Notional to achieve Net DV01 Neutrality.
4. **Audit Report Generation**: Output structured `InterestRateSwapExposureReport`.

> Full procedure: see `references/workflows.md`.
> Standards reference: see `references/standards.md`.
> Printable pre-flight checklist: see `assets/checklist.md`.

## Common Pitfalls

- **Confusing Pay-Fixed and Receive-Fixed DV01 Signs**: Misinterpreting Pay-Fixed swaps as long duration, causing hedges to double portfolio rate risk instead of neutralizing it.
- **Ignoring Non-Linearity / Convexity**: Relying solely on DV01 for massive yield curve moves ($> 100\text{ bps}$), where convexity effects distort linear approximations.
- **Neglecting Cross-Currency Curve Mismatches**: Aggregating USD SOFR DV01 with EUR Euribor DV01 without applying FX conversion.

## Verification

- Instantiate `InterestRateSwapExposureEngine`. Audit Pay-Fixed Swap ($10\text{M}$ Notional, $5\text{Y}$ Tenor) $\implies$ verify DV01 $= +\$2,500$ / bps. Audit Bond Portfolio (Bond DV01 $= -\$5,000$ / bps) $\implies$ verify engine calculates required Pay-Fixed IRS Notional of $\$20\text{M}$ to achieve DV01 Neutrality ($0.00$ Net DV01).
- Run `python scripts/test_interest_rate_swap_exposure_in_multi_asset_portfolios.py`.

## Related Skills

- `portfolio-stress-test-including-liquidity-crunch-scenarios`
- `cross-strategy-shared-infrastructure-resource-contention`
---
