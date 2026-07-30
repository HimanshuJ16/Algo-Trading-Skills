---
name: data-localization-requirements-for-trade-records
description: Quantitative regulatory compliance engine for auditing cross-border trade
  record localization laws (China PIPL/DSL, India RBI/SEBI, EU GDPR, US SEC 17a-4)
  and blocking illegal cross-border egress.
domain: Data Management Global
subdomain: Regulatory Compliance & Sovereignty
tags:
- data-localization
- trade-record-sovereignty
- pipl
- rbi-data-localization
- mifid-ii
- sec-17a-4
- cross-border-egress
brokers_frameworks:
- China PIPL/DSL
- India RBI/SEBI CSCRF
- EU GDPR
- SEC Rule 17a-4
- Python Dataclasses
version: 1.0.0
author: algo-trading-skills-contributors
license: Apache-2.0
---

## When to Use

Use this skill in multi-region quantitative trading systems, broker adapters, and cloud storage pipelines to enforce jurisdictional data residency mandates. Regulatory regimes (e.g. China CAC/PIPL, India RBI Payment & Trade Directive, EU GDPR) mandate that trade records, payment ledgers, and trader PII must be stored on primary databases physically located within national borders. This module evaluates target cloud storage regions, blocks unauthorized cross-border replication, and logs compliance audits.

## Prerequisites

- Trade record metadata (`record_id`, `origin_jurisdiction`, `destination_region`, `record_type`, `is_primary_store`).
- Jurisdiction cloud region mapping (e.g. `CN` $\to$ `cn-north-1`, `IN` $\to$ `ap-south-1`, `EU` $\to$ `eu-central-1`).

## Workflow

1. **Jurisdiction Mapping & Policy Lookup**:
   - Lookup `origin_jurisdiction` policy:
     - `CN` (China): Primary storage MUST be in `cn-north-1`/`cn-northwest-1`. Egress to non-CN regions BLOCKED.
     - `IN` (India): Primary payment & trade ledger MUST be in `ap-south-1`.
     - `EU` (European Union): Primary storage in EU regions (`eu-central-1`, `eu-west-1`). Egress requires adequacy/SCCs.
     - `US` (United States): Multi-region allowed; SEC Rule 17a-4 WORM 6-year retention enforced.
2. **Replication & Egress Audit**:
   - If `destination_region` violates localization law $\implies$ Issue `LOCALIZATION_EGRESS_BLOCKED`.
3. **WORM Retention Verification**:
   - Verify 6-year WORM compliance for SEC/FCA covered entities.
4. **Audit Report Generation**: Output structured `DataLocalizationAuditReport`.

> Full procedure: see `references/workflows.md`.
> Standards reference: see `references/standards.md`.
> Printable pre-flight checklist: see `assets/checklist.md`.

## Common Pitfalls

- **Replicating Chinese Ticks to US AWS Regions**: Configuring multi-region S3 replication from `cn-north-1` to `us-east-1`, violating China's Data Security Law (DSL).
- **Hosting Indian Trade Ledgers in US Regions**: Storing primary Indian trade records outside `ap-south-1`, violating RBI / SEBI localization mandates.
- **Conflating Anonymized Ticks with PII**: Applying strict localization rules to anonymized price quotes while missing raw trader PII and FIX order messages.

## Verification

- Instantiate `DataLocalizationComplianceEngine`. Submit a Chinese trade record (`origin="CN"`, `destination_region="us-east-1"`). Verify engine blocks egress (`status="LOCALIZATION_VIOLATION_BLOCKED"`). Submit an Indian trade record (`origin="IN"`, `destination_region="ap-south-1"`). Verify engine approves storage (`status="COMPLIANT"`).
- Run `python scripts/test_data_localization_requirements_for_trade_records.py`.

## Related Skills

- `cross-border-data-transfer-restrictions-for-trade-data`
- `record-keeping-requirements-for-tax-audit-defense`
---
