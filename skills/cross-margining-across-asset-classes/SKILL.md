---
name: cross-margining-across-asset-classes
description: >-
  Quantitative multi-asset treasury and margin optimization module for computing cross-margining offsets across clearing houses (CME, OCC, FICC), reducing initial margin, and calculating capital efficiency.
domain: Treasury & Clearing Operations
subdomain: Portfolio & Cross Margining
tags: ["cross-margining", "portfolio-margin", "cme", "occ", "ficc", "margin-offset", "capital-efficiency"]
brokers_frameworks: ["CME SPAN", "OCC STANS", "Python Dataclasses"]
version: "1.0.0"
author: algo-trading-skills-contributors
license: Apache-2.0
---

## When to Use

Use this skill in multi-asset trading firms, market makers, and treasury desks holding correlated positions across different clearing houses or asset classes (e.g. S&P 500 futures `ES` at CME vs. S&P 500 options `SPX` at OCC vs. ETF `SPY`; or Treasury Futures `ZN` vs. Interest Rate Swaps). Calculating standalone margin for each asset class in isolation traps idle collateral. This module computes cross-margin offset discounts, quantifies dollar margin savings, and evaluates capital efficiency gains.

## Prerequisites

- Position inventory with standalone margin requirements ($M_i$) per asset class.
- Pairwise correlation / offset credit matrix ($\rho_{i,j}$) established by clearing agreements (e.g., CME-OCC joint cross-margining program).

## Workflow

1. **Standalone Margin Sumation**:
   - Compute total un-offset margin: $M_{\text{standalone}} = \sum_i M_i$.
2. **Cross-Margined Risk Reduction**:
   - Compute netted portfolio margin:
     $$M_{\text{cross}} = \sqrt{\sum_i M_i^2 + 2 \sum_{i < j} \rho_{i,j} M_i M_j}$$
   - Apply clearing house minimum risk floor $M_{\text{floor}} = 0.20 \times M_{\text{standalone}}$.
3. **Capital Savings & Efficiency Calculation**:
   - $\text{Margin Savings USD} = M_{\text{standalone}} - M_{\text{cross}}$.
   - $\text{Capital Efficiency Gain Pct} = \frac{\text{Margin Savings}}{M_{\text{standalone}}} \times 100\%$.
4. **Collateral Re-allocation**: Release freed collateral to capital pool.

> Full procedure: see `references/workflows.md`.
> Standards reference: see `references/standards.md`.
> Printable pre-flight checklist: see `assets/checklist.md`.

## Common Pitfalls

- **Assuming 100% Margin Offset**: Assuming perfectly negatively correlated positions (e.g., Long Futures vs Short Stock) eliminate 100% of margin, ignoring clearing house model risk floors.
- **Unregistered Cross-Margin Accounts**: Computing cross-margin savings without opening a formal joint clearing account at CME-OCC / CME-FICC.
- **Ignoring Correlation Breakdown in Stress Tests**: Relying on normal-market correlation offsets during liquidity crises when correlations collapse.

## Verification

- Instantiate `CrossMarginingCalculator`. Input standalone margin requirements: $M_{\text{EquityFutures}} = \$500,000$, $M_{\text{IndexOptions}} = \$400,000$. Input cross-asset correlation offset $\rho = -0.80$. Compute cross-margined requirement and verify $M_{\text{cross}} \approx \$250,998$ ($49.8\%$ capital savings).
- Run `python scripts/test_cross_margining_across_asset_classes.py`.

## Related Skills

- `capital-efficiency-across-cross-margined-strategies`
- `broker-account-margin-call-handling`
---
