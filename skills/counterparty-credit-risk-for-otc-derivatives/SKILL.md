---
name: counterparty-credit-risk-for-otc-derivatives
description: Quantitative OTC derivatives risk engine for computing Current Exposure
  (CE), Potential Future Exposure (PFE), Credit Valuation Adjustment (CVA), ISDA netting
  sets, and CSA collateral margins.
domain: Risk Management & Derivatives
subdomain: Counterparty Credit Risk
tags:
- counterparty-risk
- otc-derivatives
- pfe
- cva
- isda
- csa
- netting
- sa-ccr
brokers_frameworks:
- ISDA Standard
- SA-CCR
- Python Dataclasses
version: "1.0.0"
author: algo-trading-skills-contributors
license: Apache-2.0
---

## When to Use

Use this skill when trading Over-The-Counter (OTC) derivatives (e.g. Interest Rate Swaps, FX Forwards, Equity Swaps, Crypto Perpetuals) with bilateral counterparties. Unlike exchange-traded futures backed by central clearing houses (CCPs), OTC derivatives carry bilateral **Counterparty Credit Risk (CCR)**. This module calculates Current Exposure (CE), SA-CCR Potential Future Exposure (PFE), Credit Valuation Adjustment (CVA), and enforces ISDA Master Agreement close-out netting and CSA collateral threshold limits.

## Prerequisites

- Active OTC contract mark-to-market (MTM) values and notionals.
- ISDA/CSA parameters: `threshold`, `minimum_transfer_amount` (MTA), posted collateral, counterparty probability of default ($PD$), and recovery rate ($R$).

## Workflow

1. **Netting Set Grouping**: Aggregate all active MTM contract values under the same ISDA Netting Set ID.
2. **Current Exposure (CE) Calculation**:
   - $\text{Net MTM} = \sum V_{mtm, i}$.
   - $\text{Netted Current Exposure } CE_{net} = \max(0, \text{Net MTM} - \text{Posted Collateral} - \text{Threshold})$.
3. **Potential Future Exposure (PFE) & EAD**:
   - Calculate SA-CCR Add-On: $\text{AddOn} = \text{Notional} \times \text{Risk Factor}$.
   - Exposure at Default $EAD = CE_{net} + \text{PFE}$.
4. **Credit Valuation Adjustment (CVA)**:
   - $CVA = (1 - R) \times EAD \times PD$.
5. **Pre-Trade Limit & CSA Collateral Call Audit**:
   - If $EAD > \text{Max Credit Limit}$, block trade or trigger mandatory Margin Call for collateral top-up.

> Full procedure: see `references/workflows.md`.
> Standards reference: see `references/standards.md`.
> Printable pre-flight checklist: see `assets/checklist.md`.

## Common Pitfalls

- **Gross Exposure Without Netting**: Calculating credit exposure contract-by-contract without ISDA legal close-out netting, grossly overstating credit exposure.
- **Ignoring Margin Transfer Thresholds (MTA)**: Failing to factor Minimum Transfer Amount (MTA) into collateral call triggers, leading to un-collateralized micro-exposure drift.
- **Static Default Probabilities ($PD$)**: Using static credit ratings without updating market-implied $PD$ derived from CDS spreads.

## Verification

- Instantiate `OtcCounterpartyRiskEngine`. Register 3 OTC swap contracts under Netting Set `ISDA_BANK_A` (MTMs: +$500k, -$200k, +$100k; Net MTM = +$400k). Post $300k collateral. Verify Netted Current Exposure = $100k. Compute SA-CCR PFE ($50k) and verify $EAD = $150k. Calculate CVA ($PD = 2\%, R = 40\%$) and verify $CVA = 0.60 \times 150k \times 0.02 = \$1,800$.
- Run `python scripts/test_otc_counterparty_risk.py`.

## Related Skills

- `counterparty-and-broker-concentration-risk`
- `broker-account-margin-call-handling`
---
