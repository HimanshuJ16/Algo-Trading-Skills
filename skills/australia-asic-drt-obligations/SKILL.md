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
version: "1.1.0"
author: System
license: MIT
---

## When to Use

Use this skill when your quantitative trading desk executes Over-the-Counter (OTC) derivatives (e.g., Interest Rate Swaps, FX Forwards, Credit Default Swaps) in the Australian jurisdiction. The **ASIC Derivative Transaction Rules (Reporting) 2024** mandate that all reporting entities submit detailed transaction reports to a licensed Trade Repository (TR) within a strict **T+2** window.

This engine validates that a given trade contains the three mandatory ISO standard identifiers required by ASIC before the trade is serialized and transmitted to the trade repository.

## Prerequisites

- Python 3.9+
- The trading system must generate or ingest:
  - **LEI (ISO 17442)**: Legal Entity Identifier.
  - **UTI (ISO 23897)**: Unique Transaction Identifier.
  - **UPI (ISO 4914)**: Unique Product Identifier.

## Workflow

1. **Trade Capture**: An OTC derivative trade is executed and booked in the firm's Order Management System (OMS).
2. **Data Enrichment**: The firm's middle-office systems attach the counterparty LEI, generate the UTI, and fetch the UPI from the Derivatives Service Bureau (DSB).
3. **ASIC Validation**: The trade record is passed to `AsicDrtReportingEngine.validate_report()`.
4. **Rejection/Approval**: 
   - If any mandatory identifier is missing, the engine flags a critical compliance error, preventing the submission of an invalid XML message to the repository.
   - The engine also warns if the submission is attempting to be made outside the T+2 reporting window.
5. **Submission**: Compliant trades are forwarded to the XML generation pipeline.

## Common Pitfalls

- **Missing UPIs**: Assuming that a proprietary internal product code is sufficient. ASIC explicitly requires the ISO 4914 UPI for the 2024 rewrite.
- **T+1 vs T+2 Confusion**: Historically, reporting was T+1. The 2024 rules relaxed this to T+2, but submitting on T+3 is a direct regulatory breach resulting in fines.

## Verification

Run `python scripts/test_australia_asic_drt_obligations.py` to confirm that trades missing identifiers or breaching the T+2 deadline are correctly flagged as non-compliant.

## Related Skills

- `mifid-ii-algo-trading-compliance-eu`
- `automated-tax-lot-reporting-pipeline`
