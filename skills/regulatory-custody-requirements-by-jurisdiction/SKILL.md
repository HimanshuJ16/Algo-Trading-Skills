---
name: regulatory-custody-requirements-by-jurisdiction
description: >-
  Regulatory custody compliance engine auditing asset and crypto custody arrangements against jurisdictional standards (SEC Custody Rule 206(4)-2, MiCA asset segregation, FCA CASS, MAS digital asset custody).
domain: Crypto Custody & Regulatory Compliance
subdomain: Jurisdictional Custody Governance
tags: ["regulatory-custody", "sec-custody-rule", "mica", "fca-cass", "mas", "qualified-custodian", "asset-segregation"]
brokers_frameworks: ["SEC Rule 206(4)-2", "EU MiCA Regulation", "UK FCA CASS Rules", "MAS Digital Asset Guidelines", "Python Dataclasses"]
version: "1.0.0"
author: algo-trading-skills-contributors
license: Apache-2.0
---

## When to Use

Use this skill when designing or auditing client asset and crypto custody infrastructure across global jurisdictions. Financial regulators impose strict rules on who can hold client assets and under what conditions. The SEC requires a "qualified custodian" with annual surprise audits; the EU's MiCA and UK FCA CASS mandate strict bankruptcy-remote asset segregation; Singapore's MAS requires insurance coverage for digital asset custodians. This engine audits custody setups against jurisdictional rules and flags compliance violations.

## Prerequisites

- Custody setup details (`jurisdiction`, `custodian_name`, `custody_type`, `is_asset_segregated`, `has_annual_audit`, `has_insurance_coverage`).
- Built-in jurisdictional rule database (US, EU, UK, SG) with optional custom extensions.

## Workflow

1. **Jurisdictional Rule Lookup**:
   - Retrieve custody rule specification for the target operating jurisdiction.
2. **Qualified Custodian Audit**:
   - Verify custodian type satisfies regulatory requirements (e.g. `QUALIFIED_CUSTODIAN`).
3. **Asset Segregation & Audit Verification**:
   - Verify client assets are held in segregated, bankruptcy-remote accounts.
   - Verify annual independent custody audit and insurance coverage where mandated.
4. **Audit Report Output**: Output structured `JurisdictionalCustodyAuditReport`.

> Full procedure: see `references/workflows.md`.
> Standards reference: see `references/standards.md`.
> Printable pre-flight checklist: see `assets/checklist.md`.

## Common Pitfalls

- **Self-Custodial Assets Without Exemption**: Using self-custody or unapproved internal wallets in jurisdictions requiring a qualified custodian.
- **Commingling Client Assets**: Mixing firm operating capital with client trading assets, violating FCA CASS and MiCA.
- **Uninsured Crypto Custody in Singapore**: Operating digital asset custody in SG without mandated insurance coverage.

## Verification

- Instantiate `RegulatoryCustodyRequirementsByJurisdictionEngine`. Audit US setup with qualified custodian, asset segregation, and annual audit $\implies$ verify `CUSTODY_COMPLIANT`. Audit EU setup lacking insurance $\implies$ verify `CUSTODY_VIOLATION` with `MISSING_INSURANCE`. Audit self-custody in UK $\implies$ verify `UNQUALIFIED_CUSTODIAN` violation.
- Run `python scripts/test_regulatory_custody_requirements_by_jurisdiction.py`.

## Related Skills

- `custodial-vs-non-custodial-tradeoff-assessment`
- `record-retention-periods-by-jurisdiction`
---
