---
name: kyc-aml-considerations-for-algo-trading-entities
description: >-
  Institutional KYC/AML compliance engine for algorithmic trading entities, auditing Ultimate Beneficial Ownership (UBO >= 25%), OFAC/PEP sanctions screening, and FATF high-risk jurisdiction compliance.
domain: Regulatory Compliance Global
subdomain: Corporate Governance & Anti-Money Laundering (AML)
tags: ["kyc", "aml", "ubo", "fincen", "fatf", "sanctions-screening", "pep", "enhanced-due-diligence"]
brokers_frameworks: ["FinCEN CDD Rule", "FATF Recommendation 24", "Python Dataclasses"]
version: "1.0.0"
author: algo-trading-skills-contributors
license: Apache-2.0
---

## When to Use

Use this skill when onboarding institutional trading funds, proprietary trading firms, or algo trading corporate entities with prime brokers, exchanges, or OTC desks. Regulatory frameworks (**FinCEN CDD Rule**, **FATF Recommendation 24**) require identifying and verifying all **Ultimate Beneficial Owners (UBOs)** holding $\ge 25\%$ ownership or significant managerial control, performing OFAC/PEP sanctions screening, and enforcing Enhanced Due Diligence (EDD) for high-risk FATF jurisdictions.

## Prerequisites

- Corporate Entity payload (`entity_name`, `incorporation_country`, `banking_country`, `ubos`: list of `UboRecord(name, ownership_pct, is_pep, is_sanctioned, is_verified)`).
- FATF High-Risk Jurisdiction Blacklist (`IRAN`, `NORTH_KOREA`, `MYANMAR`).

## Workflow

1. **UBO Threshold Audit**:
   - Verify all natural persons holding $\ge 25\%$ ownership are identified and verified (`is_verified == True`).
   - If any $\ge 25\%$ UBO is unverified $\implies$ Trigger `REJECTED_UNVERIFIED_UBO`.
2. **OFAC Sanctions & PEP Screening**:
   - Audit all UBOs and entity against sanctions databases (`is_sanctioned == True`). If matched $\implies$ Trigger `REJECTED_SANCTIONS_MATCH`.
3. **FATF High-Risk Jurisdiction Audit**:
   - Audit incorporation and banking countries against FATF lists. If blacklisted $\implies$ Trigger `REJECTED_FATF_HIGH_RISK`.
4. **Audit Report Generation**: Output structured `KycAmlAuditReport`.

> Full procedure: see `references/workflows.md`.
> Standards reference: see `references/standards.md`.
> Printable pre-flight checklist: see `assets/checklist.md`.

## Common Pitfalls

- **Failing to Look-Through Corporate Shell Layers**: Accepting intermediate holding companies as UBOs instead of identifying the underlying natural persons who control $\ge 25\%$.
- **Ignoring PEP / Sanctions Screenings**: Onboarding algorithmic trading accounts without running real-time OFAC/UN/EU sanctions checks on key executives and directors.
- **Neglecting Banking Jurisdiction Risks**: Onboarding an entity incorporated in a low-risk country that routes funds through uncooperative FATF blacklisted offshore banks.

## Verification

- Instantiate `KycAmlEntityComplianceEngine`. Audit Verified Institutional Fund (UBO 1 $= 60\%$, UBO 2 $= 40\%$, both verified, no sanctions) $\implies$ verify `KYC_AML_APPROVED`. Audit Unverified UBO ($30\%$ owner unverified) $\implies$ verify `REJECTED_UNVERIFIED_UBO`. Audit Sanctioned Hit $\implies$ verify `REJECTED_SANCTIONS_MATCH`.
- Run `python scripts/test_kyc_aml_considerations_for_algo_trading_entities.py`.

## Related Skills

- `alt-data-insider-trading-compliance`
- `insider-trading-controls-for-alternative-data-usage`
---
