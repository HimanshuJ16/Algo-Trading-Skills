---
name: insurance-coverage-assessment-for-custodied-crypto
description: >-
  Institutional audit engine for crypto custody insurance, evaluating Specie offline cold vault policies vs Crime hot/warm wallet policies, pro-rata pooled limit dilution, and uncovered shortfall risks.
domain: Crypto Custody Security
subdomain: Institutional Treasury Audit & Custody Insurance
tags: ["crypto-insurance", "custody-audit", "specie-policy", "crime-policy", "hot-cold-split", "pooled-limit-dilution", "risk-assessment"]
brokers_frameworks: ["Institutional Crypto Custodians (BitGo, Fireblocks, Coinbase)", "Lloyd's Specie & Crime Policies", "Python Dataclasses"]
version: "1.0.0"
author: algo-trading-skills-contributors
license: Apache-2.0
---

## When to Use

Use this skill when auditing institutional digital asset custody providers (BitGo, Coinbase Custody, Anchorage, Fireblocks) and conducting fund treasury risk management. A common misconception in institutional crypto is assuming custodian insurance covers 100% of firm assets. Custodians often advertise a $\$250\text{M}$ policy, but that limit is SHARED across all clients ($\text{Total Custodian AUM} = \$10\text{B}$), or applies exclusively to offline **Cold Specie** vaults while **Hot Wallet Crime** coverage is severely capped. This module audits hot vs cold wallet insurance coverage ratios, calculates pro-rata pooled policy dilution, and quantifies net uninsured capital exposure.

## Prerequisites

- Custody treasury spec (`custodian_name`, `firm_hot_aum_usd`, `firm_cold_aum_usd`, `hot_crime_policy_limit_usd`, `cold_specie_policy_limit_usd`, `total_custodian_cold_aum_usd`).
- Target minimum hot wallet coverage ratio ($\ge 1.0$) and minimum cold vault pro-rata coverage.

## Workflow

1. **Hot Wallet Crime Policy Audit**:
   - Compute Hot Wallet Coverage Ratio: $\text{Ratio}_{\text{hot}} = \frac{\text{Hot Crime Limit}}{\text{Firm Hot AUM}}$.
   - If $\text{Ratio}_{\text{hot}} < 1.0 \implies$ Flag Hot Wallet Uninsured Shortfall.
2. **Cold Storage Specie Policy & Pooled Dilution Audit**:
   - Compute Custodian Pooled Dilution Factor: $\text{Dilution Ratio} = \min\left(1.0, \frac{\text{Cold Specie Limit}}{\text{Total Custodian Cold AUM}}\right)$.
   - Calculate Effective Firm Protected USD: $\text{Firm Protected USD} = \text{Firm Cold AUM} \times \text{Dilution Ratio}$.
   - Compute Cold Storage Coverage Ratio.
3. **Net Uninsured Shortfall Calculation**:
   - Total Uninsured Capital USD $= (\text{Hot AUM} - \text{Covered Hot}) + (\text{Cold AUM} - \text{Covered Cold})$.
4. **Audit Report Generation**: Output structured `CustodyInsuranceReport`.

> Full procedure: see `references/workflows.md`.
> Standards reference: see `references/standards.md`.
> Printable pre-flight checklist: see `assets/checklist.md`.

## Common Pitfalls

- **Confusing Cold Specie with Hot Crime Insurance**: Assuming a custodian's $\$250\text{M}$ Specie offline vault policy covers hot wallet API hacks, leaving active trading funds unprotected.
- **Ignoring Pooled Limit Dilution**: Failing to account for shared custodian policy limits ($100\times$ over-subscription across client base), overestimating actual recovered capital during insolvency/theft.
- **Assuming Policy Covers Smart Contract Hacks**: Believing crime insurance covers DeFi protocol exploits or staking slashing events, which are standard policy exclusions.

## Verification

- Instantiate `CustodyInsuranceAssessmentEngine`. Audit Custodian Spec (Firm Hot AUM $\$2\text{M}$, Crime Limit $\$5\text{M}$, Firm Cold AUM $\$20\text{M}$, Specie Limit $\$250\text{M}$, Total Custodian Cold AUM $\$1\text{B} \implies$ Dilution Ratio $25\%$) $\implies$ verify engine calculates Hot Coverage $= 100\%$, Cold Effective Coverage $= \$5\text{M}$ ($25\%$), and Uninsured Cold Shortfall $= \$15\text{M}$.
- Run `python scripts/test_insurance_coverage_assessment_for_custodied_crypto.py`.

## Related Skills

- `hot-cold-wallet-split-for-trading-bots`
- `custodial-vs-non-custodial-tradeoff-assessment`
---
