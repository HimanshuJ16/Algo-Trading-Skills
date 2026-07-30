---
name: esma-double-volume-cap-mechanism
description: >-
  Quantitative European regulatory compliance engine for monitoring ESMA Double Volume Cap (DVC) dark pool limits (4% venue / 8% EU-wide), managing dark trading suspensions, and rerouting orders to Lit or Large-In-Scale (LIS) waivers.
domain: Venue Integration & Protocols
subdomain: European Regulatory Compliance (MiFID II / MiFIR)
tags: ["esma", "double-volume-cap", "mifid-ii", "dark-pools", "reference-price-waiver", "lis-waiver", "smart-order-router"]
brokers_frameworks: ["ESMA DVC Register", "Cboe Europe Dark", "Python Dataclasses"]
version: "1.0.0"
author: algo-trading-skills-contributors
license: Apache-2.0
---

## When to Use

Use this skill in European quantitative trading algorithms, Smart Order Routers (SOR), and dark pool execution adapters. Under MiFID II Article 5 and MiFIR rules, dark pool trading under Reference Price Waivers (RPW) is capped to protect price discovery. When a stock's rolling 12-month dark volume exceeds **4.0%** on a single venue or **8.0%** EU-wide (or 7.0% under updated Single Volume Cap rules), ESMA triggers a mandatory dark trading suspension. Smart Order Routers MUST intercept dark orders during suspensions and reroute them to Lit venues or Large-In-Scale (LIS) waivers.

## Prerequisites

- Stock ISIN / Symbol (e.g. `DE0007100000` - Mercedes-Benz Group AG).
- Rolling 12-month total EU volume (€), venue dark volume (€), and EU-wide dark volume (€).
- Proposed order details (`order_val_eur`, `intended_waiver_type`: `'RPW'`, `'NTW'`, `'LIS'`).

## Workflow

1. **Dark Volume Share Calculation**:
   - $\text{Venue Dark Share \%} = \frac{\text{Venue Dark Volume}}{\text{Total EU Market Volume}} \times 100\%$.
   - $\text{EU-Wide Dark Share \%} = \frac{\text{EU Dark Volume}}{\text{Total EU Market Volume}} \times 100\%$.
2. **ESMA Volume Cap Audit & Suspension Check**:
   - If $\text{Venue Share} > 4.0\% \implies$ Flag `SUSPENDED_4PCT_VENUE`.
   - If $\text{EU Share} > 8.0\% \implies$ Flag `SUSPENDED_8PCT_EU_WIDE`.
3. **Smart Order Router (SOR) Rerouting**:
   - If stock is suspended and order uses `RPW` dark waiver:
     - Check if $\text{Order Value} \ge \text{LIS Threshold}$ (e.g. €100,000) $\implies$ Allow `LIS_WAIVER` dark execution.
     - Else $\implies$ Block dark routing and reroute to `LIT_VENUE` (e.g. Xetra).
4. **Audit Report Generation**: Output structured `EsmaDvcAuditReport`.

> Full procedure: see `references/workflows.md`.
> Standards reference: see `references/standards.md`.
> Printable pre-flight checklist: see `assets/checklist.md`.

## Common Pitfalls

- **Routing Dark Orders During Active ESMA Suspensions**: Failing to update ESMA DVC suspension files, executing non-LIS dark orders on suspended stocks, incurring regulatory fines.
- **Conflating LIS Waivers with Standard Dark Waivers**: Assuming ESMA volume caps apply to Large-In-Scale (LIS) trades ($\ge €100,000$), which are exempt from DVC caps.
- **Ignoring 12-Month Rolling Windows**: Calculating dark volume ratios on calendar-year bounds instead of rolling 12-month lookback windows.

## Verification

- Instantiate `EsmaDoubleVolumeCapEngine`. Submit stock with $8.5\%$ EU dark volume share (breaching $8.0\%$ EU cap). Route €50,000 dark order under Reference Price Waiver (`RPW`). Verify engine flags `SUSPENDED_8PCT_EU_WIDE`, blocks dark routing, and reroutes order to `LIT_VENUE`. Submit €200,000 order ($\ge €100\text{k}$ LIS threshold). Verify engine allows `LIS_WAIVER` dark execution.
- Run `python scripts/test_esma_double_volume_cap_mechanism.py`.

## Related Skills

- `deutsche-borse-xetra-api-integration`
- `auction-only-order-types-for-illiquid-names`
---
