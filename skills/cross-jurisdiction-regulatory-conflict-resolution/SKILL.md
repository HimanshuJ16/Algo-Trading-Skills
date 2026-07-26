---
name: cross-jurisdiction-regulatory-conflict-resolution
description: >-
  Quantitative compliance resolution engine for managing cross-jurisdiction regulatory conflicts (SEC vs MiFID II vs FCA), enforcing Strictest Rule Primacy, and auditing pre-trade order routing.
domain: Compliance & Legal
subdomain: Cross-Jurisdiction Regulation
tags: ["compliance", "regulatory-conflict", "mifid-ii", "sec", "pfof", "short-selling", "lei", "strictest-rule-primacy"]
brokers_frameworks: ["MiFID II", "SEC", "FCA", "Python Dataclasses"]
version: "1.0.0"
author: algo-trading-skills-contributors
license: Apache-2.0
---

## When to Use

Use this skill in multi-national quantitative trading firms operating across multiple regulatory regimes (e.g. US SEC/FINRA, EU ESMA MiFID II, UK FCA, Hong Kong SFC, Japan FSA). Conflicts frequently arise between jurisdictions regarding Payment for Order Flow (PFOF), pre-trade LEI/Trader ID tagging, research unbundling, and short-selling disclosures. This module implements the **Strictest Rule Resolution Strategy**—automatically selecting and enforcing the most restrictive compliance constraint across overlapping jurisdictions.

## Prerequisites

- Trade order payload (`entity_jurisdiction`, `venue_jurisdiction`, `symbol`, `is_short`, `pfof_routed`).
- Regulatory rule mapping for all active jurisdictions (`is_pfof_allowed`, `is_lei_mandatory`, `short_sell_restriction_level`).

## Workflow

1. **Overlapping Jurisdiction Mapping**:
   - Determine applicable jurisdictions for order: $\mathcal{J} = \{\text{Entity Jurisdiction}, \text{Venue Jurisdiction}\}$.
2. **Rule Matrix Resolution (Strictest Rule Primacy)**:
   - For PFOF: If ANY jurisdiction in $\mathcal{J}$ bans PFOF $\implies$ Block PFOF routing.
   - For LEI Tagging: If ANY jurisdiction in $\mathcal{J}$ requires LEI $\implies$ Enforce LEI validation.
   - For Short Selling: Enforce the highest restriction level (e.g. `BAN` > `UPTICK_RULE` > `REPORTING`).
3. **Pre-Trade Compliance Audit**:
   - Evaluate proposed order against resolved strict rule set.
4. **Audit Decision Generation**:
   - Return `RegulatoryComplianceDecision` with rationale and compliance status (`APPROVED` vs `REJECTED`).

> Full procedure: see `references/workflows.md`.
> Standards reference: see `references/standards.md`.
> Printable pre-flight checklist: see `assets/checklist.md`.

## Common Pitfalls

- **Applying Local Rules Only**: Applying US SEC rules to an order routed by a US entity to an EU venue under MiFID II, violating EU PFOF or LEI reporting mandates.
- **Ignoring Entity Extraterritoriality**: Assuming a UK subsidiary trading US equities is exempt from UK FCA research unbundling or short selling rules.
- **Manual Compliance Auditing**: Relying on offline manual reviews for real-time algorithmic order flows, causing compliance latency bottlenecks.

## Verification

- Instantiate `CrossJurisdictionRegulatoryConflictEngine`. Configure `US_SEC` (PFOF allowed, LEI optional) and `EU_MIFID_II` (PFOF banned, LEI mandatory). Submit an order involving a US entity trading on an EU venue with PFOF routing enabled. Verify engine rejects PFOF under Strictest Rule Primacy and mandates LEI tagging.
- Run `python scripts/test_cross_jurisdiction_regulatory_conflict_resolution.py`.

## Related Skills

- `cross-border-data-transfer-restrictions-for-trade-data`
- `best-execution-record-keeping-global`
---
