---
name: reference-data-golden-source-designation
description: >-
  Golden source designation engine for reference data governance, resolving multi-vendor field conflicts by applying priority-ranked authoritative source rules per data field.
domain: Data Management Global
subdomain: Reference Data Governance & Conflict Resolution
tags: ["golden-source", "reference-data", "data-governance", "conflict-resolution", "multi-vendor", "authoritative-source"]
brokers_frameworks: ["ISO 10383 MIC Codes", "ISIN/CUSIP/SEDOL Standards", "Python Dataclasses"]
version: "1.0.0"
author: algo-trading-skills-contributors
license: Apache-2.0
---

## When to Use

Use this skill when ingesting instrument reference data from multiple vendors (Bloomberg, Refinitiv, exchange direct feeds) that may report conflicting values for the same field. A golden source designation establishes which vendor is the authoritative source for each specific data field (e.g., exchange feed is golden for tick_size, Bloomberg is golden for ISIN). This engine resolves multi-vendor conflicts by applying priority-ranked source rules, producing a single reconciled golden record per instrument.

## Prerequisites

- Multi-vendor instrument data (`instrument_id`, `vendor_name`, field-value pairs).
- Golden source priority rules (`field_name` → ordered list of vendor priorities).

## Workflow

1. **Multi-Vendor Data Ingestion**:
   - Collect field values from all vendors for each instrument.
2. **Golden Source Priority Resolution**:
   - For each field, select value from highest-priority vendor that provides a non-null value.
3. **Conflict Detection & Logging**:
   - Flag fields where vendors disagree on values, noting which vendor was selected as golden.
4. **Golden Record Output**: Output structured `GoldenRecordReport`.

> Full procedure: see `references/workflows.md`.
> Standards reference: see `references/standards.md`.
> Printable pre-flight checklist: see `assets/checklist.md`.

## Common Pitfalls

- **No Priority Rules Defined**: Using arbitrary or random vendor selection when values conflict.
- **Stale Golden Source**: Not updating priority rules when a vendor's data quality degrades.
- **Ignoring Null Values**: Selecting a higher-priority vendor's null value over a lower-priority vendor's valid value.

## Verification

- Instantiate `GoldenSourceDesignationEngine`. Ingest AAPL data from Bloomberg (ISIN="US0378331005") and Refinitiv (ISIN="US0378331005_OLD") with Bloomberg as golden for ISIN $\implies$ verify Bloomberg's value selected. Ingest field where only secondary vendor has data $\implies$ verify fallback to secondary.
- Run `python scripts/test_reference_data_golden_source_designation.py`.

## Related Skills

- `reference-data-symbol-mapping-across-vendors`
- `reference-data-change-notification-pipeline`
---
