---
name: sanctions-screening-for-counterparties-and-instruments
description: >-
  Production-grade sanctions screening engine auditing counterparties and financial instrument issuers against OFAC SDN, EU Consolidated, UN, and UK HMT lists using Levenshtein fuzzy string matching, OFAC 50% Rule ownership evaluation, and country embargo enforcement.
domain: Compliance & Risk Governance
subdomain: Sanctions Screening & Counterparty AML
tags: ["sanctions-screening", "ofac-sdn", "eu-consolidated", "un-sanctions", "fuzzy-matching", "ofac-50-percent-rule"]
brokers_frameworks: ["OFAC Sanctions Framework", "Levenshtein Distance Matching", "Python Dataclasses"]
version: "1.0.0"
author: algo-trading-skills-contributors
license: Apache-2.0
---

## When to Use

Use this skill when onboarding counterparties (prime brokers, execution venues, liquidity providers, OTC counterparties) or adding new financial instruments (equities, bonds, ISINs, futures) to trading systems. Global financial regulators (US Treasury OFAC, EU Commission, UN Security Council) require automated pre-trade and intra-day sanctions screening. Trading with sanctioned entities or instruments leads to asset freezing, massive regulatory fines, and criminal liability.

## Prerequisites

- Screening subject metadata (`ScreeningSubject`: `subject_id` [LEI/ISIN], `name`, `country_iso`, `entity_kind`, `ownership_pct_by_sanctioned`).
- Calibrated fuzzy match threshold (default 85.0%) and embargoed country list (`IR`, `KP`, `CU`, `SY`, `RU_CRIMEA`).

## Workflow

1. **OFAC 50% Rule Ownership Verification**:
   - Check if the entity is $\ge 50\%$ owned by one or more sanctioned entities (automatically block even if unlisted).
2. **Exact & Fuzzy Database Screening**:
   - Perform exact ID (LEI/ISIN) matching against OFAC SDN, EU, UN database.
   - Compute normalized Levenshtein similarity score between subject name and database entries. Flag hits $\ge 85\%$.
3. **Country & Jurisdiction Embargo Check**:
   - Verify subject country against embargoed jurisdiction list.
4. **Compliance Audit Report Generation**: Output structured `SanctionsScreeningReport`.

> Full procedure: see `references/workflows.md`.
> Standards reference: see `references/standards.md`.
> Printable pre-flight checklist: see `assets/checklist.md`.

## Common Pitfalls

- **Ignoring the OFAC 50% Rule**: Screening only named entities on the SDN list while trading with unlisted subsidiaries owned 50%+ by sanctioned entities.
- **Strict Exact Matching Only**: Missing sanctioned entries due to minor typos, transliteration variants, or abbreviation differences (e.g., "VTB Bank PJSC" vs "VTB Bank P.J.S.C.").
- **Uncalibrated Fuzzy Thresholds**: Setting fuzzy thresholds too low ($< 70\%$), flooding compliance teams with false positives.

## Verification

- Instantiate `SanctionsScreeningForCounterpartiesAndInstrumentsEngine`. Screen cleared entity ("APPLE INC") $\implies$ verify `is_cleared=True`. Screen "VTB BANK PJSC" $\implies$ verify fuzzy hit against OFAC SDN list and `BLOCKED_SANCTIONS_HIT` status. Screen entity with 51% sanctioned ownership $\implies$ verify OFAC 50% Rule hit. Screen North Korean entity $\implies$ verify `BLOCKED_EMBARGO` status.
- Run `python scripts/test_sanctions_screening_for_counterparties_and_instruments.py`.

## Related Skills

- `risk-reporting-for-external-stakeholders`
- `risk-control-bypass-audit-logging`
---
