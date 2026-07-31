---
name: record-keeping-requirements-for-tax-audit-defense
description: >-
  Trade record-keeping compliance engine validating mandatory tax audit documentation including cost basis, holding periods, wash sale flags, and retention policy enforcement.
domain: Tax & Regulatory Compliance
subdomain: Trade Record Retention & Audit Defense
tags: ["record-keeping", "tax-audit", "cost-basis", "holding-period", "wash-sale", "retention-policy", "irs-compliance"]
brokers_frameworks: ["IRS Revenue Procedure 98-25", "SEC Rule 17a-4", "Python Dataclasses"]
version: "1.0.0"
author: algo-trading-skills-contributors
license: Apache-2.0
---

## When to Use

Use this skill when managing algorithmic trading operations subject to tax reporting obligations (IRS, HMRC, CRA). Active trading firms generate thousands of taxable events daily. Tax authorities require complete, contemporaneous records of every trade including cost basis, acquisition/disposal dates, holding period classification (short-term vs long-term), and wash sale adjustments. This engine audits trade record completeness, flags missing mandatory fields, enforces minimum retention periods (7 years), and generates audit-ready compliance reports.

## Prerequisites

- Trade records (`trade_id`, `symbol`, `side`, `quantity`, `price`, `trade_date`, `cost_basis_usd`, `proceeds_usd`, `holding_period_days`, `wash_sale_flag`, `lot_method`).
- Config options (`min_retention_years`: default 7, `mandatory_fields`: list of required field names).

## Workflow

1. **Mandatory Field Completeness Audit**:
   - Validate each trade record contains all mandatory fields (`trade_id`, `symbol`, `side`, `quantity`, `price`, `trade_date`, `cost_basis_usd`).
   - Flag records with missing or null mandatory fields.
2. **Holding Period Classification**:
   - Classify trades as Short-Term ($\le 365$ days) or Long-Term ($> 365$ days).
3. **Wash Sale Detection Flag**:
   - Verify wash sale flag is populated for all sell transactions.
4. **Retention Policy Enforcement**:
   - Verify record age against minimum retention period ($\ge 7$ years).
5. **Audit Report Generation**: Output structured `TaxAuditComplianceReport`.

> Full procedure: see `references/workflows.md`.
> Standards reference: see `references/standards.md`.
> Printable pre-flight checklist: see `assets/checklist.md`.

## Common Pitfalls

- **Missing Cost Basis Records**: Failing to record per-lot cost basis, making it impossible to calculate capital gains accurately during audit.
- **Premature Record Deletion**: Purging trade records before the minimum 7-year retention window required by IRS/SEC.
- **Ignoring Wash Sale Adjustments**: Failing to track 30-day wash sale windows on re-purchased securities.

## Verification

- Instantiate `RecordKeepingRequirementsForTaxAuditDefenseEngine`. Add 2 complete trade records and 1 record missing cost basis $\implies$ verify `AUDIT_ISSUES_FOUND` status with 1 incomplete record flagged. Add only complete records $\implies$ verify `AUDIT_COMPLIANT` status.
- Run `python scripts/test_record_keeping_requirements_for_tax_audit_defense.py`.

## Related Skills

- `mark-to-market-election-for-active-traders-us`
- `wash-sale-rule-tracking-across-accounts`
---
