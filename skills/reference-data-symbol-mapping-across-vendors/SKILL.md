---
name: reference-data-symbol-mapping-across-vendors
description: >-
  Cross-vendor symbol mapping engine resolving instrument identifier discrepancies (ticker, RIC, ISIN, CUSIP, SEDOL, Bloomberg ID) across data vendors into a unified canonical symbol.
domain: Data Management Global
subdomain: Reference Data & Symbol Normalization
tags: ["symbol-mapping", "reference-data", "cross-vendor", "isin", "cusip", "sedol", "bloomberg", "reuters-ric"]
brokers_frameworks: ["ISO 6166 ISIN", "CUSIP Global Services", "SEDOL Masterfile", "Bloomberg FIGI", "Python Dataclasses"]
version: "1.0.0"
author: algo-trading-skills-contributors
license: Apache-2.0
---

## When to Use

Use this skill when aggregating market data or reference data from multiple vendors (Bloomberg, Refinitiv, Interactive Brokers, exchange direct feeds) that use different symbology systems. Apple Inc. may be "AAPL" on one vendor, "AAPL.O" as a Reuters RIC, "US0378331005" as an ISIN, and "037833100" as a CUSIP. This engine maintains a cross-reference mapping table and resolves any vendor-specific identifier to a single canonical internal symbol, enabling consistent data joins, position aggregation, and order routing.

## Prerequisites

- Cross-reference mapping entries (`canonical_symbol`, `vendor_name`, `vendor_symbol`, `identifier_type`).
- Config options (`case_sensitive`: default False, `allow_ambiguous`: default False).

## Workflow

1. **Mapping Table Construction**:
   - Register vendor-specific symbol entries mapping to canonical internal symbols.
2. **Forward Lookup (Vendor → Canonical)**:
   - Given a vendor name and vendor symbol, resolve to canonical symbol.
3. **Reverse Lookup (Canonical → Vendor)**:
   - Given a canonical symbol and target vendor, resolve to vendor-specific identifier.
4. **Ambiguity Detection**:
   - Flag vendor symbols that map to multiple canonical symbols (conflict).
5. **Coverage Report**: Output structured `SymbolMappingCoverageReport`.

> Full procedure: see `references/workflows.md`.
> Standards reference: see `references/standards.md`.
> Printable pre-flight checklist: see `assets/checklist.md`.

## Common Pitfalls

- **Case Sensitivity Mismatches**: "aapl" vs "AAPL" causing lookup failures.
- **Stale Mappings After Symbol Changes**: Not updating cross-reference table after ticker renames (e.g. FB → META).
- **Ambiguous One-to-Many Mappings**: Same vendor symbol mapping to different canonical symbols across exchanges.

## Verification

- Instantiate `SymbolMappingEngine`. Register AAPL mappings for Bloomberg ("AAPL US Equity") and Reuters ("AAPL.O"). Forward lookup "AAPL.O" from Reuters $\implies$ verify resolves to canonical "AAPL". Reverse lookup canonical "AAPL" for Bloomberg $\implies$ verify returns "AAPL US Equity".
- Run `python scripts/test_reference_data_symbol_mapping_across_vendors.py`.

## Related Skills

- `isin-cusip-sedol-cross-reference-service`
- `reference-data-golden-source-designation`
---
