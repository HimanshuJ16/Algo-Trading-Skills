---
name: isin-cusip-sedol-cross-reference-service
description: >-
  Security master cross-reference service implementing Modulo 10 Luhn checksum algorithms for ISIN, CUSIP, and SEDOL identifiers, resolving multi-vendor mappings to immutable OpenFIGI keys.
domain: Data Management Global
subdomain: Security Master & Symbology Resolution
tags: ["isin", "cusip", "sedol", "figi", "security-master", "checksum-validation", "symbology-resolution"]
brokers_frameworks: ["OpenFIGI API", "CUSIP Global Services", "LSE SEDOL", "Python Dataclasses"]
version: "1.0.0"
author: algo-trading-skills-contributors
license: Apache-2.0
---

## When to Use

Use this skill when building global Security Master systems, ingesting multi-vendor data feeds (Bloomberg, Refinitiv, FactSet), and validating order entry symbols. Global securities use disparate regional identifiers: **ISIN** (12-char global), **CUSIP** (9-char North America), **SEDOL** (7-char UK/Ireland), and **FIGI** (12-char OpenFIGI). Ingesting invalid or corrupted identifiers creates execution rejections and database join corruption. This module validates Modulo 10 Luhn checksums for ISIN, CUSIP, and SEDOL, cross-referencing identifiers into unified Security Master records.

## Prerequisites

- Identifier input string (`isin`, `cusip`, `sedol`, `figi`, or `ticker`).
- Security master database registry table.

## Workflow

1. **Identifier Format & Checksum Audit**:
   - **ISIN**: Verify 12-char format (`US0378331005`) and Modulo 10 Luhn checksum.
   - **CUSIP**: Verify 9-char format (`037833100`) and Modulo 10 Double-Add-Double checksum.
   - **SEDOL**: Verify 7-char format (`2046251`) and weighted Modulo 10 checksum ($1, 3, 1, 7, 3, 9, 1$).
2. **Cross-Reference Security Master Lookup**:
   - Resolve any valid input identifier to its canonical Security Master record containing (`ISIN`, `CUSIP`, `SEDOL`, `FIGI`, `ticker`, `asset_name`).
3. **Audit Report Generation**: Output structured `IdentifierCrossReferenceReport`.

> Full procedure: see `references/workflows.md`.
> Standards reference: see `references/standards.md`.
> Printable pre-flight checklist: see `assets/checklist.md`.

## Common Pitfalls

- **Skipping Checksum Validation**: Ingesting typo-corrupted ISIN/CUSIP strings without validating Modulo 10 check digits, corrupting downstream risk tables.
- **Assuming US ISIN Equals CUSIP**: Forgetting that a US ISIN is constructed by prefixing `US` and appending a new Modulo 10 check digit to the 9-digit CUSIP (`US` + CUSIP + CheckDigit).
- **Hardcoding Ticker Mappings Without FIGI**: Mapping securities purely on ticker symbols instead of immutable FIGI keys, breaking data lineage during corporate rebrands or listing changes.

## Verification

- Instantiate `IsinCusipSedolCrossReferenceEngine`. Validate Apple Inc ISIN (`US0378331005`), CUSIP (`037833100`), SEDOL (`2046251`) $\implies$ verify 100% checksum pass and cross-reference mapping to FIGI `BBG000B9XRY4`. Audit Invalid ISIN (`US0378331009`) $\implies$ verify `INVALID_ISIN_CHECKSUM` rejection.
- Run `python scripts/test_isin_cusip_sedol_cross_reference_service.py`.

## Related Skills

- `reference-data-symbol-mapping-across-vendors`
- `instrument-universe-change-detection-and-alerting`
---
