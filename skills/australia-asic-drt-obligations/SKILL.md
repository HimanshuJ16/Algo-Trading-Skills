---
name: australia-asic-drt-obligations
description: Compliance validation engine enforcing ASIC Derivative Transaction Rules
  (DRT) 2024, ensuring mandatory LEI, UTI, and UPI fields for OTC derivatives reporting.
domain: regulatory-compliance-global
subdomain: regulatory
tags:
- compliance
- asic
- australia
- otc-derivatives
- trade-reporting
- lei
- uti
- upi
brokers_frameworks:
- generic
version: "1.2.0"
author: System
license: MIT
---

## When to Use

Use this skill when your quantitative trading desk executes Over-the-Counter (OTC) derivatives (e.g., Interest Rate Swaps, FX Forwards, Credit Default Swaps) in the Australian jurisdiction. The **ASIC Derivative Transaction Rules (Reporting) 2024** mandate that all reporting entities submit detailed transaction reports to a licensed Trade Repository (TR) within a strict **T+2** window.

This engine validates that a given trade contains the three mandatory ISO standard identifiers required by ASIC before the trade is serialized and transmitted to the trade repository.

## Prerequisites

- Python 3.9+
- The trading system must generate or ingest:
  - **LEI (ISO 17442)**: Legal Entity Identifier — 20 uppercase alphanumeric characters with a valid ISO/IEC 7064 MOD 97-10 checksum.
  - **UTI (ISO 23897)**: Unique Transaction Identifier — 20-52 uppercase alphanumeric characters (the first 20 are the generating entity's LEI).
  - **UPI (ISO 4914)**: Unique Product Identifier — 12 characters with the fixed "QZ" prefix.
- A Sydney public-holiday calendar is recommended for accurate business-day deadline computation. When omitted, `validate_report()` excludes weekends only.

## Workflow

1. **Trade Capture**: An OTC derivative trade is executed and booked in the firm's Order Management System (OMS).
2. **Data Enrichment**: The firm's middle-office systems attach the counterparty LEI, generate the UTI, and fetch the UPI from the Derivatives Service Bureau (DSB).
3. **ASIC Validation**: The trade record is passed to `AsicDrtReportingEngine.validate_report()`, optionally with a Sydney public-holiday set so the T+2/T+4 deadline is computed in business days (Rule 2.2.3).
4. **Rejection/Approval**:
   - If any mandatory identifier is missing or structurally invalid (LEI length/checksum, UTI length/charset, UPI prefix/charset), the engine flags a critical compliance error, preventing the submission of an invalid XML message to the repository.
   - The engine also warns if the submission is attempting to be made after the computed reporting deadline. Set `requires_linking_identifier=True` to apply the T+4 extension for trades requiring an Item 92 linking identifier.
5. **Submission**: Compliant trades are forwarded to the XML generation pipeline.

## Common Pitfalls

- **Missing UPIs**: Assuming that a proprietary internal product code is sufficient. ASIC explicitly requires the ISO 4914 UPI for the 2024 rewrite.
- **T+1 vs T+2 Confusion**: Historically, reporting was T+1. The 2024 rules relaxed this to T+2, but submitting on T+3 is a direct regulatory breach resulting in fines.
- **Calendar days vs business days**: T+2/T+4 are counted in **business days** (Sydney time, excluding weekends and public holidays). Using `timedelta(days=2)` on calendar days will mis-flag Friday trades and ignore holiday closures. Pass a holiday set to `validate_report()`.
- **LEI checksum**: An LEI that is 20 uppercase alphanumeric characters is not necessarily valid — ISO 17442 requires the MOD 97-10 check digits to satisfy `numeric % 97 == 1`. The engine rejects structurally invalid LEIs.
- **UTI length**: ISO 23897 UTIs are 20-52 characters. Accepting a short UTI (the legacy minimum was far lower) lets structurally invalid identifiers through to the trade repository.
- **T+4 linking-identifier extension**: Forgetting that trades requiring an Item 92 linking identifier get T+4 (not T+2) causes false late-submission flags.

## Verification

Run `python scripts/test_australia_asic_drt_obligations.py` to confirm that trades missing identifiers or breaching the T+2 deadline are correctly flagged as non-compliant.

## Related Skills

- `mifid-ii-algo-trading-compliance-eu`
- `automated-tax-lot-reporting-pipeline`
