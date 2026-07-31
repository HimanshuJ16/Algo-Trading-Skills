---
name: multi-jurisdiction-tax-residency-implications
description: >-
  Multi-jurisdiction tax residency audit engine evaluating physical presence (183-day rule), Place of Effective Management (POEM), DTAA treaty withholding tax, and Foreign Tax Credit (FTC) offsets.
domain: Tax Accounting & Reporting Global
subdomain: International Tax Residency & Cross-Border DTAA Accounting
tags: ["tax-residency", "multi-jurisdiction", "poem", "dtaa", "foreign-tax-credit", "183-day-rule", "withholding-tax", "cross-border-tax"]
brokers_frameworks: ["DTAA Treaty Rules", "OECD Model Tax Convention", "Python Dataclasses"]
version: "1.0.0"
author: algo-trading-skills-contributors
license: Apache-2.0
---

## When to Use

Use this skill when operating algorithmic trading entities across multiple global tax jurisdictions (e.g., trading US securities from a UK entity, or operating a Cayman Islands fund with Place of Effective Management in Singapore or India). Cross-border trading operations trigger complex tax residency rules under domestic laws (e.g. 183-day Substantial Presence Tests), Place of Effective Management (POEM) corporate exposure, and Double Taxation Avoidance Agreements (DTAA). This engine audits dual-residency tie-breaker rules, applies treaty withholding tax (WHT) reductions, and computes Foreign Tax Credit (FTC) relief to prevent double taxation.

## Prerequisites

- Entity profile payload (`entity_id`, `primary_incorporation_country`, `days_spent_per_country`, `poem_country`).
- Income event payload (`source_country`, `destination_country`, `income_type`, `gross_income_usd`, `local_tax_paid_usd`).
- Domestic tax rates and treaty WHT schedule.

## Workflow

1. **Substantial Presence & POEM Residency Audit**:
   - Evaluate 183-day physical presence test across countries:
     $$\text{Is\_Resident}_c = (N_{\text{days}, c} \ge 183)$$
   - Audit Place of Effective Management (POEM) vs Incorporation country. If POEM $\neq$ Incorporation $\implies$ Flag dual-residency risk.
2. **DTAA Treaty Withholding Tax (WHT) Application**:
   - Determine treaty WHT rate (e.g. US 30% statutory dividend WHT reduced to 15% under US-UK DTAA).
3. **Foreign Tax Credit (FTC) Relief Calculation**:
   - Compute domestic tax liability: $\text{Tax}_{\text{dom}} = \text{Gross\_Income} \times \text{Rate}_{\text{dom}}$.
   - Calculate maximum allowable Foreign Tax Credit:
     $$\text{FTC} = \min(\text{Local\_Tax\_Paid}, \text{Tax}_{\text{dom}})$$
   - Net Tax Payable: $\text{Tax}_{\text{net}} = \max(0.0, \text{Tax}_{\text{dom}} - \text{FTC})$.
4. **Audit Report Generation**: Output structured `TaxResidencyReport`.

> Full procedure: see `references/workflows.md`.
> Standards reference: see `references/standards.md`.
> Printable pre-flight checklist: see `assets/checklist.md`.

## Common Pitfalls

- **Ignoring POEM Rules**: Assuming incorporation in a zero-tax jurisdiction (e.g. Cayman) guarantees zero corporate tax when effective board management occurs in high-tax jurisdictions (US/UK/India).
- **Unclaimed Foreign Tax Credits**: Double-paying tax by failing to claim FTC offsets on cross-border dividend withholding taxes.
- **183-Day Rule Slip-ups**: Miscounting physical travel days of key trading decision-makers across jurisdictions.

## Verification

- Instantiate `MultiJurisdictionTaxEngine`. Audit UK corporate entity receiving $100k US dividend income ($30\%$ US statutory WHT reduced to $15\%$ DTAA WHT = $15k paid) $\implies$ verify $15k FTC offset against 25% UK corporate tax ($25k$), yielding $10k net UK tax payable.
- Run `python scripts/test_multi_jurisdiction_tax_residency_implications.py`.

## Related Skills

- `double-taxation-treaty-considerations-cross-border-trading`
- `transfer-pricing-considerations-for-multi-entity-trading-operations`
---
