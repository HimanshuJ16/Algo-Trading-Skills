---
name: record-retention-periods-by-jurisdiction
description: >-
  Multi-jurisdiction trade record retention compliance engine enforcing minimum document retention periods per regulatory authority (SEC, FCA, MAS, ASIC, SEBI).
domain: Tax & Regulatory Compliance
subdomain: Record Retention & Jurisdictional Governance
tags: ["record-retention", "compliance", "sec-rule-17a-4", "fca", "mas", "asic", "sebi", "jurisdiction"]
brokers_frameworks: ["SEC Rule 17a-4 (US)", "FCA SYSC 9 (UK)", "MAS Guidelines (Singapore)", "ASIC (Australia)", "Python Dataclasses"]
version: "1.0.0"
author: algo-trading-skills-contributors
license: Apache-2.0
---

## When to Use

Use this skill when operating algorithmic trading systems across multiple regulatory jurisdictions. Different financial regulators mandate varying minimum record retention periods for trade data, communications, and order audit trails. This engine validates whether trade record retention durations meet or exceed the regulatory minimum for each applicable jurisdiction, flagging records at risk of premature deletion and generating compliance reports.

## Prerequisites

- Record retention data (`record_id`, `record_type`, `jurisdiction`, `creation_date`, `current_retention_years`).
- Jurisdictional retention rules database (built-in defaults: US=7yr, UK=5yr, SG=5yr, AU=7yr, IN=8yr, EU=5yr).

## Workflow

1. **Jurisdiction Lookup**:
   - Map record jurisdiction code to minimum retention period from regulatory database.
2. **Retention Compliance Check**:
   - Compare record's current retention period against jurisdictional minimum.
   - Flag if $\text{CurrentRetention} < \text{MinRequired}$.
3. **Expiry Risk Assessment**:
   - Calculate years remaining before minimum is met: $\text{YearsRemaining} = \text{MinRequired} - \text{CurrentRetention}$.
4. **Audit Report Generation**: Output structured `RetentionComplianceReport`.

> Full procedure: see `references/workflows.md`.
> Standards reference: see `references/standards.md`.
> Printable pre-flight checklist: see `assets/checklist.md`.

## Common Pitfalls

- **Applying Single Jurisdiction Rules Globally**: Using US 7-year rules for UK operations where 5 years may suffice (or vice versa).
- **Ignoring Jurisdiction-Specific Extensions**: Some jurisdictions require extended retention for specific record types (e.g. communications vs trade data).
- **Premature Purge of Cross-Border Records**: Deleting records that satisfy one jurisdiction's requirements but not another's when operating multi-jurisdiction strategies.

## Verification

- Instantiate `RecordRetentionPeriodsByJurisdictionEngine`. Check US record with 5yr retention vs 7yr minimum $\implies$ verify `NON_COMPLIANT` (2yr shortfall). Check UK record with 6yr retention vs 5yr minimum $\implies$ verify `COMPLIANT` (1yr surplus).
- Run `python scripts/test_record_retention_periods_by_jurisdiction.py`.

## Related Skills

- `record-keeping-requirements-for-tax-audit-defense`
- `data-retention-policy-and-storage-tiering`
---
